"""
Inference-time blending (Section 8):
  F_final(x,t) = sum_i w_i(x,t,c) * F_i(x,t),  sum_i w_i = 1

Weights come from the trained per-source LightGBM skill-score models,
softmax-normalized. If a model artifact is missing (fresh checkout, one
source down, cold start) this transparently falls back to the guardrail in
Section 18: "keep a skill-weighted-average fallback always available" —
weights derived straight from the historical skill table instead.

Both `compute_weights` (single live context) and `batch_blend` (vectorized,
whole-archive) build their feature rows through the SAME named schema in
app/blending/features.py, so a model trained on a named DataFrame is always
queried with a matching named DataFrame/columns — no feature-name mismatch
warnings from LightGBM at inference time.
"""
from __future__ import annotations
import numpy as np
import pandas as pd
import joblib
from functools import lru_cache
from app.config import MODELS, MODELS_DIR, RUNTIME_MODE
from app.blending.features import encode, FEATURE_NAMES

MODEL_DIR = MODELS_DIR / "skill"
TEMPERATURE = 4.0  # softmax temperature; lower = sharper trust decisions


@lru_cache(maxsize=None)
def _load_skillscore_model(variable: str, model_name: str):
    path = MODEL_DIR / f"skillscore_{variable}_{model_name}.joblib"
    if not path.exists():
        if RUNTIME_MODE == "real":
            raise RuntimeError(f"REAL skill model is missing: {path.name}")
        return None
    return joblib.load(path)


def compute_weights(
    variable: str,
    lead_hours: int,
    regime_probs: dict,
    region: str,
    season: str,
    historical_skill_by_model: dict[str, float],
    disagreement: float,
    forecast_values: dict[str, float],
    climatological_anomaly_by_model: dict[str, float] | None = None,
) -> tuple[dict[str, float], str]:
    """Returns (weights_by_model, source: 'meta_model'|'skill_fallback')."""
    available = [m for m in MODELS if m in forecast_values]
    climatological_anomaly_by_model = climatological_anomaly_by_model or {}
    mean_val = float(np.mean([forecast_values[m] for m in available])) if available else 0.0
    value_scale = {"precipitation": 100.0, "temperature": 8.0, "wind_speed": 40.0}.get(variable, 50.0)

    scores = {}
    used_meta_model = True
    for m in available:
        reg = _load_skillscore_model(variable, m)
        if reg is None:
            used_meta_model = False
            break
        consensus_dev = (forecast_values[m] - mean_val) / value_scale
        feat_df = encode(
            lead_hours=lead_hours, regime_probs=regime_probs, region=region, season=season,
            historical_skill=historical_skill_by_model.get(m, 0.5), disagreement=disagreement,
            forecast_value=forecast_values[m], consensus_deviation=consensus_dev,
            climatological_anomaly=climatological_anomaly_by_model.get(m, 0.0),
        )
        scores[m] = float(reg.predict(feat_df)[0])

    if used_meta_model and scores:
        arr = np.array([scores[m] for m in available])
        arr = arr - arr.max()  # numerical stability
        exp = np.exp(arr / TEMPERATURE)
        w = exp / exp.sum()
        weights = {m: float(round(wi, 4)) for m, wi in zip(available, w)}
        return weights, "meta_model"

    if RUNTIME_MODE == "real":
        raise RuntimeError(
            f"REAL model artifacts are incomplete for {variable}; refusing skill fallback in LIVE mode."
        )

    # --- fallback: skill-weighted average straight from historical_skill ---
    raw = np.array([max(historical_skill_by_model.get(m, 0.5), 1e-3) for m in available])
    w = raw / raw.sum()
    weights = {m: float(round(wi, 4)) for m, wi in zip(available, w)}
    return weights, "skill_fallback"


def blend_forecast(weights: dict[str, float], forecast_values: dict[str, float]) -> float:
    return float(sum(weights[m] * forecast_values[m] for m in weights))


# --------------------------------------------------------------------------
# Vectorized batch blending — used by scripts/train_model.py (calibration
# fitting on the validation split) and scripts/evaluate_blend.py (held-out
# test scoring), so neither one loops per-context in Python or hits the DB
# per row: every model's regressor is called ONCE on a full named DataFrame.
# --------------------------------------------------------------------------
def batch_predict_scores(variable: str, model_name: str, X: pd.DataFrame) -> np.ndarray | None:
    reg = _load_skillscore_model(variable, model_name)
    if reg is None:
        return None
    return reg.predict(X)


def batch_blend(variable: str, feats_by_model: dict[str, pd.DataFrame],
                 values_by_model: dict[str, np.ndarray],
                 fallback_skill_by_model: dict[str, np.ndarray] | None = None,
                 score_models: dict[str, object] | None = None) -> tuple[np.ndarray, dict[str, np.ndarray], str]:
    """
    feats_by_model: {model_name: DataFrame[FEATURE_NAMES]} aligned row-for-row
        across models (same context order for every model).
    values_by_model: {model_name: np.ndarray} of raw forecast values, same order.
    Returns (blended_values, weights_by_model, source).
    """
    models = list(feats_by_model.keys())
    n = len(next(iter(values_by_model.values())))

    score_rows = []
    used_meta_model = True
    for m in models:
        if score_models is not None:
            model = score_models.get(m)
            s = model.predict(feats_by_model[m]) if model is not None else None
        else:
            s = batch_predict_scores(variable, m, feats_by_model[m])
        if s is None:
            used_meta_model = False
            break
        score_rows.append(s)

    if used_meta_model:
        score_matrix = np.vstack(score_rows)  # (n_models, n_rows)
        score_matrix = score_matrix - score_matrix.max(axis=0, keepdims=True)
        exp = np.exp(score_matrix / TEMPERATURE)
        weight_matrix = exp / exp.sum(axis=0, keepdims=True)
        source = "meta_model"
    else:
        if RUNTIME_MODE == "real":
            raise RuntimeError(
                f"REAL model artifacts are incomplete for {variable}; refusing batch fallback in LIVE mode."
            )
        fallback_skill_by_model = fallback_skill_by_model or {}
        raw = np.vstack([np.clip(fallback_skill_by_model.get(m, np.full(n, 0.5)), 1e-3, None) for m in models])
        weight_matrix = raw / raw.sum(axis=0, keepdims=True)
        source = "skill_fallback"

    values_matrix = np.vstack([values_by_model[m] for m in models])
    blended = (weight_matrix * values_matrix).sum(axis=0)
    weights_by_model = {m: weight_matrix[i] for i, m in enumerate(models)}
    return blended, weights_by_model, source
