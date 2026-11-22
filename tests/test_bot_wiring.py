"""
Wiring tests for bot.py with the template and the forecasting-tools library
replaced by small stand-ins. They check the logic this repo adds (which model
answers as which ensemble member, research failure handling, aggregation
routing) without any network call or API key. They do NOT prove that the real
library accepts our calls: that is what the "Test Bot" workflow is for.

    python -m unittest discover -s tests
"""
import asyncio
import os
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

CALLS: list[tuple[str, str]] = []  # (model, prompt) for every stand-in model call
FAIL_MODELS: set[str] = set()


class FakeLlm:
    def __init__(self, model, **kwargs):
        self.model = model
        self.kwargs = kwargs

    async def invoke(self, prompt):
        await asyncio.sleep(0)  # let other tasks run, like a real network call
        CALLS.append((self.model, prompt))
        if self.model in FAIL_MODELS or (
            self.kwargs.get("extra_body") and "search-fails" in FAIL_MODELS
        ):
            raise RuntimeError("stand-in model failure")
        return f"answer from {self.model}"


class FakeQuestion:
    def __init__(self, qid=1):
        self.id_of_question = qid
        self.id_of_post = qid
        self.question_text = "Will X happen?"
        self.background_info = "Some background"
        self.resolution_criteria = "Resolves Yes if X"
        self.fine_print = ""
        self.page_url = f"https://example.org/q/{qid}"
        self.scheduled_resolution_time = None
        self.close_time = None
        self.unit_of_measure = "units"
        self.options = ["A", "B"]


class FakeBinary(FakeQuestion):
    pass


class FakeMultipleChoice(FakeQuestion):
    pass


class FakeNumeric(FakeQuestion):
    pass


class FakeDate(FakeQuestion):
    pass


class FakeReasoned:
    def __init__(self, prediction_value, reasoning):
        self.prediction_value = prediction_value
        self.reasoning = reasoning


class FakeAskNews:
    async def call_preconfigured_version(self, preset, prompt):
        await asyncio.sleep(0)
        if "asknews-fails" in FAIL_MODELS:
            raise RuntimeError("asknews down")
        return "asknews headlines"


class FakeTemplateBot:
    """Stands in for main.FallTemplateBot2026 and its ForecastBot parent."""

    _concurrency_limiter = None

    def __init__(self, **kwargs):
        self.init_kwargs = kwargs
        self._llms = kwargs["llms"]
        self.skip_previously_forecasted_questions = kwargs.get(
            "skip_previously_forecasted_questions", False
        )
        FakeTemplateBot._concurrency_limiter = asyncio.Semaphore(1)

    def get_llm(self, purpose="default", guarantee_type=None):
        llm = self._llms[purpose]
        return llm.model if guarantee_type == "string_name" else llm

    async def _make_prediction(self, question, research):
        if isinstance(question, FakeBinary):
            return await self._run_forecast_on_binary(question, research)
        if isinstance(question, FakeMultipleChoice):
            return await self._run_forecast_on_multiple_choice(question, research)
        if isinstance(question, FakeNumeric):
            return await self._run_forecast_on_numeric(question, research)
        return await self._run_forecast_on_date(question, research)

    async def _answer(self, prompt, value):
        reasoning = await self.get_llm("default", "llm").invoke(prompt)
        return FakeReasoned(value, reasoning)

    async def _binary_prompt_to_forecast(self, question, prompt):
        return await self._answer(prompt, 0.3)

    async def _multiple_choice_prompt_to_forecast(self, question, prompt):
        return await self._answer(prompt, "options")

    async def _numeric_prompt_to_forecast(self, question, prompt):
        return await self._answer(prompt, "distribution")

    async def _date_prompt_to_forecast(self, question, prompt):
        return await self._answer(prompt, "dates")

    async def _aggregate_predictions(self, predictions, question):
        return "template-aggregate"

    def _get_conditional_disclaimer_if_necessary(self, question):
        return ""

    def _create_upper_and_lower_bound_messages(self, question):
        return "UPPER BOUND MESSAGE", "LOWER BOUND MESSAGE"


def _install_stand_ins():
    ft = types.ModuleType("forecasting_tools")
    ft.AskNewsSearcher = FakeAskNews
    ft.BinaryQuestion = FakeBinary
    ft.DateQuestion = FakeDate
    ft.GeneralLlm = FakeLlm
    ft.MetaculusClient = object
    ft.MetaculusQuestion = FakeQuestion
    ft.MultipleChoiceQuestion = FakeMultipleChoice
    ft.NumericDistribution = object
    ft.NumericQuestion = FakeNumeric
    ft.PredictedOptionList = object
    ft.PredictionTypes = object
    ft.ReasonedPrediction = FakeReasoned
    sys.modules["forecasting_tools"] = ft

    template = types.ModuleType("main")
    template.FallTemplateBot2026 = FakeTemplateBot
    sys.modules["main"] = template

    helpers = types.ModuleType("bot_helpers")
    helpers.check_environment = lambda strict=True: None
    helpers.print_run_summary_banner = lambda *a, **k: None
    helpers.print_startup_banner = lambda *a, **k: None
    sys.modules["bot_helpers"] = helpers


_install_stand_ins()

import bot  # noqa: E402
import config  # noqa: E402
import prompts  # noqa: E402


def make_bot(profile_name="standard"):
    return bot.EnsembleBot(
        profile=config.PROFILES[profile_name],
        publish_reports_to_metaculus=False,
        skip_previously_forecasted_questions=True,
    )


class WiringTests(unittest.TestCase):
    def setUp(self):
        CALLS.clear()
        FAIL_MODELS.clear()
        for name in ("ASKNEWS_CLIENT_ID", "ASKNEWS_SECRET", "ASKNEWS_API_KEY"):
            os.environ.pop(name, None)

    def test_constructor_passes_ensemble_size_and_all_llm_roles(self):
        b = make_bot("standard")
        kwargs = b.init_kwargs
        self.assertEqual(kwargs["research_reports_per_question"], 1)
        self.assertEqual(kwargs["predictions_per_research_report"], 5)
        self.assertEqual(
            set(kwargs["llms"]), {"default", "summarizer", "researcher", "parser"}
        )
        search = kwargs["llms"]["researcher"]
        self.assertEqual(
            search.kwargs["extra_body"],
            {
                "tools": [
                    {
                        "type": "openrouter:web_search",
                        "parameters": {"engine": "native", "max_uses": 5},
                    }
                ],
                "max_tool_calls": 5,
            },
        )
        self.assertEqual(len(make_bot("lean").profile.ensemble), 3)

    def test_parallel_forecasts_each_use_their_own_member(self):
        b = make_bot("standard")
        question = FakeBinary(qid=7)

        async def run():
            return await asyncio.gather(
                *[b._make_prediction(question, "RESEARCH TEXT") for _ in range(5)]
            )

        results = asyncio.run(run())
        ensemble = config.PROFILES["standard"].ensemble
        for member, result in zip(ensemble, results):
            short = member.model.removeprefix("openrouter/")
            self.assertTrue(
                result.reasoning.startswith(f"[Ensemble member: {short}, {member.framing}]"),
                result.reasoning[:80],
            )
            # The model that actually answered is the member's model.
            self.assertIn(f"answer from {member.model}", result.reasoning)
            self.assertEqual(result.prediction_value, 0.3)

        # Each call got the prompt of its own framing, on its own model.
        marker = {
            config.OUTSIDE_VIEW: "Outside view first",
            config.INSIDE_VIEW: "Causal path first",
            config.SKEPTIC: "like a contract",
        }
        self.assertEqual(len(CALLS), 5)
        used = sorted((model, [k for k, m in marker.items() if m in prompt][0]) for model, prompt in CALLS)
        expected = sorted((m.model, m.framing) for m in ensemble)
        self.assertEqual(used, expected)
        for _, prompt in CALLS:
            self.assertIn("RESEARCH TEXT", prompt)

    def test_member_counter_is_per_question(self):
        b = make_bot("lean")
        ensemble = config.PROFILES["lean"].ensemble

        async def run():
            first = await b._make_prediction(FakeBinary(qid=1), "r")
            other = await b._make_prediction(FakeBinary(qid=2), "r")
            second = await b._make_prediction(FakeBinary(qid=1), "r")
            return first, other, second

        first, other, second = asyncio.run(run())
        self.assertIn(ensemble[0].model, first.reasoning)
        self.assertIn(ensemble[0].model, other.reasoning)
        self.assertIn(ensemble[1].model, second.reasoning)

    def test_member_context_does_not_leak_after_a_forecast(self):
        b = make_bot("standard")
        asyncio.run(b._make_prediction(FakeBinary(qid=3), "r"))
        self.assertIsNone(bot._CURRENT_MEMBER.get())
        self.assertIs(b.get_llm("default"), b.init_kwargs["llms"]["default"])
        self.assertIs(b.get_llm("parser"), b.init_kwargs["llms"]["parser"])

    def test_member_context_is_reset_when_a_model_fails(self):
        b = make_bot("standard")
        FAIL_MODELS.add(config.PROFILES["standard"].ensemble[0].model)
        with self.assertRaises(RuntimeError):
            asyncio.run(b._make_prediction(FakeBinary(qid=4), "r"))
        self.assertIsNone(bot._CURRENT_MEMBER.get())

    def test_other_question_types_route_to_their_prompts(self):
        b = make_bot("standard")
        asyncio.run(b._make_prediction(FakeMultipleChoice(qid=10), "r"))
        asyncio.run(b._make_prediction(FakeNumeric(qid=11), "r"))
        asyncio.run(b._make_prediction(FakeDate(qid=12), "r"))
        mc, numeric, date = (prompt for _, prompt in CALLS)
        self.assertIn("The options are: ['A', 'B']", mc)
        self.assertIn("Units for answer: units", numeric)
        self.assertIn("LOWER BOUND MESSAGE\nUPPER BOUND MESSAGE", numeric)
        self.assertIn("Percentile 95: XX", numeric)
        self.assertIn("Percentile 95: YYYY-MM-DD", date)

    def test_research_runs_every_pass_of_the_profile(self):
        b = make_bot("standard")
        research = asyncio.run(b.run_research(FakeBinary()))
        self.assertIn("## Current status and recent news", research)
        self.assertIn("## Base rates and outside forecasts", research)
        self.assertNotIn("AskNews", research)
        self.assertEqual(len(CALLS), 2)
        self.assertEqual({model for model, _ in CALLS}, {config.GPT})

    def test_research_adds_asknews_when_keys_are_set(self):
        os.environ["ASKNEWS_CLIENT_ID"] = "id"
        os.environ["ASKNEWS_SECRET"] = "secret"
        b = make_bot("lean")
        research = asyncio.run(b.run_research(FakeBinary()))
        self.assertIn("## Recent news (AskNews)\nasknews headlines", research)
        self.assertIn("## Research", research)

    def test_research_survives_one_failing_source(self):
        os.environ["ASKNEWS_API_KEY"] = "key"
        FAIL_MODELS.add("asknews-fails")
        b = make_bot("standard")
        research = asyncio.run(b.run_research(FakeBinary()))
        self.assertNotIn("AskNews", research)
        self.assertIn("## Current status and recent news", research)

    def test_research_falls_back_to_a_note_when_everything_fails(self):
        FAIL_MODELS.add("search-fails")
        b = make_bot("standard")
        research = asyncio.run(b.run_research(FakeBinary()))
        self.assertEqual(research, prompts.NO_RESEARCH_NOTE)

    def test_binary_aggregation_uses_trimmed_log_odds(self):
        b = make_bot("standard")
        result = asyncio.run(
            b._aggregate_predictions([0.01, 0.30, 0.30, 0.30, 0.99], FakeBinary())
        )
        self.assertAlmostEqual(result, 0.30, places=9)

    def test_other_aggregation_is_left_to_the_template(self):
        b = make_bot("standard")
        for question, predictions in (
            (FakeNumeric(), ["d1", "d2"]),
            (FakeMultipleChoice(), ["o1", "o2"]),
            (FakeBinary(), ["not-a-number", 0.4]),
            (FakeBinary(), [True, False]),
        ):
            result = asyncio.run(b._aggregate_predictions(predictions, question))
            self.assertEqual(result, "template-aggregate")

    def test_every_model_is_tracked_under_its_stage(self):
        b = make_bot("minimal")
        llms = b.init_kwargs["llms"]
        self.assertEqual(llms["researcher"].stage, bot.SEARCH)
        self.assertEqual(llms["parser"].stage, bot.HELPER)
        self.assertEqual(llms["summarizer"].stage, bot.HELPER)
        self.assertEqual(llms["default"].stage, bot.FORECAST)
        for member in b.profile.ensemble:
            self.assertEqual(b._llm_for(member.model).stage, bot.FORECAST)
        for llm in (*llms.values(), *b._member_llms.values()):
            self.assertIs(llm.ledger, b.ledger)
            # stage and ledger are ours: they must not reach the model request
            self.assertNotIn("stage", llm.kwargs)
            self.assertNotIn("ledger", llm.kwargs)

    def test_search_cap_follows_the_profile_in_request_and_prompt(self):
        for name, profile in config.PROFILES.items():
            b = make_bot(name)
            body = b.init_kwargs["llms"]["researcher"].kwargs["extra_body"]
            self.assertEqual(body["max_tool_calls"], profile.max_searches, name)
            self.assertEqual(
                body["tools"][0]["parameters"]["max_uses"], profile.max_searches, name
            )
            CALLS.clear()
            asyncio.run(b.run_research(FakeBinary(qid=1)))
            self.assertTrue(CALLS)
            for _, prompt in CALLS:
                self.assertIn(f"at most {profile.max_searches} web searches", prompt)

    def test_pick_spread_takes_one_of_each_type_first(self):
        questions = [FakeBinary(1), FakeBinary(2), FakeNumeric(3), FakeDate(4)]
        self.assertEqual(
            [q.id_of_question for q in bot.pick_spread(questions, 2)], [1, 3]
        )
        self.assertEqual(
            [q.id_of_question for q in bot.pick_spread(questions, 4)], [1, 3, 4, 2]
        )
        self.assertEqual(len(bot.pick_spread(questions, 99)), 4)
        self.assertEqual(bot.pick_spread([], 3), [])

    def test_cost_report_shows_the_credit_difference_when_known(self):
        ledger = bot.CostLedger()
        report = bot.cost_report(ledger, 2, 5.0, 4.6)
        self.assertIn("$0.40 spent", report)
        self.assertNotIn("spent", bot.cost_report(ledger, 2, None, 4.6))

    def test_balance_check_never_raises(self):
        os.environ.pop("OPENROUTER_API_KEY", None)
        self.assertIsNone(bot.openrouter_credits_remaining())

        class Boom:
            @staticmethod
            def get(*args, **kwargs):
                raise ConnectionError("no network")

        original = bot.requests
        os.environ["OPENROUTER_API_KEY"] = "test-key"
        try:
            bot.requests = Boom
            self.assertIsNone(bot.openrouter_credits_remaining())
        finally:
            bot.requests = original
            os.environ.pop("OPENROUTER_API_KEY", None)

    def test_balance_check_reads_limit_remaining(self):
        class Response:
            def __init__(self, payload):
                self.payload = payload

            def raise_for_status(self):
                return None

            def json(self):
                return self.payload

        class Fake:
            payload = {}

            @classmethod
            def get(cls, url, headers, timeout):
                assert url == "https://openrouter.ai/api/v1/key"
                assert headers == {"Authorization": "Bearer test-key"}
                return Response(cls.payload)

        original = bot.requests
        os.environ["OPENROUTER_API_KEY"] = "test-key"
        try:
            bot.requests = Fake
            Fake.payload = {"data": {"limit_remaining": 37.5}}
            self.assertEqual(bot.openrouter_credits_remaining(), 37.5)
            Fake.payload = {"data": {"limit_remaining": None}}
            self.assertIsNone(bot.openrouter_credits_remaining())
        finally:
            bot.requests = original
            os.environ.pop("OPENROUTER_API_KEY", None)


if __name__ == "__main__":
    unittest.main()
