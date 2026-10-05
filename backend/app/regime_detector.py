"""
Rule-based regime detector (Section 7: "student-friendly regime detector").
Transparent thresholds over context features -> auditable regime probabilities,
rather than a black-box classifier. This is deliberately simple so every
weight decision downstream can be traced back to a human-checkable rule.
"""
from __future__ import annotations
from app.config import REGIMES
import numpy as np


def detect_regime(variable_medians: dict, season: str, lat: float) -> dict:
    """
    variable_medians: {"precipitation": x, "temperature": y, "wind_speed": z}
    Returns a probability distribution over REGIMES using simple scored rules.
    """
    p = variable_medians.get("precipitation", 0.0)
    t = variable_medians.get("temperature", 28.0)
    w = variable_medians.get("wind_speed", 15.0)

    scores = {r: 0.0 for r in REGIMES}

    # Rainfall-driven rules
    if p >= 80:
        scores["depression"] += 3.0
        scores["active_monsoon"] += 1.5
    elif p >= 40:
        scores["active_monsoon"] += 2.5
    elif p <= 5 and season == "sw_monsoon":
        scores["break_monsoon"] += 2.5
    elif p >= 15 and season == "pre_monsoon":
        scores["pre_monsoon"] += 2.0

    # Wind-driven rule (coastal depression signature)
    if w >= 45:
        scores["depression"] += 2.5

    # Temperature-driven rule
    if t >= 39:
        scores["heatwave"] += 3.0

    # Season prior (western disturbances only make sense in winter/pre-monsoon)
    if season == "winter":
        scores["western_disturbance"] += 1.5
        scores["normal"] += 1.0
    if season == "post_monsoon":
        scores["depression"] += 0.5

    # Always give "normal" a small floor so probabilities never degenerate
    scores["normal"] += 0.4

    arr = np.array([scores[r] for r in REGIMES])
    probs = np.exp(arr) / np.exp(arr).sum()
    return {r: round(float(pr), 4) for r, pr in zip(REGIMES, probs)}


def top_regime(regime_probs: dict) -> str:
    return max(regime_probs, key=regime_probs.get)
