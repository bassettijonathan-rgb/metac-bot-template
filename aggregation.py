"""
Pure-Python helpers for combining the ensemble's binary forecasts.

Kept free of third-party imports on purpose so the logic can be unit-tested
anywhere with `python -m unittest discover -s tests`.
"""
from __future__ import annotations

import math
from typing import Sequence

# Final forecasts are kept inside this range. The tournament is scored with a
# log rule, so a confident miss costs far more than a confident hit earns.
DEFAULT_LOWER_CLIP = 0.02
DEFAULT_UPPER_CLIP = 0.98


def clip(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def logit(probability: float) -> float:
    p = clip(probability, 1e-6, 1 - 1e-6)
    return math.log(p / (1 - p))


def inverse_logit(log_odds: float) -> float:
    if log_odds >= 0:
        return 1 / (1 + math.exp(-log_odds))
    exp = math.exp(log_odds)
    return exp / (1 + exp)


def aggregate_binary(
    predictions: Sequence[float],
    lower_clip: float = DEFAULT_LOWER_CLIP,
    upper_clip: float = DEFAULT_UPPER_CLIP,
) -> float:
    """
    Trimmed mean in log-odds space.

    - With 5 or more forecasts the single highest and single lowest are dropped
      before averaging, so one confused ensemble member cannot drag the result.
    - With 3 or 4 forecasts every forecast is used.
    - Averaging happens on log-odds rather than raw probabilities, which treats
      10% vs 1% as the large disagreement it is.
    - No extremization is applied. Other bot makers reported that fixed
      extremization hurt them, and it has not been validated for this bot.
    """
    cleaned = [float(p) for p in predictions if p is not None and not math.isnan(p)]
    if not cleaned:
        raise ValueError("Cannot aggregate an empty list of predictions")
    for p in cleaned:
        if not 0 <= p <= 1:
            raise ValueError(f"Prediction {p} is not a probability")

    log_odds = sorted(logit(p) for p in cleaned)
    if len(log_odds) >= 5:
        log_odds = log_odds[1:-1]
    mean_log_odds = sum(log_odds) / len(log_odds)
    return clip(inverse_logit(mean_log_odds), lower_clip, upper_clip)


def disagreement(predictions: Sequence[float]) -> float:
    """Spread of the ensemble in log-odds (max minus min). 0 means full agreement."""
    cleaned = [logit(float(p)) for p in predictions if p is not None]
    if len(cleaned) < 2:
        return 0.0
    return max(cleaned) - min(cleaned)
