"""
Tests for costs.py (the per-stage cost ledger). No network, no API key.

    python -m unittest discover -s tests
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from costs import FORECAST, HELPER, SEARCH, Call, CostLedger, call_from_usage  # noqa: E402


class UsageObject:
    def __init__(self, **fields):
        for name, value in fields.items():
            setattr(self, name, value)


class CallFromUsageTests(unittest.TestCase):
    def test_reads_cost_tokens_and_searches_from_a_dict(self):
        call = call_from_usage(
            SEARCH,
            "some/model",
            {
                "prompt_tokens": 1200,
                "completion_tokens": 300,
                "cost": 0.0123,
                "server_tool_use": {"web_search_requests": 4},
            },
        )
        self.assertEqual(
            call, Call(SEARCH, "some/model", 1200, 300, 4, 0.0123)
        )

    def test_reads_the_same_fields_from_an_object(self):
        usage = UsageObject(
            prompt_tokens=10,
            completion_tokens=5,
            cost=0.5,
            server_tool_use=UsageObject(web_search_requests=2),
        )
        call = call_from_usage(FORECAST, "m", usage)
        self.assertEqual((call.web_searches, call.cost_usd), (2, 0.5))

    def test_falls_back_when_usage_has_no_cost(self):
        call = call_from_usage(HELPER, "m", {"prompt_tokens": 1}, fallback_cost=0.02)
        self.assertEqual(call.cost_usd, 0.02)

    def test_a_reported_cost_of_zero_is_not_replaced_by_the_fallback(self):
        call = call_from_usage(HELPER, "m", {"cost": 0}, fallback_cost=0.02)
        self.assertEqual(call.cost_usd, 0.0)

    def test_missing_or_broken_usage_counts_as_zero(self):
        for usage in (None, {}, {"prompt_tokens": "n/a", "cost": "n/a"}):
            call = call_from_usage(HELPER, "m", usage)
            self.assertEqual(
                (call.prompt_tokens, call.completion_tokens, call.web_searches, call.cost_usd),
                (0, 0, 0, 0.0),
            )


class LedgerTests(unittest.TestCase):
    def _ledger(self):
        ledger = CostLedger()
        ledger.record(Call(FORECAST, "a", 1000, 500, 0, 0.01))
        ledger.record(Call(SEARCH, "b", 20000, 800, 3, 0.06))
        ledger.record(Call(FORECAST, "c", 1000, 500, 0, 0.02))
        ledger.record(Call(HELPER, "d", 400, 50, 0, 0.01))
        return ledger

    def test_totals_per_stage_in_a_fixed_order(self):
        stages = self._ledger().by_stage()
        self.assertEqual(list(stages), [SEARCH, FORECAST, HELPER])
        self.assertEqual(stages[FORECAST].calls, 2)
        self.assertEqual(stages[FORECAST].prompt_tokens, 2000)
        self.assertAlmostEqual(stages[FORECAST].cost_usd, 0.03)
        self.assertEqual(stages[SEARCH].web_searches, 3)

    def test_total(self):
        self.assertAlmostEqual(self._ledger().total_usd(), 0.10)

    def test_table_shows_share_and_cost_per_question(self):
        table = self._ledger().format_table(question_count=2)
        search_line = next(line for line in table.splitlines() if line.startswith(SEARCH))
        self.assertIn("$0.0600", search_line)
        self.assertIn("60%", search_line)
        self.assertIn("$0.0300", search_line)  # per question
        total_line = table.splitlines()[-1]
        self.assertIn("$0.1000", total_line)
        self.assertIn("$0.0500", total_line)

    def test_table_survives_an_empty_run_and_zero_questions(self):
        self.assertIn("no model calls", CostLedger().format_table(0))
        self.assertIn("$0.1000", self._ledger().format_table(0))

    def test_table_survives_calls_that_cost_nothing(self):
        ledger = CostLedger()
        ledger.record(Call(FORECAST, "a", 10, 5, 0, 0.0))
        self.assertIn("0%", ledger.format_table(1))


if __name__ == "__main__":
    unittest.main()
