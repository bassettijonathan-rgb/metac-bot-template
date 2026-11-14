"""
Tests for the parts of the bot that do not need the network or any key:
aggregation maths, profile choice, prompt assembly.

    python -m unittest discover -s tests
"""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import prompts  # noqa: E402
from aggregation import aggregate_binary, disagreement, inverse_logit, logit  # noqa: E402


class AggregationTests(unittest.TestCase):
    def test_logit_round_trip(self):
        for p in (0.01, 0.2, 0.5, 0.8, 0.99):
            self.assertAlmostEqual(inverse_logit(logit(p)), p, places=9)

    def test_identical_forecasts_are_unchanged(self):
        self.assertAlmostEqual(aggregate_binary([0.3] * 5), 0.3, places=9)

    def test_five_forecasts_drop_highest_and_lowest(self):
        # 0.01 and 0.99 are the outliers; the middle three are all 0.30.
        self.assertAlmostEqual(
            aggregate_binary([0.01, 0.30, 0.30, 0.30, 0.99]), 0.30, places=9
        )

    def test_three_forecasts_use_all_values(self):
        expected = inverse_logit((logit(0.2) + logit(0.5) + logit(0.8)) / 3)
        self.assertAlmostEqual(aggregate_binary([0.2, 0.5, 0.8]), expected, places=9)
        self.assertAlmostEqual(expected, 0.5, places=9)

    def test_log_odds_mean_differs_from_plain_mean(self):
        result = aggregate_binary([0.01, 0.10, 0.10])
        self.assertLess(result, (0.01 + 0.10 + 0.10) / 3)
        self.assertGreater(result, 0.02)

    def test_result_is_clipped(self):
        self.assertEqual(aggregate_binary([0.999, 0.999, 0.999]), 0.98)
        self.assertEqual(aggregate_binary([0.001, 0.001, 0.001]), 0.02)

    def test_order_does_not_matter(self):
        a = aggregate_binary([0.1, 0.4, 0.6, 0.7, 0.9])
        b = aggregate_binary([0.9, 0.6, 0.1, 0.7, 0.4])
        self.assertAlmostEqual(a, b, places=12)

    def test_single_forecast(self):
        self.assertAlmostEqual(aggregate_binary([0.42]), 0.42, places=9)

    def test_rejects_empty_and_invalid(self):
        with self.assertRaises(ValueError):
            aggregate_binary([])
        with self.assertRaises(ValueError):
            aggregate_binary([0.5, 1.5])
        with self.assertRaises(ValueError):
            aggregate_binary([math.nan])

    def test_disagreement(self):
        self.assertEqual(disagreement([0.5, 0.5, 0.5]), 0.0)
        self.assertAlmostEqual(disagreement([0.2, 0.8]), logit(0.8) - logit(0.2))
        self.assertEqual(disagreement([0.3]), 0.0)


class ProfileTests(unittest.TestCase):
    def test_explicit_profile_wins_over_balance(self):
        self.assertEqual(config.choose_profile("full", 1.0).name, "full")
        self.assertEqual(config.choose_profile(" Lean ", 500.0).name, "lean")

    def test_auto_steps_down_as_credit_runs_out(self):
        self.assertEqual(config.choose_profile("auto", 100.0).name, "standard")
        self.assertEqual(config.choose_profile("auto", 40.0).name, "standard")
        self.assertEqual(config.choose_profile("auto", 39.99).name, "lean")
        self.assertEqual(config.choose_profile("auto", 10.0).name, "lean")
        self.assertEqual(config.choose_profile("auto", 9.99).name, "minimal")
        self.assertEqual(config.choose_profile("auto", 0.0).name, "minimal")

    def test_unknown_balance_uses_default(self):
        self.assertEqual(config.choose_profile("auto", None).name, config.DEFAULT_PROFILE)
        self.assertEqual(config.choose_profile(None, None).name, config.DEFAULT_PROFILE)
        self.assertEqual(config.choose_profile("", None).name, config.DEFAULT_PROFILE)

    def test_unknown_profile_name_fails_loudly(self):
        with self.assertRaises(ValueError):
            config.choose_profile("turbo", 50.0)

    def test_every_profile_is_well_formed(self):
        framings = {config.OUTSIDE_VIEW, config.INSIDE_VIEW, config.SKEPTIC}
        for profile in config.PROFILES.values():
            self.assertGreaterEqual(len(profile.ensemble), 3, profile.name)
            for member in profile.ensemble:
                self.assertIn(member.framing, framings)
                self.assertTrue(member.model.startswith("openrouter/"))
            for pass_key in profile.research_passes:
                self.assertIn(pass_key, prompts.RESEARCH_PASSES)
            # More than one model family, so one provider outage or one
            # model's blind spot cannot take out the whole forecast.
            families = {m.model.split("/")[1] for m in profile.ensemble}
            self.assertGreaterEqual(len(families), 2, profile.name)


FIELDS = dict(
    question_text="Will the widget index close above 100 on 2026-12-31?",
    background="The widget index tracks widgets.",
    resolution_criteria="Resolves Yes if the official close is above 100.",
    fine_print="Revisions after publication are ignored.",
    research="## Current status\n- Index at 97 on 2026-11-10 (Widget Times)",
    today="2026-11-12",
    resolution_date="2026-12-31",
)


class PromptTests(unittest.TestCase):
    def assert_contains_question(self, text):
        for value in FIELDS.values():
            self.assertIn(value, text)

    def test_binary_prompt_for_each_framing(self):
        seen = set()
        for framing in (config.OUTSIDE_VIEW, config.INSIDE_VIEW, config.SKEPTIC):
            text = prompts.binary_prompt(framing, **FIELDS)
            self.assert_contains_question(text)
            self.assertTrue(text.rstrip().endswith('"Probability: ZZ%", 0-100'))
            seen.add(text)
        self.assertEqual(len(seen), 3, "each framing must produce a different prompt")

    def test_framings_lead_with_different_steps(self):
        outside = prompts.binary_prompt(config.OUTSIDE_VIEW, **FIELDS)
        inside = prompts.binary_prompt(config.INSIDE_VIEW, **FIELDS)
        skeptic = prompts.binary_prompt(config.SKEPTIC, **FIELDS)
        self.assertIn("Outside view first", outside)
        self.assertIn("Causal path first", inside)
        self.assertIn("like a contract", skeptic)

    def test_multiple_choice_prompt_lists_options(self):
        text = prompts.multiple_choice_prompt(
            config.INSIDE_VIEW, options=["Red", "Green", "Blue"], **FIELDS
        )
        self.assert_contains_question(text)
        self.assertIn("['Red', 'Green', 'Blue']", text)
        self.assertIn("Option_N: Probability_N", text)

    def test_numeric_prompt_asks_for_tails_and_units(self):
        text = prompts.numeric_prompt(
            config.OUTSIDE_VIEW,
            unit_of_measure="points",
            lower_bound_message="The outcome can not be lower than 0 points.",
            upper_bound_message="The question creator thinks the number is likely not higher than 200 points.",
            **FIELDS,
        )
        self.assert_contains_question(text)
        self.assertIn("Units for answer: points", text)
        self.assertIn("can not be lower than 0 points", text)
        positions = [text.index(f"Percentile {p}:") for p in (5, 10, 20, 40, 50, 60, 80, 90, 95)]
        self.assertEqual(positions, sorted(positions))

    def test_numeric_prompt_without_units(self):
        text = prompts.numeric_prompt(
            config.SKEPTIC,
            unit_of_measure=None,
            lower_bound_message="low",
            upper_bound_message="high",
            **FIELDS,
        )
        self.assertIn("Not stated (please infer this)", text)

    def test_date_prompt_format(self):
        text = prompts.date_prompt(
            config.OUTSIDE_VIEW,
            lower_bound_message="The outcome can not be lower than 2026-01-01 .",
            upper_bound_message="The outcome can not be higher than 2027-01-01 .",
            **FIELDS,
        )
        self.assert_contains_question(text)
        self.assertIn("Percentile 95: YYYY-MM-DD (latest date)", text)

    def test_optional_fields_can_be_missing(self):
        fields = dict(FIELDS, background=None, fine_print="", resolution_date=None)
        text = prompts.binary_prompt(config.OUTSIDE_VIEW, **fields)
        self.assertNotIn("Background:", text)
        self.assertNotIn("Fine print:", text)
        self.assertNotIn("None", text)
        self.assertIn("Today is 2026-11-12.", text)

    def test_conditional_disclaimer_is_included(self):
        text = prompts.binary_prompt(
            config.OUTSIDE_VIEW, conditional_disclaimer="ONLY THE CHILD QUESTION", **FIELDS
        )
        self.assertIn("ONLY THE CHILD QUESTION", text)

    def test_research_prompts(self):
        for pass_key in prompts.RESEARCH_PASSES:
            text = prompts.research_prompt(
                pass_key,
                question_text=FIELDS["question_text"],
                resolution_criteria=FIELDS["resolution_criteria"],
                fine_print=None,
                background=None,
                today="2026-11-12",
                resolution_date=None,
            )
            self.assertIn(FIELDS["question_text"], text)
            self.assertIn("You do not give a forecast", text)
            self.assertNotIn("None", text)
            self.assertTrue(prompts.research_section_title(pass_key))


if __name__ == "__main__":
    unittest.main()
