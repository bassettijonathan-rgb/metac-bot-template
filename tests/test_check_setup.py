"""
Tests for check_setup.py with the network replaced by canned answers.

    python -m unittest discover -s tests
"""
import contextlib
import io
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import check_setup  # noqa: E402
import config  # noqa: E402


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.ok = 200 <= status_code < 300

    def json(self):
        return self._payload

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError(f"HTTP {self.status_code}")


def run_quietly(function, *args):
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        result = function(*args)
    return result, output.getvalue()


def catalogue(model_ids):
    return FakeResponse(
        payload={
            "data": [
                {"id": name, "pricing": {"prompt": "0.000003", "completion": "0.000015"}}
                for name in model_ids
            ]
        }
    )


class CheckSetupTests(unittest.TestCase):
    def setUp(self):
        self.saved = {
            name: os.environ.pop(name, None)
            for name in ("METACULUS_TOKEN", "OPENROUTER_API_KEY")
        }

    def tearDown(self):
        for name, value in self.saved.items():
            os.environ.pop(name, None)
            if value is not None:
                os.environ[name] = value

    def test_configured_models_cover_every_profile_without_the_prefix(self):
        used = check_setup.configured_models()
        for profile in config.PROFILES.values():
            for model in [m.model for m in profile.ensemble] + [
                profile.search_model,
                profile.helper_model,
            ]:
                self.assertIn(profile.name, used[model.removeprefix("openrouter/")])
        self.assertFalse(any(name.startswith("openrouter/") for name in used))

    def test_all_models_found(self):
        with mock.patch.object(
            check_setup.requests, "get", return_value=catalogue(check_setup.configured_models())
        ):
            problems, output = run_quietly(check_setup.check_models)
        self.assertEqual(problems, [])
        self.assertIn("$3.00 in / $15.00 out per million tokens", output)

    def test_missing_model_is_reported_with_close_names(self):
        wanted = sorted(check_setup.configured_models())
        gone = wanted[0]
        vendor = gone.split("/")[0]
        offered = wanted[1:] + [gone + "-preview", f"{vendor}/something-else"]
        with mock.patch.object(check_setup.requests, "get", return_value=catalogue(offered)):
            problems, output = run_quietly(check_setup.check_models)
        self.assertEqual(problems, [f"Model {gone} is not on OpenRouter"])
        self.assertIn(f"Closest: {gone}-preview", output)
        self.assertIn(f"All {vendor} models on OpenRouter today", output)
        self.assertIn(f"    {vendor}/something-else", output)

    def test_unreachable_model_list_is_a_problem_not_a_crash(self):
        with mock.patch.object(check_setup.requests, "get", side_effect=OSError("no network")):
            problems, _ = run_quietly(check_setup.check_models)
        self.assertEqual(len(problems), 1)

    def test_missing_or_placeholder_metaculus_token(self):
        for value in (None, "", "REPLACE_ME"):
            os.environ.pop("METACULUS_TOKEN", None)
            if value is not None:
                os.environ["METACULUS_TOKEN"] = value
            with mock.patch.object(check_setup.requests, "get") as get:
                problems, _ = run_quietly(check_setup.check_metaculus)
            self.assertEqual(problems, ["METACULUS_TOKEN is missing"])
            get.assert_not_called()

    def test_rejected_metaculus_token_is_never_printed(self):
        os.environ["METACULUS_TOKEN"] = "secret-token-value"
        with mock.patch.object(check_setup.requests, "get", return_value=FakeResponse(401)):
            problems, output = run_quietly(check_setup.check_metaculus)
        self.assertEqual(problems, ["METACULUS_TOKEN was rejected by Metaculus"])
        self.assertNotIn("secret-token-value", output)

    def test_personal_account_token_is_refused(self):
        os.environ["METACULUS_TOKEN"] = "secret-token-value"
        account = FakeResponse(payload={"id": 5, "username": "someone", "is_bot": False})
        with mock.patch.object(check_setup.requests, "get", return_value=account):
            problems, _ = run_quietly(check_setup.check_metaculus)
        self.assertEqual(problems, ["METACULUS_TOKEN is not a bot account token"])

    def test_missing_model_key_is_only_an_error_when_required(self):
        problems, output = run_quietly(check_setup.check_openrouter_key, False)
        self.assertEqual(problems, [])
        self.assertIn("not set yet", output)
        problems, _ = run_quietly(check_setup.check_openrouter_key, True)
        self.assertEqual(problems, ["OPENROUTER_API_KEY is missing"])

    def test_model_key_credit_is_shown_without_the_key(self):
        os.environ["OPENROUTER_API_KEY"] = "secret-key-value"
        answer = FakeResponse(payload={"data": {"limit_remaining": 12.5}})
        with mock.patch.object(check_setup.requests, "get", return_value=answer):
            problems, output = run_quietly(check_setup.check_openrouter_key, True)
        self.assertEqual(problems, [])
        self.assertIn("$12.50", output)
        self.assertNotIn("secret-key-value", output)


if __name__ == "__main__":
    unittest.main()
