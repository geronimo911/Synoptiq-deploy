"""Synoptic regime router — the explicit, auditable face of regime conditioning.

The blending meta-model already receives regime probabilities as features
(app/regime_detector.py produces them from transparent rules over rainfall,
temperature, wind and season). What was missing operationally was the
*answer* to "so what did the regime change?" — a visible statement of which
regime is active, how each model has historically performed in that regime,
and how the issued weights shift relative to the all-weather average.

This module holds:
  * the canonical regime catalogue with plain-language synoptic
    descriptions, so a meteorologist can check the label rather than trust
    a black box;
  * policy-shift formatting (issued weights vs global mean weights);
  * the current-regime selection used by the API and the dashboard.

The conditional skill and weight profiles are computed offline from the
archived 12-month record by backend/scripts/build_advanced_artifacts.py and
served read-only from artifacts/.../research/regime_router.json.
"""
from __future__ import annotations

# Canonical regimes — keys must match app.config.REGIMES exactly.
REGIME_CATALOGUE = {
    "active_monsoon": {
        "label": "Active Monsoon Trough",
        "synoptic": "Monsoon trough in its normal position across the "
                    "peninsula; strong low-level (850 hPa) westerly jet "
                    "feeding organised rainfall over the west coast and "
                    "central India.",
        "typical_signature": "widespread rain, moderate temperatures, "
                             "sustained onshore winds",
    },
    "break_monsoon": {
        "label": "Break Monsoon",
        "synoptic": "Trough shifted to the Himalayan foothills; suppressed "
                    "convection over the peninsula and core monsoon zone "
                    "despite the calendar season.",
        "typical_signature": "anomalously dry over central India, rain "
                             "concentrated along the foothills",
    },
    "depression": {
        "label": "Cyclonic Low / Depression",
        "synoptic": "Low-pressure system (depression/deep depression) over "
                    "the Bay of Bengal or Arabian Sea; closed circulation "
                    "with strong winds and intense convective bands.",
        "typical_signature": "very heavy rain, high wind speeds, falling "
                             "pressure",
    },
    "western_disturbance": {
        "label": "Western Disturbance",
        "synoptic": "Mid-latitude trough in the westerlies crossing "
                    "northwest India; the dominant winter and pre-monsoon "
                    "rain-bearing system for the north.",
        "typical_signature": "cloudy, cool, light-to-moderate rain over "
                             "the Indo-Gangetic Plains",
    },
    "heatwave": {
        "label": "Heat Dome / Heatwave",
        "synoptic": "Stable high-pressure ridge over central/north India "
                    "with subsidence, clear skies and suppressed convection.",
        "typical_signature": "very high maximum temperatures, low humidity, "
                             "near-zero rainfall",
    },
    "pre_monsoon": {
        "label": "Pre-Monsoon Convection",
        "synoptic": "Seasonal heating with locally organised thunderstorm "
                    "activity (Nor'westers over the east, coastal Kerala "
                    "pre-monsoon showers) before monsoon onset.",
        "typical_signature": "isolated intense convective cells, high "
                             "daytime temperatures, gusty winds",
    },
    "normal": {
        "label": "Fair Weather / No Dominant System",
        "synoptic": "No organised synoptic forcing; the blend defaults to "
                    "its all-weather weight profile.",
        "typical_signature": "near-climatological conditions",
    },
}


def describe_regime(regime: str) -> dict:
    entry = REGIME_CATALOGUE.get(regime, {})
    return {
        "regime": regime,
        "label": entry.get("label", regime.replace("_", " ").title()),
        "synoptic": entry.get("synoptic", ""),
        "typical_signature": entry.get("typical_signature", ""),
    }


def policy_shift(regime: str, regime_weights: dict[str, float],
                 global_weights: dict[str, float], min_delta: float = 0.02) -> dict:
    """Human-readable statement of how the regime moved the weights.

    Returns the per-model deltas (regime mean minus all-weather mean) and a
    sentence naming the largest shift, or an honest "no material shift"
    when every delta is below min_delta.
    """
    deltas = {m: round(float(regime_weights.get(m, 0.0))
                       - float(global_weights.get(m, 0.0)), 4)
              for m in sorted(set(regime_weights) | set(global_weights))}
    material = {m: d for m, d in deltas.items() if abs(d) >= min_delta}
    if not material:
        return {"deltas": deltas, "material": False, "statement":
                f"In the {describe_regime(regime)['label']} regime the issued "
                f"weights stay within {min_delta:.0%} of the all-weather "
                f"profile — no material policy shift."}
    top = max(material, key=lambda m: abs(material[m]))
    direction = "up" if material[top] > 0 else "down"
    reason = _SHIFT_REASONS.get((regime, top, direction), "")
    statement = (f"Detected synoptic regime: {describe_regime(regime)['label']}. "
                 f"Dynamic blending policy shifts {top} weight "
                 f"{'up' if material[top] > 0 else 'down'} "
                 f"{abs(material[top]):.0%} versus the all-weather profile")
    if reason:
        statement += f" — {reason}"
    return {"deltas": deltas, "material": True, "statement": statement + "."}


# Explanations are attached only where the historical record actually
# supports them (the build script fills these from measured skill); the
# fallback text stays deliberately generic rather than inventing a reason.
_SHIFT_REASONS: dict[tuple[str, str, str], str] = {}


def register_shift_reasons(reasons: dict[tuple[str, str, str], str]) -> None:
    """Called by the build script with measured explanations."""
    _SHIFT_REASONS.update(reasons)


def dominant_regime(regime_probs: dict[str, float]) -> str:
    if not regime_probs:
        return "normal"
    return max(regime_probs, key=regime_probs.get)
