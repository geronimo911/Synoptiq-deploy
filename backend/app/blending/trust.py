"""
Forecast Trust, Disagreement and Bust Detection (Section 10).

Trust is explicitly NOT another forecast value — it's a decision-support
signal built from five named components so the dashboard can show *why*
confidence is high or low, not just a bare number.
"""
from __future__ import annotations
import numpy as np
import joblib
from functools import lru_cache
from app.config import MODELS_DIR, RUNTIME_MODE
from app.blending.features import encode

MODEL_DIR = MODELS_DIR / "bust"
ABSTAIN_TRUST_THRESHOLD = 0.35
ABSTAIN_BUST_THRESHOLD = 0.6


@lru_cache(maxsize=None)
def _load_bust_model(variable: str):
    path = MODEL_DIR / f"bust_{variable}.joblib"
    if not path.exists():
        if RUNTIME_MODE == "real":
            raise RuntimeError(f"REAL bust-risk model is missing: {path.name}")
        return None
    return joblib.load(path)


def _regime_stability(regime_probs: dict) -> float:
    """1.0 = one regime dominates cleanly; lower = the regime detector itself is unsure."""
    if not regime_probs:
        return 0.5
    top = max(regime_probs.values())
    return float(np.clip((top - 1.0 / len(regime_probs)) / (1.0 - 1.0 / len(regime_probs)), 0.0, 1.0))


def compute_trust(
    variable: str,
    weights: dict[str, float],
    historical_skill_by_model: dict[str, float],
    disagreement: float,
    lead_hours: int,
    regime_probs: dict,
    n_sources_available: int,
    n_sources_expected: int,
    context_features: "pd.DataFrame | None" = None,
) -> dict:
    hist_skill_weighted = sum(weights[m] * historical_skill_by_model.get(m, 0.5) for m in weights)

    value_scale = {"precipitation": 100.0, "temperature": 8.0, "wind_speed": 40.0}.get(variable, 50.0)
    disagreement_signal = float(np.clip(1.0 - disagreement / value_scale, 0.0, 1.0))

    lead_signal = float(np.clip(1.0 - (lead_hours / 120.0) * 0.6, 0.15, 1.0))
    regime_signal = _regime_stability(regime_probs)
    data_quality_signal = n_sources_available / max(n_sources_expected, 1)

    signals = {
        "historical_skill": round(hist_skill_weighted, 3),
        "model_agreement": round(disagreement_signal, 3),
        "lead_time_confidence": round(lead_signal, 3),
        "regime_stability": round(regime_signal, 3),
        "data_quality": round(data_quality_signal, 3),
    }
    weights_of_signals = {
        "historical_skill": 0.30, "model_agreement": 0.25, "lead_time_confidence": 0.20,
        "regime_stability": 0.15, "data_quality": 0.10,
    }
    trust_score = float(sum(signals[k] * weights_of_signals[k] for k in signals))
    trust_score = round(float(np.clip(trust_score, 0.0, 1.0)), 3)

    bust_model = _load_bust_model(variable)
    if bust_model is not None and context_features is not None:
        bust_probability = float(bust_model.predict_proba(context_features)[0, 1])
    else:
        bust_probability = float(np.clip(
            0.5 * (1 - disagreement_signal) + 0.3 * (1 - lead_signal) + 0.2 * (1 - regime_signal), 0, 1
        ))
    bust_probability = round(bust_probability, 3)

    abstain = trust_score < ABSTAIN_TRUST_THRESHOLD or bust_probability > ABSTAIN_BUST_THRESHOLD

    return {
        "trust_score": trust_score,
        "disagreement": round(disagreement, 2),
        "bust_probability": bust_probability,
        "abstain": bool(abstain),
        "signals": signals,
    }
