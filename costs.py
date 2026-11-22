"""
Where the money goes.

Every model call the bot makes is recorded here with the stage it belongs to
(web search, forecast, helper) and the cost that OpenRouter itself reports for
that call. At the end of a run the totals are printed per stage, so the
expensive part of a forecast is visible instead of guessed.

No third-party imports here, so this file can be unit-tested on its own.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

SEARCH = "web search"
FORECAST = "forecast"
HELPER = "helper"
STAGE_ORDER = (SEARCH, FORECAST, HELPER)


@dataclass(frozen=True)
class Call:
    stage: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    web_searches: int
    cost_usd: float


@dataclass
class StageTotal:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    web_searches: int = 0
    cost_usd: float = 0.0


def _get(source: Any, name: str) -> Any:
    """Read a field from either an object or a dict."""
    if source is None:
        return None
    if isinstance(source, dict):
        return source.get(name)
    return getattr(source, name, None)


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _as_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _web_searches(usage: Any) -> int:
    for name in ("server_tool_use_details", "server_tool_use"):
        count = _get(_get(usage, name), "web_search_requests")
        if count is not None:
            return _as_int(count)
    return 0


def call_from_usage(stage: str, model: str, usage: Any, fallback_cost: Any = None) -> Call:
    """
    Build a Call from the usage block of a model response.

    usage: the response's usage object or dict. OpenRouter puts the real charge
    in usage.cost and the number of web searches in
    usage.server_tool_use_details.web_search_requests (server_tool_use on some
    endpoints).
    fallback_cost: used only when usage carries no cost.
    """
    cost = _get(usage, "cost")
    if cost is None:
        cost = fallback_cost
    return Call(
        stage=stage,
        model=model,
        prompt_tokens=_as_int(_get(usage, "prompt_tokens")),
        completion_tokens=_as_int(_get(usage, "completion_tokens")),
        web_searches=_web_searches(usage),
        cost_usd=_as_float(cost),
    )


@dataclass
class CostLedger:
    calls: list[Call] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def record(self, call: Call) -> None:
        with self._lock:
            self.calls.append(call)

    def total_usd(self) -> float:
        return sum(call.cost_usd for call in self.calls)

    def by_stage(self) -> dict[str, StageTotal]:
        totals: dict[str, StageTotal] = {}
        for call in self.calls:
            total = totals.setdefault(call.stage, StageTotal())
            total.calls += 1
            total.prompt_tokens += call.prompt_tokens
            total.completion_tokens += call.completion_tokens
            total.web_searches += call.web_searches
            total.cost_usd += call.cost_usd
        ordered = [stage for stage in STAGE_ORDER if stage in totals]
        ordered += sorted(stage for stage in totals if stage not in STAGE_ORDER)
        return {stage: totals[stage] for stage in ordered}

    def format_table(self, question_count: int) -> str:
        """Plain-text table of cost per stage, for the end of the run log."""
        if not self.calls:
            return "Cost by stage: no model calls were recorded."
        total = self.total_usd()
        questions = max(question_count, 1)
        header = (
            f"{'stage':<12}{'calls':>6}{'searches':>10}{'tokens in':>12}"
            f"{'tokens out':>12}{'cost':>10}{'share':>8}{'per question':>14}"
        )
        lines = [
            "Cost by stage, as charged by OpenRouter "
            f"({question_count} question{'s' if question_count != 1 else ''}):",
            header,
            "-" * len(header),
        ]
        for stage, stage_total in self.by_stage().items():
            share = stage_total.cost_usd / total if total > 0 else 0.0
            lines.append(
                f"{stage:<12}{stage_total.calls:>6}{stage_total.web_searches:>10}"
                f"{stage_total.prompt_tokens:>12,}{stage_total.completion_tokens:>12,}"
                f"{'$' + format(stage_total.cost_usd, '.4f'):>10}{share:>8.0%}"
                f"{'$' + format(stage_total.cost_usd / questions, '.4f'):>14}"
            )
        lines.append("-" * len(header))
        lines.append(
            f"{'total':<12}{len(self.calls):>6}{'':>10}{'':>12}{'':>12}"
            f"{'$' + format(total, '.4f'):>10}{'':>8}"
            f"{'$' + format(total / questions, '.4f'):>14}"
        )
        return "\n".join(lines)
