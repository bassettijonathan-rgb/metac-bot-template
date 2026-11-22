"""
End-to-end check of bot.py against the REAL template (main.py) and the REAL
forecasting-tools library. Only the two things that would cost money or touch
the network are replaced: the model call (GeneralLlm.invoke) and the answer
parser (structure_output). Nothing is submitted to Metaculus.

This catches what the stand-in tests in tests/ cannot: a renamed method, a
changed constructor argument or a prediction type the library no longer
accepts, for example after the template or the library is upgraded.

It lives in its own folder because tests/ swaps the library for stand-ins and
the two must not share one Python process.

    uv run python -m unittest discover -s tests_real_library
"""
import asyncio
import contextlib
import io
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# Placeholder values: no request is ever sent with them.
os.environ["METACULUS_TOKEN"] = "not-a-real-token"
os.environ["OPENROUTER_API_KEY"] = "not-a-real-key"
for _name in ("ASKNEWS_CLIENT_ID", "ASKNEWS_SECRET", "ASKNEWS_API_KEY"):
    os.environ.pop(_name, None)

try:
    import forecasting_tools as ft
except ImportError:  # the plain unit-test job has no third-party packages
    ft = None

if ft is not None:
    import bot
    import config
    import main as template

CALLS: list[tuple[str, str]] = []  # (model, prompt) of every stand-in model call
CALL_TASKS: list[object] = []  # the asyncio task each of those calls ran in


async def _fake_invoke(self, prompt, system_prompt=None):
    # Models answer after different delays, as real ones do, so parallel
    # forecasts overtake each other.
    for _ in range(1 + sum(map(ord, self.model)) % 5):
        await asyncio.sleep(0)
    CALLS.append((self.model, str(prompt)))
    CALL_TASKS.append(asyncio.current_task())
    return f"Step by step reasoning from {self.model}.\nProbability: 30%"


async def _fake_structure_output(text_to_structure, output_type, **kwargs):
    if output_type is ft.BinaryPrediction:
        return ft.BinaryPrediction(prediction_in_decimal=0.3)
    if output_type is ft.PredictedOptionList:
        return ft.PredictedOptionList(
            predicted_options=[
                ft.PredictedOption(option_name="Red", probability=0.5),
                ft.PredictedOption(option_name="Green", probability=0.3),
                ft.PredictedOption(option_name="Blue", probability=0.2),
            ]
        )
    levels = [0.05, 0.1, 0.2, 0.4, 0.5, 0.6, 0.8, 0.9, 0.95]
    if output_type == list[ft.Percentile]:
        return [ft.Percentile(percentile=p, value=10 + 80 * p) for p in levels]
    if output_type == list[ft.DatePercentile]:
        start = datetime(2027, 1, 1, tzinfo=timezone.utc)
        return [
            ft.DatePercentile(percentile=p, value=start + timedelta(days=300 * p))
            for p in levels
        ]
    raise AssertionError(f"Unexpected output type {output_type}")


def _common(question_id: int) -> dict:
    return dict(
        id_of_post=question_id,
        id_of_question=question_id,
        page_url=f"https://example.org/questions/{question_id}",
        background_info="Background.",
        resolution_criteria="Resolves according to the official source.",
        fine_print="",
        close_time=datetime(2027, 3, 1, tzinfo=timezone.utc),
        scheduled_resolution_time=datetime(2027, 3, 2, tzinfo=timezone.utc),
    )


def _questions() -> list:
    return [
        ft.BinaryQuestion(question_text="Will it happen?", **_common(1)),
        ft.MultipleChoiceQuestion(
            question_text="Which colour?", options=["Red", "Green", "Blue"], **_common(2)
        ),
        ft.NumericQuestion(
            question_text="How many?",
            upper_bound=100.0,
            lower_bound=0.0,
            open_upper_bound=True,
            open_lower_bound=False,
            unit_of_measure="units",
            **_common(3),
        ),
        ft.DateQuestion(
            question_text="When?",
            upper_bound=datetime(2028, 1, 1, tzinfo=timezone.utc),
            lower_bound=datetime(2026, 12, 1, tzinfo=timezone.utc),
            open_upper_bound=True,
            open_lower_bound=False,
            **_common(4),
        ),
    ]


def _discrete_question():
    # Whole-number outcomes 0..10: Metaculus wants a short curve, not 201 points.
    return ft.DiscreteQuestion(
        question_text="How many of the ten?",
        upper_bound=10.5,
        lower_bound=-0.5,
        open_upper_bound=False,
        open_lower_bound=False,
        cdf_size=12,
        unit_of_measure="items",
        **_common(5),
    )


def _conditional_question():
    def sub(question_id, text, conditional_type=None):
        return ft.BinaryQuestion(
            question_text=text, conditional_type=conditional_type, **_common(question_id)
        )

    return ft.ConditionalQuestion(
        question_text="Conditional pair",
        parent=sub(11, "Will the parent event happen?"),
        child=sub(12, "Will the child event happen?"),
        question_yes=sub(13, "Child event, if the parent resolves yes?", "yes"),
        question_no=sub(14, "Child event, if the parent resolves no?", "no"),
        **_common(10),
    )


@unittest.skipIf(ft is None, "forecasting-tools is not installed")
class RealLibraryTests(unittest.TestCase):
    def setUp(self):
        CALLS.clear()
        CALL_TASKS.clear()

    def _run(self, profile_name: str):
        ensemble_bot = bot.EnsembleBot(
            profile=config.PROFILES[profile_name],
            publish_reports_to_metaculus=False,
            folder_to_save_reports_to=None,
            skip_previously_forecasted_questions=True,
            extra_metadata_in_explanation=True,
        )
        with mock.patch.object(ft.GeneralLlm, "invoke", _fake_invoke), mock.patch.object(
            template, "structure_output", _fake_structure_output
        ):
            reports = asyncio.run(
                ensemble_bot.forecast_questions(_questions(), return_exceptions=True)
            )
        return ensemble_bot, reports

    def test_every_profile_builds_with_the_real_library(self):
        for name, profile in config.PROFILES.items():
            ensemble_bot = bot.EnsembleBot(
                profile=profile, publish_reports_to_metaculus=False
            )
            self.assertEqual(
                ensemble_bot.predictions_per_research_report, len(profile.ensemble), name
            )
            search = ensemble_bot.get_llm("researcher", "llm")
            self.assertEqual(
                search.litellm_kwargs["extra_body"],
                {"plugins": [{"id": "web", "engine": "native"}]},
            )

    def test_all_question_types_run_end_to_end(self):
        ensemble_bot, reports = self._run("standard")
        failures = [r for r in reports if isinstance(r, BaseException)]
        self.assertEqual(failures, [], f"Failed questions: {failures}")
        self.assertEqual(len(reports), 4)
        for report in reports:
            self.assertEqual(report.errors, [], report.question.question_text)

        binary, multiple_choice, numeric, date = reports
        self.assertAlmostEqual(binary.prediction, 0.3, places=6)
        self.assertEqual(
            [o.option_name for o in multiple_choice.prediction.predicted_options],
            ["Red", "Green", "Blue"],
        )
        for report in (numeric, date):
            # The 201-point curve that would be submitted to Metaculus.
            cdf = [point.percentile for point in report.prediction.cdf]
            self.assertEqual(len(cdf), report.question.cdf_size)
            self.assertEqual(cdf, sorted(cdf))
            self.assertGreaterEqual(cdf[0], 0)
            self.assertLessEqual(cdf[-1], 1)

    def test_each_question_is_answered_by_every_ensemble_member(self):
        ensemble_bot, reports = self._run("standard")
        profile = config.PROFILES["standard"]
        for question in _questions():
            answered_by = sorted(
                model
                for model, prompt in CALLS
                if question.question_text in prompt
                and "professional forecaster with a strong track record" in prompt
            )
            self.assertEqual(
                answered_by,
                sorted(member.model for member in profile.ensemble),
                question.question_text,
            )
        explanation = reports[0].explanation
        for framing in {member.framing for member in profile.ensemble}:
            self.assertIn(framing, explanation)

    def test_discrete_question_gets_a_curve_of_the_size_it_asks_for(self):
        ensemble_bot = bot.EnsembleBot(
            profile=config.PROFILES["standard"], publish_reports_to_metaculus=False
        )

        async def structure(text_to_structure, output_type, **kwargs):
            levels = [0.05, 0.1, 0.2, 0.4, 0.5, 0.6, 0.8, 0.9, 0.95]
            return [ft.Percentile(percentile=p, value=1 + 8 * p) for p in levels]

        with mock.patch.object(ft.GeneralLlm, "invoke", _fake_invoke), mock.patch.object(
            template, "structure_output", structure
        ):
            reports = asyncio.run(
                ensemble_bot.forecast_questions([_discrete_question()], return_exceptions=True)
            )
        self.assertNotIsInstance(reports[0], BaseException, reports[0])
        self.assertEqual(reports[0].errors, [])
        cdf = [point.percentile for point in reports[0].prediction.get_cdf()]
        self.assertEqual(len(cdf), 12)
        self.assertEqual(cdf, sorted(cdf))
        self.assertAlmostEqual(cdf[0], 0.0)
        self.assertAlmostEqual(cdf[-1], 1.0)

    def test_conditional_question_keeps_one_member_per_forecast(self):
        profile = config.PROFILES["standard"]
        ensemble_bot = bot.EnsembleBot(profile=profile, publish_reports_to_metaculus=False)
        question = _conditional_question()
        with mock.patch.object(ft.GeneralLlm, "invoke", _fake_invoke), mock.patch.object(
            template, "structure_output", _fake_structure_output
        ):
            reports = asyncio.run(
                ensemble_bot.forecast_questions([question], return_exceptions=True)
            )
        self.assertNotIsInstance(reports[0], BaseException, reports[0])
        self.assertEqual(reports[0].errors, [])
        prediction = reports[0].prediction
        self.assertAlmostEqual(prediction.prediction_yes, 0.3, places=6)
        self.assertAlmostEqual(prediction.prediction_no, 0.3, places=6)

        # Every part of the conditional is answered once by each ensemble
        # member, and a member uses the same reasoning order for all four parts.
        expected_models = sorted(member.model for member in profile.ensemble)
        marker = {
            config.OUTSIDE_VIEW: "Outside view first",
            config.INSIDE_VIEW: "Causal path first",
            config.SKEPTIC: "like a contract",
        }
        expected_pairs = sorted((m.model, m.framing) for m in profile.ensemble)
        for part in (question.parent, question.child, question.question_yes, question.question_no):
            calls = [
                (model, prompt)
                for model, prompt in CALLS
                if f"The question:\n{part.question_text}" in prompt
            ]
            self.assertEqual(sorted(model for model, _ in calls), expected_models)
            pairs = sorted(
                (model, [k for k, text in marker.items() if text in prompt][0])
                for model, prompt in calls
            )
            self.assertEqual(pairs, expected_pairs, part.question_text)

        # Each of the five parallel forecasts is one asyncio task: inside it,
        # all four parts must come from the same model.
        models_per_task: dict[object, set[str]] = {}
        for (model, prompt), task in zip(CALLS, CALL_TASKS):
            if "professional forecaster with a strong track record" in prompt:
                models_per_task.setdefault(task, set()).add(model)
        self.assertEqual(len(models_per_task), len(profile.ensemble))
        for models in models_per_task.values():
            self.assertEqual(len(models), 1, models)

    def test_research_runs_every_pass_with_the_search_model(self):
        self._run("standard")
        profile = config.PROFILES["standard"]
        research_calls = [
            (model, prompt)
            for model, prompt in CALLS
            if "research assistant of a professional forecaster" in prompt
        ]
        self.assertEqual(len(research_calls), 4 * len(profile.research_passes))
        self.assertEqual({model for model, _ in research_calls}, {profile.search_model})

    # ------------------------------------------------------------ entry point

    def _run_main(self, *arguments, questions_by_tournament):
        """Runs bot.main() with Metaculus and the models replaced. Returns what
        would have been sent: (tournament ids asked for, list of (url, json))."""
        import forecasting_tools.helpers.metaculus_client as client_module

        asked: list[object] = []
        posted: list[tuple[str, object]] = []

        def fake_open_questions(client, tournament_id, *args, **kwargs):
            asked.append(tournament_id)
            return questions_by_tournament.get(tournament_id, [])

        class Accepted:
            status_code = 200
            ok = True
            text = ""
            content = b"{}"

            def raise_for_status(self):
                return None

            def json(self):
                return {}

        def fake_post(url, json=None, **kwargs):
            posted.append((url, json))
            return Accepted()

        with mock.patch.object(sys, "argv", ["bot.py", *arguments]), mock.patch.object(
            bot.logging, "basicConfig"  # keep the test output short
        ), contextlib.redirect_stdout(io.StringIO()), mock.patch.object(
            bot, "openrouter_credits_remaining", return_value=25.0
        ), mock.patch.object(
            ft.MetaculusClient, "get_all_open_questions_from_tournament", fake_open_questions
        ), mock.patch.object(
            ft.MetaculusClient, "_sleep_between_requests", lambda self: None
        ), mock.patch.object(
            client_module.requests, "post", fake_post
        ), mock.patch.object(
            ft.GeneralLlm, "invoke", _fake_invoke
        ), mock.patch.object(
            template, "structure_output", _fake_structure_output
        ):
            bot.main()
        return asked, posted

    def test_main_dry_run_covers_season_and_minibench_and_submits_nothing(self):
        client = ft.MetaculusClient()
        asked, posted = self._run_main(
            "--mode",
            "tournament",
            "--dry-run",
            questions_by_tournament={client.CURRENT_AI_COMPETITION_ID: _questions()},
        )
        self.assertEqual(asked, [client.CURRENT_AI_COMPETITION_ID, client.CURRENT_MINIBENCH_ID])
        self.assertEqual(posted, [])
        # $25 of credit left: the budget guard picked the three-member line-up.
        forecasts = [c for c in CALLS if "strong track record" in c[1]]
        self.assertEqual(len(forecasts), 4 * len(config.PROFILES["lean"].ensemble))

    def test_main_test_mode_submits_valid_forecasts_and_private_comments(self):
        asked, posted = self._run_main(
            "--mode", "test_questions", questions_by_tournament={"bot-testing-area": _questions()}
        )
        self.assertEqual(asked, ["bot-testing-area"])
        forecasts = {
            entry["question"]: entry
            for url, payload in posted
            if url.endswith("/questions/forecast/")
            for entry in payload
        }
        comments = [payload for url, payload in posted if url.endswith("/comments/create/")]
        self.assertEqual(sorted(forecasts), [1, 2, 3, 4])
        self.assertEqual(len(comments), 4)

        self.assertAlmostEqual(forecasts[1]["probability_yes"], 0.3, places=6)
        self.assertAlmostEqual(sum(forecasts[2]["probability_yes_per_category"].values()), 1.0)
        for question_id in (3, 4):
            cdf = forecasts[question_id]["continuous_cdf"]
            self.assertEqual(len(cdf), 201)
            self.assertEqual(cdf, sorted(cdf))
        for comment in comments:
            self.assertTrue(comment["is_private"])
            self.assertIn("Ensemble member", comment["text"])

    def test_main_limit_forecasts_only_that_many_test_questions(self):
        asked, posted = self._run_main(
            "--mode",
            "test_questions",
            "--limit",
            "2",
            questions_by_tournament={"bot-testing-area": _questions()},
        )
        self.assertEqual(asked, ["bot-testing-area"])
        forecasts = [
            entry
            for url, payload in posted
            if url.endswith("/questions/forecast/")
            for entry in payload
        ]
        self.assertEqual(len(forecasts), 2)

    def test_limit_is_refused_in_tournament_mode(self):
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            self._run_main("--mode", "tournament", "--limit", "2", questions_by_tournament={})

    def test_real_model_call_path_records_what_the_provider_charged(self):
        import litellm
        from forecasting_tools.ai_models import general_llm as general_llm_module

        requests_sent: list[dict] = []

        async def fake_acompletion(**kwargs):
            requests_sent.append(kwargs)
            return litellm.ModelResponse(
                choices=[{"message": {"role": "assistant", "content": "findings"}}],
                usage={
                    "prompt_tokens": 9000,
                    "completion_tokens": 700,
                    "total_tokens": 9700,
                    "cost": 0.0421,
                    "server_tool_use": {"web_search_requests": 3},
                },
            )

        ensemble_bot = bot.EnsembleBot(
            profile=config.PROFILES["minimal"], publish_reports_to_metaculus=False
        )
        search = ensemble_bot.get_llm("researcher", "llm")
        with mock.patch.object(general_llm_module, "acompletion", fake_acompletion):
            answer = asyncio.run(search.invoke("What happened?"))

        self.assertEqual(answer, "findings")
        self.assertNotIn("stage", requests_sent[0])
        self.assertNotIn("ledger", requests_sent[0])
        (call,) = ensemble_bot.ledger.calls
        self.assertEqual(call.stage, bot.SEARCH)
        self.assertEqual(call.model, config.PROFILES["minimal"].search_model)
        self.assertEqual(
            (call.prompt_tokens, call.completion_tokens, call.web_searches), (9000, 700, 3)
        )
        self.assertAlmostEqual(call.cost_usd, 0.0421)

    def test_tournament_ids_exist_in_the_library(self):
        client = ft.MetaculusClient()
        self.assertTrue(client.CURRENT_AI_COMPETITION_ID)
        self.assertTrue(client.CURRENT_MINIBENCH_ID)


if __name__ == "__main__":
    unittest.main()
