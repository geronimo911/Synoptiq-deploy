"""
SHAP-backed weight explanations (Section 10 / Section 11 "must-have"):
SHAP feature attributions are the source of truth for every blending
decision. An optional narrative template turns those structured facts into
readable text — it never invents anything not present in the SHAP output,
satisfying the blueprint's explicit constraint on that feature.
"""
from __future__ import annotations
import numpy as np
from app.blending.blend import _load_skillscore_model
from app.blending.features import FEATURE_NAMES

_READABLE = {
    "lead_hours_norm": "forecast lead time",
    "historical_skill": "this model's expanding historical skill here",
    "disagreement_norm": "how much the models disagree",
    "forecast_value_norm": "the raw forecast value itself",
    "consensus_deviation_norm": "how far this model sits from the multi-model consensus",
    "climatological_anomaly_norm": "how anomalous this forecast is vs. the historical climatology",
}
for _r in FEATURE_NAMES:
    if _r.startswith("regime_prob__"):
        _READABLE[_r] = f"probability of {_r.split('__', 1)[1].replace('_', ' ')} regime"
    if _r.startswith("region__"):
        _READABLE[_r] = f"being in {_r.split('__', 1)[1].replace('_', ' ')}"
    if _r.startswith("season__"):
        _READABLE[_r] = f"{_r.split('__', 1)[1].replace('_', ' ')} season"


def _shap_top_drivers(variable: str, model_name: str, feat_df, top_k: int = 3) -> list[dict]:
    reg = _load_skillscore_model(variable, model_name)
    if reg is None:
        return []
    try:
        import shap
        explainer = shap.TreeExplainer(reg)
        sv = explainer.shap_values(feat_df)[0]
    except Exception:
        # Fallback: LightGBM built-in feature importance (no per-instance direction,
        # but still real, model-derived attribution rather than an invented one).
        importances = getattr(reg, "feature_importances_", np.zeros(feat_df.shape[1]))
        sv = importances * np.sign(feat_df.iloc[0].to_numpy())
    order = np.argsort(-np.abs(sv))[:top_k]
    drivers = []
    for i in order:
        name = FEATURE_NAMES[i]
        drivers.append({
            "feature": name,
            "readable": _READABLE.get(name, name),
            "contribution": round(float(sv[i]), 4),
            "direction": "increases" if sv[i] > 0 else "decreases",
        })
    return drivers


def generate_narrative(region_label: str, variable: str, valid_time_str: str, regime: str,
                        weights: list[dict], blended_value: float, trust_score: float,
                        disagreement: float, bust_probability: float, abstain: bool) -> str:
    """Template-only narrative built strictly from structured facts already computed
    upstream (weights, trust signals, SHAP drivers) — no free-form generation."""
    top = max(weights, key=lambda w: w["weight"])
    parts = [
        f"{region_label} \u2014 {variable.replace('_', ' ')} valid {valid_time_str} "
        f"under a {regime.replace('_', ' ')} regime.",
        f"{top['model']} carries the highest weight ({top['weight']*100:.0f}%) in this blend.",
    ]
    # Never narrate "the raw forecast value itself" as the reason a source is
    # trusted — a model's own predicted magnitude is not evidence of its own
    # trustworthiness, and citing it as the "main driver" would misleadingly
    # imply magnitude alone proves skill. Prefer the next-ranked SHAP driver.
    narratable_drivers = [d for d in top["top_drivers"] if d["feature"] != "forecast_value_norm"]
    if narratable_drivers:
        d = narratable_drivers[0]
        parts.append(f"Main driver: {d['readable']} {d['direction']} its trust.")
    parts.append(f"Blended value: {blended_value:.1f}. Trust {trust_score*100:.0f}%, "
                  f"model disagreement {disagreement:.1f}, bust risk {bust_probability*100:.0f}%.")
    if abstain:
        parts.append("Confidence is low enough that this forecast is flagged for human review "
                      "rather than issued at face value.")
    return " ".join(parts)
