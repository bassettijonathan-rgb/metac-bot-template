"""
Ensemble and budget configuration.

No third-party imports here, so this file can be unit-tested on its own.

A "profile" decides which models forecast each question and how many web
research passes run. The profile is picked from the BOT_PROFILE environment
variable (a GitHub repository variable). With BOT_PROFILE=auto (the default)
the bot looks at how many OpenRouter credits are left and steps down to a
cheaper profile before the money runs out, because a missed question scores
worse than a cheap forecast.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

# Framings: each ensemble member reasons in a different order so that the
# members do not all make the same mistake. See prompts.py.
OUTSIDE_VIEW = "outside_view_first"
INSIDE_VIEW = "inside_view_first"
SKEPTIC = "skeptic"

# Model ids as OpenRouter names them (checked 2026-11-12). Metaculus-donated
# credits only cover OpenAI, Anthropic and Google models.
OPUS = "openrouter/anthropic/claude-opus-5.5"
SONNET = "openrouter/anthropic/claude-sonnet-5.5"
HAIKU = "openrouter/anthropic/claude-haiku-5.5"
GPT = "openrouter/openai/gpt-6-sol"
GPT_SMALL = "openrouter/openai/gpt-6-luna"
GEMINI = "openrouter/google/gemini-3.1-pro-preview"


@dataclass(frozen=True)
class Member:
    model: str
    framing: str


@dataclass(frozen=True)
class Profile:
    name: str
    ensemble: tuple[Member, ...]
    search_model: str
    research_passes: tuple[str, ...]  # keys of prompts.RESEARCH_PASSES
    helper_model: str  # parses answers into numbers, writes the short summary
    # Most web searches one research pass may run. Each search is billed on
    # its own (about $0.01 with the provider's native search), and left alone
    # a model ran 10 to 17 of them per question, which was 86% of the cost.
    max_searches: int = 5


PROFILES: dict[str, Profile] = {
    "full": Profile(
        name="full",
        ensemble=(
            Member(OPUS, OUTSIDE_VIEW),
            Member(GPT, OUTSIDE_VIEW),
            Member(GEMINI, INSIDE_VIEW),
            Member(OPUS, INSIDE_VIEW),
            Member(GPT, SKEPTIC),
        ),
        search_model=GPT,
        research_passes=("status", "base_rates"),
        helper_model=HAIKU,
        max_searches=6,
    ),
    "standard": Profile(
        name="standard",
        ensemble=(
            Member(SONNET, OUTSIDE_VIEW),
            Member(GPT, OUTSIDE_VIEW),
            Member(GEMINI, INSIDE_VIEW),
            Member(GPT, INSIDE_VIEW),
            Member(SONNET, SKEPTIC),
        ),
        search_model=GPT,
        research_passes=("status", "base_rates"),
        helper_model=HAIKU,
    ),
    "lean": Profile(
        name="lean",
        ensemble=(
            Member(GPT, OUTSIDE_VIEW),
            Member(GEMINI, INSIDE_VIEW),
            Member(SONNET, SKEPTIC),
        ),
        search_model=GPT,
        research_passes=("combined",),
        helper_model=HAIKU,
    ),
    "minimal": Profile(
        name="minimal",
        ensemble=(
            Member(HAIKU, OUTSIDE_VIEW),
            Member(GPT_SMALL, INSIDE_VIEW),
            Member(HAIKU, SKEPTIC),
        ),
        search_model=GPT_SMALL,
        research_passes=("combined",),
        helper_model=HAIKU,
        max_searches=4,
    ),
}

DEFAULT_PROFILE = "standard"

# Dollars of OpenRouter credit left below which the bot steps down.
LEAN_BELOW_USD = 40.0
MINIMAL_BELOW_USD = 10.0


def choose_profile(
    requested: str | None,
    credits_remaining_usd: float | None,
) -> Profile:
    """
    requested: value of BOT_PROFILE ("auto", "", None or a profile name).
    credits_remaining_usd: None when the balance is unknown or unlimited.
    """
    requested = (requested or "auto").strip().lower()
    if requested in PROFILES:
        return PROFILES[requested]
    if requested != "auto":
        raise ValueError(
            f"Unknown BOT_PROFILE '{requested}'. Use auto or one of {sorted(PROFILES)}"
        )
    if credits_remaining_usd is None:
        return PROFILES[DEFAULT_PROFILE]
    if credits_remaining_usd < MINIMAL_BELOW_USD:
        return PROFILES["minimal"]
    if credits_remaining_usd < LEAN_BELOW_USD:
        return PROFILES["lean"]
    return PROFILES[DEFAULT_PROFILE]


def requested_profile_from_env() -> str:
    return os.getenv("BOT_PROFILE", "auto")
