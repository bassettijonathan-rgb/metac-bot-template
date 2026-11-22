"""
Forecasting bot for the Metaculus FutureEval Bot Tournament.

Built on the official template (main.py, kept unmodified so upstream fixes
merge cleanly). What this file changes:

1. Research: several independent passes run in parallel (current status,
   base rates and outside forecasts, plus AskNews when keys are set) instead
   of one generic summary.
2. Ensemble: each forecast for a question comes from a different model and a
   different reasoning order (outside view first, inside view first, skeptic),
   so the members do not share the same blind spot.
3. Aggregation: binary forecasts are combined with a trimmed mean in log-odds
   and clipped to [2%, 98%]. No extremization.
4. Numeric and date questions ask for explicit 5th and 95th percentiles so the
   tails are stated rather than extrapolated.
5. Budget guard: the bot reads the remaining OpenRouter credit at start-up and
   steps down to cheaper models before the money runs out.
6. Cost report: every model call is recorded with what OpenRouter charged for
   it, and the run ends with a table of cost per stage (see costs.py).

No human is in the loop: forecasts and comments are produced and submitted by
this program only, as the tournament rules require.

Run:
    uv run python bot.py --mode test_questions     # unscored bot-testing-area
    uv run python bot.py --mode tournament         # live season + MiniBench
    uv run python bot.py --mode test_questions --dry-run   # do not submit
    uv run python bot.py --mode test_questions --limit 2   # only 2 questions
"""
from __future__ import annotations

import argparse
import asyncio
import contextvars
import logging
import os
from datetime import datetime, timezone
from typing import Literal

import requests

# The template module. Importing it also loads .env and quiets noisy loggers.
from main import FallTemplateBot2026

from bot_helpers import (
    check_environment,
    print_run_summary_banner,
    print_startup_banner,
)
from forecasting_tools import (
    AskNewsSearcher,
    BinaryQuestion,
    DateQuestion,
    GeneralLlm,
    MetaculusClient,
    MetaculusQuestion,
    MultipleChoiceQuestion,
    NumericDistribution,
    NumericQuestion,
    PredictedOptionList,
    PredictionTypes,
    ReasonedPrediction,
)

import prompts
from aggregation import aggregate_binary, disagreement
from costs import FORECAST, HELPER, SEARCH, CostLedger, call_from_usage
from config import (
    OUTSIDE_VIEW,
    Member,
    Profile,
    choose_profile,
    requested_profile_from_env,
)

logger = logging.getLogger(__name__)

# The ensemble member that the current asyncio task is forecasting as.
# asyncio.gather gives each forecast its own task and therefore its own copy
# of this variable, so parallel forecasts do not overwrite each other.
_CURRENT_MEMBER: contextvars.ContextVar[Member | None] = contextvars.ContextVar(
    "current_member", default=None
)


def openrouter_credits_remaining() -> float | None:
    """Dollars left on the OpenRouter key, or None if unknown or unlimited."""
    key = os.getenv("OPENROUTER_API_KEY")
    if not key:
        return None
    try:
        response = requests.get(
            "https://openrouter.ai/api/v1/key",
            headers={"Authorization": f"Bearer {key}"},
            timeout=15,
        )
        response.raise_for_status()
        remaining = response.json().get("data", {}).get("limit_remaining")
        return float(remaining) if remaining is not None else None
    except Exception as error:  # never let a balance check stop a forecast run
        logger.warning(f"Could not read OpenRouter balance: {error.__class__.__name__}")
        return None


def _has_asknews_keys() -> bool:
    return bool(
        (os.getenv("ASKNEWS_CLIENT_ID") and os.getenv("ASKNEWS_SECRET"))
        or os.getenv("ASKNEWS_API_KEY")
    )


class TrackedLlm(GeneralLlm):
    """A GeneralLlm that writes what each call cost into the run's ledger."""

    def __init__(self, model: str, *, stage: str, ledger: CostLedger, **kwargs) -> None:
        super().__init__(model, **kwargs)
        self.stage = stage
        self.ledger = ledger

    async def _call_litellm(self, prompt):  # type: ignore[override]
        response = await super()._call_litellm(prompt)
        try:  # accounting must never stop a forecast
            usage = getattr(response, "usage", None)
            hidden = getattr(response, "_hidden_params", None) or {}
            self.ledger.record(
                call_from_usage(self.stage, self.model, usage, hidden.get("response_cost"))
            )
            if self.stage == SEARCH and usage is not None:
                dump = usage.model_dump() if hasattr(usage, "model_dump") else usage
                logger.info(f"Web search call usage ({self.model}): {dump}")
        except Exception as error:
            logger.warning(f"Could not record the cost of a call: {error.__class__.__name__}")
        return response


class EnsembleBot(FallTemplateBot2026):
    def __init__(self, *, profile: Profile, **kwargs) -> None:
        self.profile = profile
        self.ledger = CostLedger()
        self._member_llms: dict[str, GeneralLlm] = {}
        self._member_counter: dict[object, int] = {}
        helper = TrackedLlm(
            profile.helper_model, stage=HELPER, ledger=self.ledger, temperature=None
        )
        super().__init__(
            research_reports_per_question=1,
            predictions_per_research_report=len(profile.ensemble),
            use_research_summary_to_forecast=False,
            llms={
                "default": self._llm_for(profile.ensemble[0].model),
                "summarizer": helper,
                "researcher": self._search_llm(),
                "parser": helper,
            },
            **kwargs,
        )

    # ------------------------------------------------------------------ models

    def _llm_for(self, model: str) -> GeneralLlm:
        if model not in self._member_llms:
            # temperature=None keeps each provider's default, which several of
            # the newest models require.
            self._member_llms[model] = TrackedLlm(
                model,
                stage=FORECAST,
                ledger=self.ledger,
                temperature=None,
                timeout=240,
                allowed_tries=2,
            )
        return self._member_llms[model]

    def _search_llm(self) -> GeneralLlm:
        # OpenRouter web plugin with the provider's own search engine. The
        # donated tournament credits do not cover the Exa engine.
        return TrackedLlm(
            self.profile.search_model,
            stage=SEARCH,
            ledger=self.ledger,
            temperature=None,
            timeout=240,
            allowed_tries=2,
            extra_body={"plugins": [{"id": "web", "engine": "native"}]},
        )

    def get_llm(self, purpose: str = "default", guarantee_type=None):  # type: ignore[override]
        member = _CURRENT_MEMBER.get()
        if purpose == "default" and member is not None:
            llm = self._llm_for(member.model)
            return llm.model if guarantee_type == "string_name" else llm
        return super().get_llm(purpose, guarantee_type)

    async def _make_prediction(
        self, question: MetaculusQuestion, research: str
    ) -> ReasonedPrediction[PredictionTypes]:
        if _CURRENT_MEMBER.get() is not None:
            # A sub-question of a conditional question: the member already
            # chosen for the outer question answers it too.
            return await super()._make_prediction(question, research)

        # Read and bump the counter with no await in between, so two parallel
        # forecasts never get the same ensemble slot.
        key = question.id_of_question or question.id_of_post or id(question)
        slot = self._member_counter.get(key, 0)
        self._member_counter[key] = slot + 1
        member = self.profile.ensemble[slot % len(self.profile.ensemble)]

        token = _CURRENT_MEMBER.set(member)
        try:
            prediction = await super()._make_prediction(question, research)
        finally:
            _CURRENT_MEMBER.reset(token)

        label = f"[Ensemble member: {member.model.removeprefix('openrouter/')}, {member.framing}]"
        return ReasonedPrediction(
            prediction_value=prediction.prediction_value,
            reasoning=f"{label}\n\n{prediction.reasoning}",
        )

    def _framing(self) -> str:
        member = _CURRENT_MEMBER.get()
        return member.framing if member is not None else OUTSIDE_VIEW

    # ---------------------------------------------------------------- research

    async def run_research(self, question: MetaculusQuestion) -> str:
        async with self._concurrency_limiter:
            titles: list[str] = []
            tasks = []

            if _has_asknews_keys():
                titles.append("Recent news (AskNews)")
                tasks.append(
                    AskNewsSearcher().call_preconfigured_version(
                        "asknews/news-summaries", question.question_text
                    )
                )

            search_llm = self.get_llm("researcher", "llm")
            for pass_key in self.profile.research_passes:
                titles.append(prompts.research_section_title(pass_key))
                tasks.append(
                    search_llm.invoke(
                        prompts.research_prompt(pass_key, **self._research_fields(question))
                    )
                )

            results = await asyncio.gather(*tasks, return_exceptions=True)

            sections: list[str] = []
            for title, result in zip(titles, results):
                if isinstance(result, BaseException):
                    logger.warning(
                        f"Research pass '{title}' failed for {question.page_url}: "
                        f"{result.__class__.__name__}: {result}"
                    )
                    continue
                text = str(result).strip()
                if text:
                    sections.append(f"## {title}\n{text}")

            research = "\n\n".join(sections) if sections else prompts.NO_RESEARCH_NOTE
            logger.info(f"Research for URL {question.page_url}:\n{research}")
            return research

    @staticmethod
    def _today() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    @staticmethod
    def _resolution_date(question: MetaculusQuestion) -> str | None:
        moment = question.scheduled_resolution_time or question.close_time
        return moment.strftime("%Y-%m-%d") if moment else None

    def _research_fields(self, question: MetaculusQuestion) -> dict:
        return dict(
            question_text=question.question_text,
            resolution_criteria=question.resolution_criteria,
            fine_print=question.fine_print,
            background=question.background_info,
            today=self._today(),
            resolution_date=self._resolution_date(question),
        )

    def _forecast_fields(self, question: MetaculusQuestion, research: str) -> dict:
        return dict(
            question_text=question.question_text,
            background=question.background_info,
            resolution_criteria=question.resolution_criteria,
            fine_print=question.fine_print,
            research=research,
            today=self._today(),
            resolution_date=self._resolution_date(question),
            conditional_disclaimer=self._get_conditional_disclaimer_if_necessary(question),
        )

    # --------------------------------------------------------------- forecasts
    # Only the prompts change. Parsing the model's answer into a number is left
    # to the template's *_prompt_to_forecast methods.

    async def _run_forecast_on_binary(
        self, question: BinaryQuestion, research: str
    ) -> ReasonedPrediction[float]:
        prompt = prompts.binary_prompt(
            self._framing(), **self._forecast_fields(question, research)
        )
        return await self._binary_prompt_to_forecast(question, prompt)

    async def _run_forecast_on_multiple_choice(
        self, question: MultipleChoiceQuestion, research: str
    ) -> ReasonedPrediction[PredictedOptionList]:
        prompt = prompts.multiple_choice_prompt(
            self._framing(),
            options=question.options,
            **self._forecast_fields(question, research),
        )
        return await self._multiple_choice_prompt_to_forecast(question, prompt)

    async def _run_forecast_on_numeric(
        self, question: NumericQuestion, research: str
    ) -> ReasonedPrediction[NumericDistribution]:
        upper_bound_message, lower_bound_message = (
            self._create_upper_and_lower_bound_messages(question)
        )
        prompt = prompts.numeric_prompt(
            self._framing(),
            unit_of_measure=question.unit_of_measure,
            lower_bound_message=lower_bound_message,
            upper_bound_message=upper_bound_message,
            **self._forecast_fields(question, research),
        )
        return await self._numeric_prompt_to_forecast(question, prompt)

    async def _run_forecast_on_date(
        self, question: DateQuestion, research: str
    ) -> ReasonedPrediction[NumericDistribution]:
        upper_bound_message, lower_bound_message = (
            self._create_upper_and_lower_bound_messages(question)
        )
        prompt = prompts.date_prompt(
            self._framing(),
            lower_bound_message=lower_bound_message,
            upper_bound_message=upper_bound_message,
            **self._forecast_fields(question, research),
        )
        return await self._date_prompt_to_forecast(question, prompt)

    # ------------------------------------------------------------- aggregation

    async def _aggregate_predictions(
        self,
        predictions: list[PredictionTypes],
        question: MetaculusQuestion,
    ) -> PredictionTypes:
        all_numbers = bool(predictions) and all(
            isinstance(p, (int, float)) and not isinstance(p, bool) for p in predictions
        )
        if isinstance(question, BinaryQuestion) and all_numbers:
            values = [float(p) for p in predictions]  # type: ignore[arg-type]
            combined = aggregate_binary(values)
            logger.info(
                f"Combined binary forecast for {question.page_url}: {combined:.3f} "
                f"from {[round(v, 3) for v in values]} "
                f"(log-odds spread {disagreement(values):.2f})"
            )
            return combined
        return await super()._aggregate_predictions(predictions, question)


def pick_spread(questions: list, limit: int) -> list:
    """
    The first `limit` questions, taking one of each question type before a
    second of any type, so that a short test still covers different types.
    """
    by_type: dict[str, list] = {}
    for question in questions:
        by_type.setdefault(type(question).__name__, []).append(question)
    picked: list = []
    while len(picked) < limit and any(by_type.values()):
        for group in by_type.values():
            if group and len(picked) < limit:
                picked.append(group.pop(0))
    return picked


def cost_report(
    ledger: CostLedger,
    question_count: int,
    credits_before: float | None,
    credits_after: float | None,
) -> str:
    lines = [ledger.format_table(question_count)]
    if credits_before is not None and credits_after is not None:
        spent = credits_before - credits_after
        lines.append(
            f"OpenRouter credit: ${credits_before:.2f} before, ${credits_after:.2f} after, "
            f"${spent:.2f} spent. The balance can lag by a few minutes; when the two "
            "differ, the balance is the final word."
        )
    lines.append(
        "The 'Total cost estimated' line printed by the library above has been "
        "about twice the real charge. Use the figures here."
    )
    return "\n".join(lines)


RunMode = Literal["tournament", "test_questions"]

TOURNAMENT_URLS = {
    "tournament": "https://www.metaculus.com/tournaments/?search=futureeval",
    "test_questions": "https://www.metaculus.com/tournament/bot-testing-area/",
}


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    parser = argparse.ArgumentParser(description="Run the ensemble forecasting bot")
    parser.add_argument(
        "--mode",
        type=str,
        choices=["tournament", "test_questions"],
        default="tournament",
        help="What to forecast on (default: tournament)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Research and forecast but do not submit anything to Metaculus",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Test mode only: forecast at most this many questions (a cheap trial run)",
    )
    args = parser.parse_args()
    run_mode: RunMode = args.mode
    if args.limit is not None and (run_mode != "test_questions" or args.limit < 1):
        parser.error("--limit needs --mode test_questions and a number of 1 or more")
    publish = not args.dry_run

    check_environment(strict=True)
    print_startup_banner(run_mode, will_publish=publish)

    credits = openrouter_credits_remaining()
    profile = choose_profile(requested_profile_from_env(), credits)
    credits_text = "unknown" if credits is None else f"${credits:.2f}"
    print(
        f"Profile: {profile.name} "
        f"({len(profile.ensemble)} forecasters, {len(profile.research_passes)} search passes, "
        f"AskNews {'on' if _has_asknews_keys() else 'off'}). "
        f"OpenRouter credit left: {credits_text}\n"
    )

    bot = EnsembleBot(
        profile=profile,
        publish_reports_to_metaculus=publish,
        folder_to_save_reports_to=None,
        skip_previously_forecasted_questions=True,
        extra_metadata_in_explanation=True,
    )

    client = MetaculusClient()
    if run_mode == "tournament":
        tournament_ids = [client.CURRENT_AI_COMPETITION_ID, client.CURRENT_MINIBENCH_ID]
    else:
        # bot-testing-area holds one question of each type and is not scored.
        bot.skip_previously_forecasted_questions = False
        tournament_ids = ["bot-testing-area"]

    async def forecast_all() -> list:
        # One event loop for the whole run: the template's shared semaphore
        # must not be reused across separate asyncio.run() calls.
        reports: list = []
        for tournament_id in tournament_ids:
            if args.limit is None:
                reports += await bot.forecast_on_tournament(
                    tournament_id, return_exceptions=True
                )
                continue
            questions = client.get_all_open_questions_from_tournament(tournament_id)
            chosen = pick_spread(questions, args.limit)
            print(f"Limit {args.limit}: forecasting {len(chosen)} of {len(questions)} open questions\n")
            reports += await bot.forecast_questions(chosen, return_exceptions=True)
        return reports

    forecast_reports = asyncio.run(forecast_all())

    bot.log_report_summary(forecast_reports)
    print(
        "\n"
        + cost_report(
            bot.ledger, len(forecast_reports), credits, openrouter_credits_remaining()
        )
        + "\n"
    )
    print_run_summary_banner(
        forecast_reports,
        will_publish=publish,
        tournament_url=TOURNAMENT_URLS.get(run_mode),
    )


if __name__ == "__main__":
    main()
