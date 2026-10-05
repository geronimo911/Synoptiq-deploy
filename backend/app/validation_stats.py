"""Validation residual statistics served from the production database.

The conformal band and the EVT tail in the API are not read from a frozen
artifact: they are recomputed from the *production database's* own validation
rows, so the uncertainty a user sees is derived from the same records the
system was evaluated on.

How it works, per variable:

  1. read the validation-split forecast rows from the `forecasts` table
     (split == "val") and the matching observations from `ground_truth`;
  2. rebuild the production blend for each validation context with the same
     vectorized path the trainer uses (`batch_blend` + `encode`), so the
     residuals belong to the deployed blend, not to an approximation;
  3. residuals = blend - observed; the 90% split-conformal half-width is the
     0.9 quantile of |residual|;
  4. a Generalised Pareto tail is fitted to the |residual| exceedances above
     their own 95th percentile, and separately to the *observed* exceedances
     above the observed 95th percentile, so the response can state how
     unusual the current error and the current forecast magnitude are.

Results are cached per (model version, variable) in memory and on disk under
data/real/validation_stats/, because the computation touches thousands of DB
rows and must never sit in a request's hot path twice. If the database has no
validation rows (a fresh deployment before training data is loaded), the
caller gets an explicit UNAVAILABLE status and must say so — never a silently
substituted number.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from app.config import DATA_DIR, RUNTIME_MODE
from app.blending.peaks import fit_gpd, gpd_exceedance_probability

log = logging.getLogger("synoptiq.validation_stats")

STATS_DIR = DATA_DIR / "validation_stats"
MIN_VALIDATION_CONTEXTS = 30


def _cache_path(variable: str, model_version: str) -> Path:
    safe = model_version.replace("/", "_") or "unversioned"
    return STATS_DIR / f"{variable}__{safe}.json"


def _reconstruct_blend(frame: pd.DataFrame) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """Rebuild the deployed blend for the validation contexts in `frame`.

    `frame` must have one row per (region, valid_time, lead_hours) with the
    three model columns and the context columns the encoder needs.
    """
    from app.blending.blend import batch_blend
    from app.blending.features import encode
    from app.config import MODELS

    feats_by_model = {}
    values_by_model = {}
    for model in MODELS:
        feats = []
        for row in frame.itertuples():
            feats.append(encode(
                lead_hours=int(row.lead_hours),
                regime_probs=row.regime_probs if isinstance(row.regime_probs, dict) else {},
                region=row.region,
                season=row.season or "normal",
                historical_skill=float(row.historical_skill or 0.5),
                disagreement=float(row.disagreement),
                forecast_value=float(getattr(row, model)),
                consensus_deviation=float(getattr(row, model) - row.consensus),
                climatological_anomaly=float(row.climatological_anomaly or 0.0),
            ))
        feats_by_model[model] = pd.concat(feats, ignore_index=True)
        values_by_model[model] = frame[model].to_numpy(dtype=float)

    blended, weights, source = batch_blend(
        variable=frame["variable"].iloc[0],
        feats_by_model=feats_by_model,
        values_by_model=values_by_model,
    )
    return blended, weights


def compute_from_db(db, variable: str, model_version: str) -> dict:
    """Return the residual statistics dict for one variable (cached on disk)."""
    cache = _cache_path(variable, model_version)
    if cache.is_file():
        try:
            payload = json.loads(cache.read_text(encoding="utf-8"))
            payload["cache"] = "disk"
            return payload
        except json.JSONDecodeError:
            log.warning("corrupt validation stats cache at %s; recomputing", cache)

    from app.config import MODELS
    from app.models_db import ForecastRow, GroundTruthRow

    rows = db.query(ForecastRow).filter(
        ForecastRow.variable == variable,
        ForecastRow.split == "val",
    ).all()
    if not rows:
        return {"status": "UNAVAILABLE",
                "reason": "no validation-split rows in the production database",
                "variable": variable}

    frame = pd.DataFrame([{
        "region": r.region, "valid_time": r.valid_time, "lead_hours": r.lead_hours,
        "season": r.season, "regime_probs": r.regime_probs,
        "historical_skill": r.historical_skill_expanding,
        "climatological_anomaly": r.climatological_anomaly_norm,
        "model": r.model, "value": r.forecast_value,
    } for r in rows])
    wide = frame.pivot_table(index=["region", "valid_time", "lead_hours"],
                             columns="model", values="value", aggfunc="first")
    missing = [m for m in MODELS if m not in wide.columns]
    if missing:
        return {"status": "UNAVAILABLE",
                "reason": f"validation rows missing models: {missing}",
                "variable": variable}
    wide = wide.dropna(subset=MODELS).reset_index()
    context = frame.drop_duplicates(["region", "valid_time", "lead_hours"]).set_index(
        ["region", "valid_time", "lead_hours"])
    wide["season"] = wide.set_index(["region", "valid_time", "lead_hours"]).index.map(
        context["season"])
    wide["regime_probs"] = wide.set_index(["region", "valid_time", "lead_hours"]).index.map(
        context["regime_probs"])
    wide["historical_skill"] = wide.set_index(["region", "valid_time", "lead_hours"]).index.map(
        context["historical_skill"])
    wide["climatological_anomaly"] = wide.set_index(
        ["region", "valid_time", "lead_hours"]).index.map(context["climatological_anomaly"])
    wide["variable"] = variable
    wide["consensus"] = wide[MODELS].mean(axis=1)
    wide["disagreement"] = wide[MODELS].std(axis=1)

    truth = db.query(GroundTruthRow).filter(GroundTruthRow.variable == variable).all()
    observed = pd.DataFrame([{"region": t.region, "valid_time": t.valid_time,
                              "observed_value": t.observed_value} for t in truth])
    if observed.empty:
        return {"status": "UNAVAILABLE",
                "reason": "no ground-truth rows in the production database",
                "variable": variable}
    merged = wide.merge(observed, on=["region", "valid_time"], how="inner")
    if len(merged) < MIN_VALIDATION_CONTEXTS:
        return {"status": "UNAVAILABLE",
                "reason": f"only {len(merged)} validation contexts with truth; "
                          f"need >= {MIN_VALIDATION_CONTEXTS}",
                "variable": variable, "n": int(len(merged))}

    blended, _weights = _reconstruct_blend(merged)
    residuals = np.abs(blended - merged["observed_value"].to_numpy(dtype=float))
    q90 = float(np.quantile(residuals, 0.9))
    u_err = float(np.quantile(residuals, 0.95))
    err_tail = fit_gpd(residuals[residuals > u_err] - u_err)

    obs_values = merged["observed_value"].to_numpy(dtype=float)
    u_obs = float(np.quantile(obs_values, 0.95))
    obs_tail = fit_gpd(obs_values[obs_values > u_obs] - u_obs)

    payload = {
        "status": "OK",
        "variable": variable,
        "model_version": model_version,
        "source": "production database (forecasts.split == 'val' joined to ground_truth)",
        "n_validation_contexts": int(len(merged)),
        "conformal": {
            "q90": round(q90, 4),
            "nominal_coverage": 0.9,
            "method": "split conformal, 0.9 quantile of |blend - observed| on the "
                      "validation split",
        },
        "residual_tail": {**err_tail, "threshold_u95": round(u_err, 4)},
        "observed_tail": {**obs_tail, "threshold_u95": round(u_obs, 4)},
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "cache": "computed",
    }
    STATS_DIR.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(payload, indent=1, allow_nan=False), encoding="utf-8")
    return payload


@lru_cache(maxsize=8)
def _cached(variable: str, model_version: str, _db_token: str) -> dict:
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        return compute_from_db(db, variable, model_version)
    finally:
        db.close()


def validation_stats(variable: str, model_version: str) -> dict:
    """Cached accessor. `_db_token` keeps the cache key explicit."""
    return _cached(variable, model_version, model_version)


def exceedance_probability(stats: dict, value: float) -> dict:
    """Return-period context for a forecast magnitude under the observed tail."""
    tail = stats.get("observed_tail") or {}
    xi, sigma, u = tail.get("xi"), tail.get("sigma"), tail.get("threshold_u95")
    if xi is None or sigma is None or u is None:
        return {"status": "NOT_ESTIMABLE",
                "reason": tail.get("note", "observed tail not fitted")}
    y = float(value) - float(u)
    p = gpd_exceedance_probability(y, float(u), float(xi), float(sigma))
    if p is None:
        return {"status": "NOT_ESTIMABLE", "reason": "value beyond the fitted tail support"}
    return {
        "status": "OK",
        "threshold_u95": u,
        "exceedance_probability_given_above_threshold": round(p, 5),
        "interpretation": f"given an observation above {u:.1f}, the chance of exceeding "
                          f"the forecast value {value:.1f} is about {p * 100:.1f}%",
    }
