"""
Read-only setup check. It makes no model call, submits nothing to Metaculus
and costs nothing, so it can run before any model credit exists.

It answers three questions:
1. Does METACULUS_TOKEN work, and can the bot read the bot-testing-area?
2. Does OpenRouter still offer every model named in config.py?
3. Is OPENROUTER_API_KEY set, and how much credit is left?

Run:
    uv run python check_setup.py                     # a missing model key is reported, not an error
    uv run python check_setup.py --require-llm-key   # a missing model key is an error

Secrets are never printed, only whether they are set.
"""
from __future__ import annotations

import argparse
import difflib
import os
import sys
from collections import Counter

import requests

from config import PROFILES

METACULUS_API = "https://www.metaculus.com/api"
OPENROUTER_API = "https://openrouter.ai/api/v1"
TEST_TOURNAMENT = "bot-testing-area"
# Values shipped in the template's .env.template: a secret that still holds
# one of these was never filled in.
PLACEHOLDERS = {"1234567890", "REPLACE_ME", "your-token-here", "your-api-key-here"}

OK, BAD, NOTE = "✅", "❌", "ℹ️ "


def secret(name: str) -> str | None:
    value = (os.getenv(name) or "").strip()
    return value if value and value not in PLACEHOLDERS else None


def configured_models() -> dict[str, list[str]]:
    """OpenRouter model id (without the litellm 'openrouter/' prefix) -> profiles using it."""
    used: dict[str, list[str]] = {}
    for profile in PROFILES.values():
        models = [member.model for member in profile.ensemble]
        models += [profile.search_model, profile.helper_model]
        for model in models:
            names = used.setdefault(model.removeprefix("openrouter/"), [])
            if profile.name not in names:
                names.append(profile.name)
    return used


def check_metaculus() -> list[str]:
    print("== Metaculus ==")
    token = secret("METACULUS_TOKEN")
    if token is None:
        print(f"{BAD} METACULUS_TOKEN is not set (or still a placeholder).")
        return ["METACULUS_TOKEN is missing"]

    try:
        response = requests.get(
            f"{METACULUS_API}/users/me",  # same address the library uses
            headers={"Authorization": f"Token {token}", "Accept-Language": "en"},
            timeout=30,
        )
    except requests.RequestException as error:
        print(f"{BAD} Could not reach Metaculus: {error.__class__.__name__}")
        return ["Metaculus could not be reached"]
    if response.status_code in (401, 403):
        print(f"{BAD} Metaculus rejected the token (HTTP {response.status_code}).")
        return ["METACULUS_TOKEN was rejected by Metaculus"]
    if not response.ok:
        print(f"{BAD} Metaculus answered HTTP {response.status_code}.")
        return [f"Metaculus answered HTTP {response.status_code}"]

    try:
        account = response.json()
    except ValueError:
        account = {}
    username = account.get("username", "unknown")
    print(f"{OK} Token accepted. Account: {username} (id {account.get('id', 'unknown')}).")
    if account.get("is_bot") is False:
        print(
            f"{BAD} This token belongs to a personal account, not a bot account. "
            "Only bot accounts may forecast in the tournament."
        )
        return ["METACULUS_TOKEN is not a bot account token"]

    # Same call the bot makes to find questions. Nothing is submitted.
    try:
        from forecasting_tools import MetaculusClient

        questions = MetaculusClient().get_all_open_questions_from_tournament(TEST_TOURNAMENT)
    except Exception as error:
        print(f"{BAD} Could not list the questions of {TEST_TOURNAMENT}: {error.__class__.__name__}: {error}")
        return [f"Questions of {TEST_TOURNAMENT} could not be read"]
    kinds = Counter(type(question).__name__ for question in questions)
    summary = ", ".join(f"{count} {kind}" for kind, count in sorted(kinds.items()))
    print(f"{OK} {TEST_TOURNAMENT}: {len(questions)} open question(s) ({summary or 'none'}).")
    return []


def check_models() -> list[str]:
    print("\n== Model names on OpenRouter ==")
    try:
        response = requests.get(f"{OPENROUTER_API}/models", timeout=30)
        response.raise_for_status()
        catalogue = {entry["id"]: entry for entry in response.json()["data"]}
    except Exception as error:
        print(f"{BAD} Could not read the OpenRouter model list: {error.__class__.__name__}")
        return ["OpenRouter model list could not be read"]

    problems: list[str] = []
    missing_vendors: set[str] = set()
    for model, profiles in sorted(configured_models().items()):
        entry = catalogue.get(model)
        where = f"used by: {', '.join(profiles)}"
        if entry is None:
            vendor = model.split("/")[0]
            missing_vendors.add(vendor)
            candidates = [name for name in catalogue if name.startswith(vendor + "/")]
            close = difflib.get_close_matches(model, candidates, n=4, cutoff=0.5)
            print(f"{BAD} {model} is not offered ({where}). Closest: {', '.join(close) or 'none'}")
            problems.append(f"Model {model} is not on OpenRouter")
            continue
        pricing = entry.get("pricing") or {}
        try:
            price = (
                f"${float(pricing['prompt']) * 1e6:.2f} in / "
                f"${float(pricing['completion']) * 1e6:.2f} out per million tokens"
            )
        except (KeyError, TypeError, ValueError):
            price = "price unknown"
        print(f"{OK} {model}: {price} ({where})")

    for vendor in sorted(missing_vendors):
        names = sorted(name for name in catalogue if name.startswith(vendor + "/"))
        print(f"\n{NOTE}All {vendor} models on OpenRouter today:")
        for name in names:
            print(f"    {name}")
    return problems


def check_openrouter_key(required: bool) -> list[str]:
    print("\n== Model credit (OpenRouter) ==")
    key = secret("OPENROUTER_API_KEY")
    if key is None:
        if required:
            print(f"{BAD} OPENROUTER_API_KEY is not set: the bot cannot forecast.")
            return ["OPENROUTER_API_KEY is missing"]
        print(f"{NOTE}OPENROUTER_API_KEY is not set yet: the bot cannot forecast until it is.")
        return []
    try:
        response = requests.get(
            f"{OPENROUTER_API}/key", headers={"Authorization": f"Bearer {key}"}, timeout=30
        )
    except requests.RequestException as error:
        print(f"{BAD} Could not reach OpenRouter: {error.__class__.__name__}")
        return ["OpenRouter could not be reached"]
    if not response.ok:
        print(f"{BAD} OpenRouter rejected the key (HTTP {response.status_code}).")
        return ["OPENROUTER_API_KEY was rejected by OpenRouter"]
    data = response.json().get("data", {})
    remaining = data.get("limit_remaining")
    if remaining is None:
        print(
            f"{OK} Key accepted. It has no spending limit of its own, so the bot cannot "
            "see how much credit is left and will not step down to cheaper models by itself."
        )
    else:
        print(f"{OK} Key accepted. Credit left on this key: ${float(remaining):.2f}.")
    return []


def main() -> None:
    parser = argparse.ArgumentParser(description="Check the bot's setup without spending anything")
    parser.add_argument(
        "--require-llm-key",
        action="store_true",
        help="Treat a missing OPENROUTER_API_KEY as an error",
    )
    args = parser.parse_args()

    problems = check_metaculus() + check_models() + check_openrouter_key(args.require_llm_key)

    print()
    if problems:
        print(f"{BAD} Setup check found {len(problems)} problem(s):")
        for problem in problems:
            print(f"    - {problem}")
        sys.exit(1)
    print(f"{OK} Setup check passed.")


if __name__ == "__main__":
    main()
