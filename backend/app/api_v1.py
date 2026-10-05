from __future__ import annotations

import json
import logging
import math
import os
import re
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.config import (ACTIVE_METRICS_DIR, ACTIVE_MODEL_VERSION, CALIBRATION_DIR, LEAD_HOURS,
                        MANIFEST_PATH, MODELS, MODELS_DIR, PILOT_ZONES, RUNTIME_MODE,
                        THRESHOLDS, VARIABLES, LIVE_FRESHNESS_MINUTES,
                        TARGET_RELATIVE_CSI_IMPROVEMENT)
from app.database import get_db
from app.models_db import ForecastRow, GroundTruthRow, LiveForecast
from app.pipeline import run_blend_pipeline
from app.blending.calibration import exceedance_calibration_source
from app.replay import real_test_replay_cases
from app.research import (archived_dates, bulletin_path, cap_alert, impact_for_date, ops_status,
                          peak_preservation, regime_router, research_section,
                          research_summary, rpi_for_date, trust_atlas)
from app.schemas import BlendResponseV2, SystemStatusResponse

router = APIRouter(prefix="/api/v1", tags=["Synoptiq API v1"])
log = logging.getLogger("synoptiq.api")

PUBLIC_REGION = {
    "KWG": "kerala_western_ghats",
    "BOB": "bay_of_bengal_east_coast",
    "IGP": "indo_gangetic_plains",
}
INTERNAL_TO_PUBLIC = {v: k for k, v in PUBLIC_REGION.items()}
REGION_LABELS = {
    "KWG": "Kerala / Western Ghats",
    "BOB": "Bay of Bengal / East Coast",
    "IGP": "Indo-Gangetic Plains / NW India",
}
COASTAL = {"KWG": True, "BOB": True, "IGP": False}
UNITS = {"precipitation": "mm/24h", "temperature": "deg_C", "wind_speed": "km/h"}
MODEL_CYCLE_HOURS = {"GFS": 6, "IFS": 6, "AIFS": 12}
MODEL_PUBLISH_GRACE_HOURS = {"GFS": 4, "IFS": 8, "AIFS": 9}

# How long an ingested live cycle stays operationally current: the model's own
# cycle length plus its publish grace.
#
# This replaces a fixed 45-minute ingestion-age gate, which was wrong for a
# demand-driven (no-cron) deployment: refresh only runs when somebody opens the
# app, so the ingestion age simply measures "time since the last visit". With a
# 45-minute gate the UI declared itself DEGRADED on every quiet afternoon even
# though the forecast cycle it was serving was still perfectly current (GFS
# cycles are 6-hourly, IFS 6-hourly, AIFS 12-hourly). Freshness now tracks the
# real cadence: the cycle we hold must still be the current cycle for its model.
MODEL_FRESHNESS_MINUTES = {
    model: (MODEL_CYCLE_HOURS[model] + MODEL_PUBLISH_GRACE_HOURS[model]) * 60
    for model in MODEL_CYCLE_HOURS
}


def _live_freshness_window_minutes(model: str) -> int:
    """Freshness window for one live provider, in minutes."""
    return MODEL_FRESHNESS_MINUTES.get(model, LIVE_FRESHNESS_MINUTES)


def live_cycle_is_fresh(
    model: str,
    status: str | None,
    source_within_cycle: bool,
    live_age_minutes: int | None,
) -> bool:
    """Is this live provider's cycle still operationally current?

    `source_within_cycle` is the semantic test; the ingestion age is a safety
    net so a cycle superseded several times over is never served as live.
    """
    return bool(
        status == "LIVE"
        and source_within_cycle
        and live_age_minutes is not None
        and live_age_minutes <= _live_freshness_window_minutes(model)
    )


def _latest_ingestion(db):
    """Newest LiveForecast row, tie-broken by id.

    ``refresh_once`` persists a cycle twice with the SAME ``ingestion_time``:
    first a partial marker (so GFS/IFS are served while AIFS is still
    downloading) and then the complete state. Ordering on ``ingestion_time``
    alone is therefore non-deterministic and can return the partial row, which
    is why a successful AIFS fetch could still read back as UNAVAILABLE.
    The complete row is always written last, so it has the higher id.
    """
    return (
        db.query(LiveForecast)
        .order_by(LiveForecast.ingestion_time.desc(), LiveForecast.id.desc())
        .first()
    )


def _optional_metric(value: object) -> float | None:
    if value is None:
        return None
    try:
        metric = float(value)
    except (TypeError, ValueError):
        return None
    return metric if math.isfinite(metric) else None


def _confidence_decay(points: list[dict]) -> list[dict[str, float | int]]:
    series = []
    for point in points:
        trust = _optional_metric(point.get("trust_score"))
        lead_hours = point.get("lead_hours")
        if trust is not None and isinstance(lead_hours, int):
            series.append({"lead_hours": lead_hours, "trust": trust})
    return series


def _internal_region(code: str) -> str:
    if code not in PUBLIC_REGION:
        raise HTTPException(404, f"Unknown region '{code}'. Use KWG, BOB, or IGP.")
    return PUBLIC_REGION[code]


def _public_region(internal: str) -> str:
    return INTERNAL_TO_PUBLIC.get(internal, internal)


def _manifest() -> dict:
    p = MANIFEST_PATH
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _valid_real_manifest(manifest: dict) -> bool:
    if (manifest.get("data_mode") != "real"
            or manifest.get("synthetic_data_used") is not False
            or manifest.get("model_version") != ACTIVE_MODEL_VERSION):
        return False
    provenance = json.dumps({
        key: manifest.get(key)
        for key in ("forecast_sources", "truth_sources", "model_generations", "notes")
    }).lower()
    return "synthetic" not in provenance


def _is_real() -> bool:
    m = _manifest()
    if m.get("data_mode") == "real":
        return _valid_real_manifest(m)
    return any("synthetic" not in str(x).lower() for x in m.get("truth_sources", [])) if m else False


def _require_real_production() -> dict:
    manifest = _manifest()
    if RUNTIME_MODE == "fake":
        return manifest
    if not ACTIVE_MODEL_VERSION or not _valid_real_manifest(manifest):
        raise HTTPException(503, "REAL mode is unavailable: no validated real model manifest is active.")
    return manifest


def _require_live_inputs(db: Session) -> None:
    if RUNTIME_MODE != "real":
        return
    status = system_status(db)
    if status["ready"]:
        return
    raise HTTPException(
        503,
        detail={
            "code": "REAL_INPUTS_UNAVAILABLE",
            "message": "Current real provider cycles are stale, incomplete, or unavailable.",
            "providers": status["providers"],
        },
    )


def _latest_context(db: Session, internal_region: str, variable: str, lead_hours: int,
                    season: str | None = None) -> datetime:
    q = db.query(ForecastRow).filter(
        ForecastRow.region == internal_region,
        ForecastRow.variable == variable,
        ForecastRow.lead_hours == lead_hours,
    )
    if season:
        q = q.filter(ForecastRow.season == season)
    row = q.order_by(ForecastRow.valid_time.desc()).first()
    if not row:
        raise HTTPException(404, "No forecast context is available for this selection.")
    return row.valid_time


def _run_blend_context(
    db: Session,
    region_code: str,
    variable: str,
    lead_hours: int,
    valid_time: datetime | None = None,
    regime_override: str | None = None,
    season_override: str | None = None,
):
    _require_real_production()
    internal = _internal_region(region_code)
    if variable not in VARIABLES:
        raise HTTPException(422, f"Unknown variable '{variable}'.")
    if lead_hours not in LEAD_HOURS:
        raise HTTPException(422, f"Unsupported lead time {lead_hours}. Available: {LEAD_HOURS}")
    if valid_time is None:
        valid_time = _latest_context(db, internal, variable, lead_hours)
    try:
        result = run_blend_pipeline(db, internal, variable, valid_time, lead_hours,
                                    regime_override=regime_override, season_override=season_override)
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return internal, valid_time, result


def _blend_public(db: Session, region_code: str, variable: str, lead_hours: int,
                  valid_time: datetime | None = None, regime_override: str | None = None,
                  season_override: str | None = None):
    internal, valid_time, result = _run_blend_context(
        db, region_code, variable, lead_hours, valid_time,
        regime_override=regime_override, season_override=season_override,
    )
    source_map = {s.model: s for s in result.raw_sources}
    sources = []
    for model in MODELS:
        s = source_map.get(model)
        if s is None:
            continue
        sources.append({
            "model": model,
            "model_generation": getattr(s, "model_generation", None),
            "forecast_value": s.forecast_value,
            "weight": s.model_weight,
            "historical_skill": s.historical_skill,
        })

    explanations = []
    for w in result.weights:
        for d in w.top_drivers[:3]:
            contribution = float(d.get("contribution", 0.0))
            direction = "increases_weight" if contribution >= 0 else "decreases_weight"
            explanations.append({
                "feature": d.get("feature", "unknown"),
                "contribution": contribution,
                "direction": direction,
                "detail": d.get("readable", d.get("feature", "")),
            })

    # BlendedForecastResponse stores the detailed signals indirectly only through
    # the computed score, so reconstruct the five public components from the same
    # weights used in app.blending.trust.
    from app.blending.trust import compute_trust
    hist = {s.model: float(s.historical_skill or 0.5) for s in result.raw_sources}
    weights = {w.model: w.weight for w in result.weights}
    trust = compute_trust(
        variable=variable,
        weights=weights,
        historical_skill_by_model=hist,
        disagreement=result.disagreement,
        lead_hours=lead_hours,
        regime_probs=result.regime_probs,
        n_sources_available=len(result.raw_sources),
        n_sources_expected=len(MODELS),
        context_features=None,
    )

    context_row = db.query(ForecastRow).filter(
        ForecastRow.region == internal,
        ForecastRow.variable == variable,
        ForecastRow.lead_hours == lead_hours,
        ForecastRow.valid_time == valid_time,
    ).first()
    season = (season_override or (context_row.season if context_row else None) or "normal")
    run_time = result.raw_sources[0].run_time.isoformat() if result.raw_sources else valid_time.isoformat()
    bust_flag = result.bust_probability > 0.6
    manifest = _require_real_production()
    provenance = {
        "GFS": "NOAA GFS",
        "IFS": "ECMWF IFS",
        "AIFS": "ECMWF AIFS",
        "regime_detector": "rule-based context detector",
        "explanation_backend": "SHAP TreeExplainer",
        "data_mode": manifest.get("data_mode", RUNTIME_MODE),
    }

    return {
        "region": region_code,
        "region_name": REGION_LABELS[region_code],
        "variable": variable,
        "unit": UNITS[variable],
        "run_time": run_time,
        "valid_time": result.valid_time.isoformat(),
        "lead_hours": lead_hours,
        "season": season,
        "regime": result.regime,
        "regime_probs": result.regime_probs,
        "sources": sources,
        "disagreement": result.disagreement,
        "raw_blend_value": result.blended_value_raw,
        "bias_corrected_value": result.blended_value_calibrated,
        "final_value": result.blended_value_calibrated,
        "calibration": {
            "status": "CALIBRATED" if len(sources) == len(MODELS) else "NOT_AVAILABLE",
            "reason": None if len(sources) == len(MODELS)
            else "The calibration artifacts were fitted on the complete three-source blend.",
        },
        "trust": {
            "historical_skill_component": trust["signals"]["historical_skill"],
            "disagreement_component": trust["signals"]["model_agreement"],
            "lead_time_component": trust["signals"]["lead_time_confidence"],
            "regime_stability_component": trust["signals"]["regime_stability"],
            "data_quality_component": trust["signals"]["data_quality"],
            "trust_score": result.trust_score,
        },
        "bust_probability": result.bust_probability,
        "bust_flag": bust_flag,
        "abstain": result.abstain,
        "explanation": explanations,
        "fallback_used": len(sources) < len(MODELS),
        "provenance": provenance,
        "peak_preservation": _peak_layer(variable, sources, result, db=db),
        "regime_routing": _regime_routing(result.regime, result.regime_probs),
        "weight_policy": _weight_policy(variable, result.regime, weights),
        "synoptic_regime": {
            "regime": result.regime,
            "label": _regime_label(result.regime),
            "regime_probs": result.regime_probs,
        },
        "confidence_interval_90": _confidence_block(variable, result.blended_value_calibrated),
        "validation_statistics": _validation_block(variable),
        "arithmetic": {
            "note": "display-only reconstruction of the exact arithmetic from "
                    "the same snapshot values; computed here, not by the frontend",
            "terms": [
                {"model": s["model"],
                 "value": s["forecast_value"],
                 "weight": s["weight"],
                 "contribution": round(float(s["forecast_value"]) * float(s["weight"]), 4)}
                for s in sources
            ],
            "weighted_sum": round(
                sum(float(s["forecast_value"]) * float(s["weight"]) for s in sources), 4),
            "raw_blend_value": result.blended_value_raw,
            "bias_correction": round(
                float(result.blended_value_calibrated) - float(result.blended_value_raw), 4),
            "final_value": result.blended_value_calibrated,
        },
    }




def _regime_label(regime: str) -> str:
    from app.regimes import describe_regime
    return describe_regime(regime)["label"]


def _validation_block(variable: str) -> dict:
    """DB-derived validation residual statistics (cached), for transparency."""
    from app.validation_stats import validation_stats
    try:
        stats = validation_stats(variable, ACTIVE_MODEL_VERSION or "unversioned")
    except Exception as exc:  # never break a forecast on a stats problem
        return {"status": "ERROR", "reason": f"{type(exc).__name__}: {exc}"}
    return {
        "status": stats.get("status"),
        "source": stats.get("source"),
        "n_validation_contexts": stats.get("n_validation_contexts"),
        "conformal": stats.get("conformal"),
        "residual_tail": stats.get("residual_tail"),
        "observed_tail": stats.get("observed_tail"),
        "reason": stats.get("reason"),
    }


def _confidence_block(variable: str, value: float) -> dict:
    """90% prediction interval for this exact forecast.

    Preference order, always stated in the response:
      1. split-conformal half-width computed from the production database's
         own validation residuals (the live, self-updating source);
      2. the artifact-calibrated q90 when the database has no validation rows.
    """
    from app.validation_stats import exceedance_probability, validation_stats
    band = None
    source = None
    try:
        stats = validation_stats(variable, ACTIVE_MODEL_VERSION or "unversioned")
        if stats.get("status") == "OK" and stats.get("conformal", {}).get("q90"):
            q90 = float(stats["conformal"]["q90"])
            band = {"level": 0.9, "low": round(value - q90, 3), "high": round(value + q90, 3),
                    "half_width": round(q90, 3),
                    "method": "split_conformal_production_db_validation_residuals",
                    "n_calibration_contexts": stats.get("n_validation_contexts")}
            source = "production_database"
            tail = exceedance_probability(stats, value)
        else:
            tail = {"status": "NOT_ESTIMABLE",
                    "reason": stats.get("reason", "validation statistics unavailable")}
    except Exception as exc:
        tail = {"status": "NOT_ESTIMABLE", "reason": f"{type(exc).__name__}: {exc}"}
    if band is None:
        try:
            entry = peak_preservation()["variables"].get(variable, {})
            q90 = (entry.get("conformal") or {}).get("q90")
            if q90:
                band = {"level": 0.9, "low": round(value - q90, 3),
                        "high": round(value + q90, 3), "half_width": round(q90, 3),
                        "method": "split_conformal_artifact_validation_residuals"}
                source = "artifact"
        except HTTPException:
            pass
    return {"interval_90": band, "source": source, "tail_context": tail}


def _weight_policy(variable: str, regime: str, meta_weights: dict) -> dict:
    """The weighting policy actually applied, with its measured justification.

    The router's regime-mean weights are available and reported, but the
    measured optimum on the untouched test split is lambda=0 (the meta-model
    already receives regime probabilities as features, so it is already
    regime-conditioned; substituting the coarser regime-mean weights costs
    accuracy — see the regime_router artifact's weight_policy_measurement).
    SYNOPTIQ_REGIME_WEIGHT_LAMBDA overrides the mixing for experiments.
    """
    out = {"applied_lambda": 0.0, "source": "meta_model",
           "meta_model_weights": {k: round(float(v), 4) for k, v in meta_weights.items()}}
    try:
        router = regime_router()
        measurement = router.get("weight_policy_measurement", {})
        measured = measurement.get("recommended_lambda")
        row = next((r for r in measurement.get("rows", []) if r["variable"] == variable), None)
        out["measured_optimum_lambda"] = measured
        out["measurement"] = {
            "mae_by_lambda": row.get("mae_by_lambda") if row else None,
            "router_only_would_cost_mae": row.get("router_only_lambda_1") if row else None,
            "meta_model_mae": row.get("meta_model_lambda_0") if row else None,
        }
        profile = next((p for p in router.get("profiles", []) if p["regime"] == regime), None)
        if profile:
            out["router_regime_weights"] = profile.get("mean_weights")
        env = os.getenv("SYNOPTIQ_REGIME_WEIGHT_LAMBDA")
        lam = float(env) if env not in (None, "") else float(measured or 0.0)
        out["applied_lambda"] = round(lam, 3)
        out["source"] = "router_blend" if lam > 0 else "meta_model"
        out["note"] = ("router weights blended in at lambda=%.2f" % lam) if lam > 0 else (
            "meta-model weights applied (measured optimum); router weights are "
            "reported alongside for audit")
    except HTTPException as exc:
        out["status"] = "ARTIFACT_UNAVAILABLE"
        out["note"] = str(exc.detail)[:200]
    return out


def _peak_layer(variable: str, sources: list[dict], result, db=None) -> dict:
    """Peak-preservation layer for this exact context (display + audit).

    alpha is the value calibrated on the validation split for this variable;
    it is modulated upward only by live bust probability and inter-model
    disagreement (see app.blending.peaks.alpha_for_context). When the
    calibrated alpha is zero the layer is reported as INACTIVE and the base
    blend is returned unchanged — the response never silently sharpens a
    forecast that the validation data did not justify.
    """
    from app.blending.peaks import (alpha_for_context, conformal_band,
                                    peak_preserving_value)
    try:
        peaks = peak_preservation()
        entry = peaks.get("variables", {}).get(variable, {})
        base_alpha = float(entry.get("alpha") or 0.0)
    except HTTPException:
        return {"status": "ARTIFACT_UNAVAILABLE",
                "note": "run backend/scripts/build_advanced_artifacts.py to enable "
                        "the peak-preservation layer"}
    values = {s["model"]: float(s["forecast_value"]) for s in sources}
    if not values:
        return {"status": "NO_SOURCES"}
    sharpest = max(values.values())
    alpha = alpha_for_context(base_alpha,
                              bust_probability=float(result.bust_probability or 0.0),
                              disagreement=float(result.disagreement or 0.0))
    base = float(result.blended_value_calibrated)
    value = peak_preserving_value(base, sharpest, alpha)
    q90 = entry.get("conformal", {}).get("q90")
    if db is not None:
        try:
            from app.validation_stats import validation_stats
            stats = validation_stats(variable, ACTIVE_MODEL_VERSION or "unversioned")
            if stats.get("status") == "OK" and stats.get("conformal", {}).get("q90"):
                q90 = float(stats["conformal"]["q90"])
        except Exception:
            pass  # fall back to the artifact q90, already loaded
    return {
        "status": "ACTIVE" if alpha > 0 else "INACTIVE",
        "calibrated_alpha": base_alpha,
        "effective_alpha": round(alpha, 3),
        "base_value": round(base, 3),
        "sharpest_model_value": round(sharpest, 3),
        "peak_preserved_value": round(value, 3),
        "applied": abs(value - base) > 1e-9,
        "conformal_interval": conformal_band(value, q90) if q90 else None,
        "note": entry.get("conclusion", ""),
    }


def _regime_routing(regime: str, regime_probs: dict) -> dict:
    """Explicit statement of how the active synoptic regime moved the policy."""
    from app.regimes import describe_regime, policy_shift
    try:
        router = regime_router()
    except HTTPException:
        return {"regime": regime, **describe_regime(regime),
                "status": "ARTIFACT_UNAVAILABLE"}
    profile = next((p for p in router.get("profiles", []) if p["regime"] == regime), None)
    out = {"regime": regime, **describe_regime(regime),
           "regime_probs": regime_probs, "status": "OK"}
    if profile:
        out["mean_weights_in_regime"] = profile.get("mean_weights")
        out["best_test_model_in_regime"] = profile.get("best_test_model")
        out["policy_shift"] = profile.get("policy_shift", {}).get("statement")
    else:
        out["policy_shift"] = policy_shift(
            regime, {}, router.get("global_mean_weights", {})).get("statement")
    return out


@router.get("/health")
def health(db: Session = Depends(get_db)):
    n = db.query(ForecastRow).count()
    manifest = _manifest()
    skill_dir = MODELS_DIR / "skill"
    bust_dir = MODELS_DIR / "bust"
    calibration_dir = CALIBRATION_DIR
    skill_models = len(list(skill_dir.glob("skillscore_*.joblib")))
    bust_models = sum((bust_dir / f"bust_{variable}.joblib").exists() for variable in VARIABLES)
    from app.models_db import ReplayEvent
    replay_exists = db.query(ReplayEvent).count() > 0
    return {
        "status": "ok" if n and (RUNTIME_MODE == "fake" or manifest.get("data_mode") == "real") else "not_ready",
        "historical_data_cached": n > 0,
        "blend_model_trained": skill_models >= 9,
        "bust_model_trained": bust_models >= 3,
        "replay_events_cached": replay_exists,
        "data_mode": manifest.get("data_mode", RUNTIME_MODE),
        "model_version": manifest.get("model_version", ACTIVE_MODEL_VERSION or None),
        "forecast_rows_cached": n,
        "calibration_artifacts": len(list(calibration_dir.glob("*.joblib"))),
    }


@router.get("/system/status", response_model=SystemStatusResponse)
def system_status(db: Session = Depends(get_db)):
    """Expose deployment readiness without inventing operational values."""
    manifest = _manifest()
    skill_dir = MODELS_DIR / "skill"
    bust_dir = MODELS_DIR / "bust"
    calibration_dir = CALIBRATION_DIR
    skill_count = len(list(skill_dir.glob("skillscore_*.joblib")))
    bust_count = sum((bust_dir / f"bust_{variable}.joblib").exists() for variable in VARIABLES)
    calibration_count = len(list(calibration_dir.glob("*.joblib")))
    latest = db.query(ForecastRow).order_by(ForecastRow.run_time.desc()).first()
    latest_ingestion = _latest_ingestion(db)
    latest_verification = db.query(GroundTruthRow).order_by(GroundTruthRow.valid_time.desc()).first()
    latest_by_model = {
        model: db.query(ForecastRow.run_time)
        .filter(ForecastRow.model == model)
        .order_by(ForecastRow.run_time.desc()).first()
        for model in MODELS
    }
    real_manifest = RUNTIME_MODE == "fake" or _valid_real_manifest(manifest)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    providers = {}
    for model, latest_run_row in latest_by_model.items():
        latest_run = latest_run_row[0] if latest_run_row else None
        latest_available = None
        latest_run_utc = (
            latest_run.replace(tzinfo=timezone.utc)
            if latest_run and latest_run.tzinfo is None
            else latest_run.astimezone(timezone.utc) if latest_run else None
        )
        is_real = RUNTIME_MODE == "real" and real_manifest
        if RUNTIME_MODE != "real":
            status, validation, reason = "UNAVAILABLE", "NOT_EVALUATED", "Provider checks are disabled in explicit fake mode."
        elif not real_manifest:
            status, validation, reason = "UNAVAILABLE", "NOT_EVALUATED", "The active real artifact manifest is not validated."
        elif latest_run is None:
            status, validation, reason = "UNAVAILABLE", "NO_RECORDS", "No forecast run is recorded for this source."
        else:
            rows = db.query(ForecastRow).filter(
                ForecastRow.model == model, ForecastRow.run_time == latest_run
            ).all()
            values = [row.forecast_value for row in rows]
            has_invalid_values = any(
                value is None or not math.isfinite(float(value)) for value in values
            )
            source_time = latest_run_utc.replace(tzinfo=None)
            age = now - source_time
            expected_coverage = {
                (region, variable, lead)
                for region in PILOT_ZONES
                for variable in VARIABLES
                for lead in LEAD_HOURS
            }
            observed_coverage = {
                (row.region, row.variable, row.lead_hours) for row in rows
            }
            latest_available = max((row.valid_time for row in rows), default=None)
            freshness_window = timedelta(
                hours=MODEL_CYCLE_HOURS[model] + MODEL_PUBLISH_GRACE_HOURS[model]
            )
            if age < timedelta(0):
                status, validation, reason = "INVALID", "FUTURE_RUN_TIME", "The recorded provider run time is in the future relative to UTC."
            elif age > freshness_window:
                status, validation, reason = "STALE", "STALE", f"The latest recorded source cycle exceeds the {freshness_window.total_seconds() / 3600:g}-hour update-cycle freshness limit."
            elif has_invalid_values:
                status, validation, reason = "INVALID", "NON_FINITE", "The latest recorded source cycle contains non-finite forecast values."
            elif observed_coverage != expected_coverage:
                status, validation, reason = "INVALID", "INCOMPLETE_HORIZON", "The latest recorded source cycle is missing a required region, variable, or lead."
            else:
                status, validation, reason = "LIVE", "PASS", "The latest recorded source cycle is finite, complete, and within its provider update-cycle freshness limit."
        providers[model] = {
            "status": status,
            "validation_result": validation,
            "run_time": latest_run_utc.isoformat() if latest_run_utc else None,
            "latest_available_time": latest_available.isoformat() if latest_available else None,
            "age_minutes": max(0, int((now - latest_run_utc.replace(tzinfo=None)).total_seconds() / 60)) if latest_run_utc else None,
            "validation_reason": reason,
            "timestamp": latest_run_utc.isoformat() if latest_run_utc else None,
            "reason": reason,
            "is_real": is_real,
        }
    live_provider_count = sum(
        provider["status"] == "LIVE" and provider["is_real"]
        for provider in providers.values()
    )
    live_payload = latest_ingestion.payload if latest_ingestion and isinstance(latest_ingestion.payload, dict) else {}
    live_states = live_payload.get("providers", {}) if isinstance(live_payload, dict) else {}
    live_age_minutes = None
    if latest_ingestion:
        live_age_minutes = max(0, int((now - latest_ingestion.ingestion_time).total_seconds() / 60))
    if live_states:
        live_provider_count = 0
        for model in MODELS:
            state = live_states.get(model, {})
            source_timestamp = state.get("source_run_time") or state.get("retrieved_at")
            source_age_minutes = None
            source_within_cycle = True
            if source_timestamp:
                try:
                    source_time = datetime.fromisoformat(source_timestamp)
                    if source_time.tzinfo is not None:
                        source_time = source_time.astimezone(timezone.utc).replace(tzinfo=None)
                    source_age = now - source_time
                    source_age_minutes = int(source_age.total_seconds() / 60)
                    source_within_cycle = timedelta(0) <= source_age <= timedelta(
                        hours=MODEL_CYCLE_HOURS[model] + MODEL_PUBLISH_GRACE_HOURS[model]
                    )
                except (TypeError, ValueError):
                    source_within_cycle = False
            # Cycle-aware freshness. `source_within_cycle` is the semantic
            # test (is this still the current cycle for this model?); the
            # ingestion age is only a safety net so we never serve data from a
            # cycle that has been superseded several times over.
            fresh = live_cycle_is_fresh(
                model, state.get("status"), source_within_cycle, live_age_minutes
            )
            if fresh:
                live_provider_count += 1
            providers[model].update({
                "status": "LIVE" if fresh else ("STALE" if state.get("status") == "LIVE" else state.get("status", "UNAVAILABLE")),
                "validation_result": "PASS" if fresh else state.get("reason", "live provider unavailable"),
                "fresh": fresh,
                "complete": bool(state.get("complete", False)),
                "finite": bool(state.get("finite", False)),
                "source": state.get("source"),
                "source_model": state.get("source_model"),
                "source_transport": state.get("source_transport"),
                "retrieved_at": state.get("retrieved_at"),
                "source_run_time": state.get("source_run_time"),
                "run_time_basis": state.get("run_time_basis"),
                "coverage": state.get("coverage", {}),
                "fallback_used": bool(state.get("fallback_used", False)),
                "age_minutes": source_age_minutes if source_age_minutes is not None else live_age_minutes,
                "reason": state.get("reason", "fresh validated real provider cycle" if fresh else "live cycle is stale"),
            })
    if live_provider_count == len(MODELS):
        live_state = "REAL LIVE"
    elif live_provider_count >= 2:
        live_state = "DEGRADED REAL"
    else:
        live_state = "REAL INPUTS UNAVAILABLE"
    return {
        "mode": RUNTIME_MODE,
        "live_state": live_state,
        "ready": bool(
            real_manifest
            and skill_dir.exists() and bust_dir.exists()
            and calibration_dir.exists()
            and skill_count >= 9 and bust_count >= 3 and calibration_count > 0
            and (
                (RUNTIME_MODE == "fake" and all(latest_by_model.values()))
                or (RUNTIME_MODE == "real" and live_provider_count >= 2)
            )
        ),
        "active_model_version": ACTIVE_MODEL_VERSION or None,
        "training_period": manifest.get("training_period"),
        "last_successful_ingestion_time": latest_ingestion.ingestion_time.isoformat() if latest_ingestion else None,
        "latest_source_run": latest.run_time.isoformat() if latest else None,
        "latest_source_runs": {
            model: row[0].isoformat() if row else None
            for model, row in latest_by_model.items()
        },
        "providers": providers,
        "supported_lead_times": LEAD_HOURS,
        "test_period": manifest.get("test_period"),
        "last_verification_update": latest_verification.valid_time.isoformat() if latest_verification else None,
        "database": {"status": "connected", "forecast_rows": db.query(ForecastRow).count()},
        "artifacts": {
            "models": skill_dir.exists() and bust_dir.exists(),
            "calibration": calibration_dir.exists(),
            "skill_model_count": skill_count,
            "bust_model_count": bust_count,
            "calibration_count": calibration_count,
        },
    }


@router.get("/regions")
def regions():
    return [
        {
            "code": code,
            "name": REGION_LABELS[code],
            "lat": round(sum(PILOT_ZONES[internal]["lat_range"]) / 2, 3),
            "lon": round(sum(PILOT_ZONES[internal]["lon_range"]) / 2, 3),
            "emphasis": PILOT_ZONES[internal]["emphasis"],
            "coastal": COASTAL[code],
        }
        for code, internal in PUBLIC_REGION.items()
    ]


@router.get("/sources")
def sources():
    return {
        "GFS": {"kind": "nwp", "provider": "NOAA", "role": "global forecast source"},
        "IFS": {"kind": "nwp", "provider": "ECMWF", "role": "global forecast source"},
        "AIFS": {"kind": "ai_forecast", "provider": "ECMWF", "role": "machine-learning forecast source"},
    }


@router.get("/variables")
def variables():
    return [
        {"variable": v, "unit": UNITS[v], "extreme_threshold": next(
            x for k, x in THRESHOLDS[v].items() if k != "unit"
        )}
        for v in VARIABLES
    ]


@router.get("/lead-times")
def lead_times():
    return LEAD_HOURS


@router.get("/forecast/blend", response_model=BlendResponseV2)
def forecast_blend(region: str, variable: str, lead_hours: int,
                   valid_time: datetime | None = None, db: Session = Depends(get_db)):
    """Blended forecast with confidence interval, synoptic regime, weight
    policy and peak-preservation state. The 90% interval and the EVT tail are
    recomputed from the production database's validation residuals."""
    return _blend_public(db, region, variable, lead_hours, valid_time)


@router.get("/weights/map")
def weights_map(region: str, variable: str, season: str, regime: str,
                db: Session = Depends(get_db)):
    internal = _internal_region(region)
    if variable not in VARIABLES:
        raise HTTPException(422, f"Unknown variable '{variable}'.")
    _require_real_production()
    points = []
    for lead in LEAD_HOURS:
        try:
            # The requested season/regime describe the context we want to
            # simulate, not necessarily the season/regime stored on the
            # selected forecast row. Use the latest available forecast for
            # the region/variable/lead and override the context in the
            # blending pipeline.
            vt = _latest_context(db, internal, variable, lead)
        except HTTPException as exc:
            if exc.status_code == 404:
                continue
            raise
        try:
            _, _, result = _run_blend_context(
                db, region, variable, lead, vt,
                regime_override=regime, season_override=season,
            )
        except HTTPException as exc:
            if exc.status_code == 404:
                continue
            raise
        points.append({
            "lead_hours": lead,
            "weights": {weight.model: _optional_metric(weight.weight) for weight in result.weights},
            "trust_score": _optional_metric(result.trust_score),
        })
    if not points:
        raise HTTPException(404, "No forecast contexts match the requested season/regime.")
    return {
        "region": region,
        "variable": variable,
        "regime": regime,
        "season": season,
        "points": points,
        "confidence_decay": _confidence_decay(points),
    }


@router.get("/skill/verification")
def verification(region: str | None = None, variable: str | None = None):
    rows = []
    for path in sorted(ACTIVE_METRICS_DIR.glob("blend_eval_*_*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        internal = payload.get("region")
        var = payload.get("variable")
        public = _public_region(internal)
        if region and public != region:
            continue
        if variable and var != variable:
            continue
        contexts = payload.get("n_test_contexts")
        try:
            test_contexts = int(contexts) if contexts is not None else None
        except (TypeError, ValueError):
            test_contexts = None

        if var == "precipitation":
            best = _optional_metric(payload.get("best_single_model_csi"))
            skill = _optional_metric(payload.get("synoptiq_csi"))
            rel_pct = _optional_metric(payload.get("relative_csi_improvement_pct"))
            rel = rel_pct / 100.0 if rel_pct is not None else None
            metric = str(payload.get("metric", ""))
            metric_threshold = re.search(r"CSI@\s*(\d+(?:\.\d+)?)\s*mm", metric, re.IGNORECASE)
            threshold = (
                float(metric_threshold.group(1))
                if metric_threshold
                else payload.get("threshold")
            )
            rows.append({
                "region": public,
                "variable": var,
                "metric": metric or (f"CSI@{float(threshold):g}mm" if threshold is not None else "CSI"),
                "threshold": float(threshold) if threshold is not None else None,
                "best_single_model": payload.get("best_single_model", "unknown"),
                "best_single_model_score": best,
                "synoptiq_score": skill,
                "relative_improvement": rel,
                "meets_target": (
                    rel >= TARGET_RELATIVE_CSI_IMPROVEMENT if rel is not None else None
                ),
                "target_relative_improvement": TARGET_RELATIVE_CSI_IMPROVEMENT,
                "best_single_model_csi": best,
                "synoptiq_csi": skill,
                "relative_csi_improvement": rel,
                "test_contexts": test_contexts,
            })
        else:
            best = _optional_metric(payload.get("best_single_model_rmse"))
            skill = _optional_metric(payload.get("synoptiq_rmse"))
            rel_pct = _optional_metric(payload.get("relative_rmse_improvement_pct"))
            rel = rel_pct / 100.0 if rel_pct is not None else None
            rows.append({
                "region": public,
                "variable": var,
                "metric": payload.get("metric", "RMSE"),
                "threshold": None,
                "best_single_model": payload.get("best_single_model", "unknown"),
                "best_single_model_score": best,
                "synoptiq_score": skill,
                "relative_improvement": rel,
                "meets_target": None,
                "target_relative_improvement": None,
                "best_single_model_csi": None,
                "synoptiq_csi": None,
                "relative_csi_improvement": None,
                "test_contexts": test_contexts,
            })
    return rows


@router.get("/extreme/guidance")
def extreme_guidance(region: str, lead_hours: int, db: Session = Depends(get_db)):
    internal = _internal_region(region)
    if lead_hours not in LEAD_HOURS:
        raise HTTPException(422, f"Unsupported lead time {lead_hours}. Available: {LEAD_HOURS}")
    _require_real_production()
    guidance = []
    valid_time = None
    for variable in VARIABLES:
        try:
            current_time = _latest_context(db, internal, variable, lead_hours)
        except HTTPException as exc:
            if exc.status_code == 404:
                continue
            raise
        try:
            _, current_time, internal_result = _run_blend_context(
                db, region, variable, lead_hours, current_time,
            )
        except HTTPException as exc:
            if exc.status_code == 404:
                continue
            raise
        if valid_time is None:
            valid_time = current_time
        threshold_key = next(k for k in THRESHOLDS[variable] if k != "unit")
        probability = internal_result.exceedance_probabilities.get(threshold_key)
        calibration_source = exceedance_calibration_source(variable, threshold_key, internal)
        calibrated = (
            len(internal_result.raw_sources) == len(MODELS)
            and calibration_source is not None
            and probability is not None
            and math.isfinite(float(probability))
        )
        forecast_value = internal_result.blended_value_calibrated
        probability_reason = None if calibrated else (
            "Insufficient real calibration events in the non-test calibration set."
        )
        source_values = [
            float(source.forecast_value)
            for source in internal_result.raw_sources
            if source.forecast_value is not None
        ]
        bust_probability = internal_result.bust_probability
        bust_flag = float(bust_probability) > 0.6 if bust_probability is not None else None
        guidance.append({
            "variable": variable,
            "threshold": THRESHOLDS[variable][threshold_key],
            "unit": THRESHOLDS[variable]["unit"],
            "probability": float(probability) if calibrated else None,
            "calibrated": calibrated,
            "calibration_status": "CALIBRATED" if calibrated else "WITHHELD",
            "calibration_source": calibration_source if calibrated else None,
            "probability_reason": probability_reason,
            "forecast_value": forecast_value,
            "threshold_exceeded": forecast_value >= THRESHOLDS[variable][threshold_key],
            "trust_score": internal_result.trust_score,
            "bust_probability": bust_probability,
            "bust_flag": bust_flag,
            "disagreement": internal_result.disagreement,
            "source_range": (
                {"minimum": min(source_values), "maximum": max(source_values)}
                if source_values else None
            ),
        })
    if not guidance:
        raise HTTPException(404, "No forecast data available for extreme guidance.")
    return {"region": region, "lead_hours": lead_hours, "valid_time": valid_time.isoformat() if valid_time else None,
            "guidance": guidance}


@router.get("/replay/events")
def replay_events(db: Session = Depends(get_db)):
    from app.models_db import ReplayEvent
    events = db.query(ReplayEvent).order_by(ReplayEvent.valid_time.desc()).all()
    out = []
    event_ids = set()
    for summary, _ in real_test_replay_cases():
        event_ids.add(summary["event_id"])
        out.append({**summary, "region": _public_region(summary["region"])})
    for e in events:
        payload = e.payload or {}
        headline = payload.get("headline")
        if not headline:
            headline = "Historical replay case"
        if e.event_id in event_ids:
            continue
        out.append({
            "event_id": e.event_id,
            "region": _public_region(e.region),
            "variable": e.variable,
            "valid_time": e.valid_time.isoformat(),
            "label": e.label,
            "headline": headline,
            "outcome": payload.get("outcome", "MIXED"),
            "severity": e.severity,
        })
    out.sort(key=lambda event: event["valid_time"], reverse=True)
    return out


@router.get("/replay/events/{event_id}")
def replay_event(event_id: str, db: Session = Depends(get_db)):
    from app.models_db import ReplayEvent
    e = db.get(ReplayEvent, event_id)
    if not e:
        for summary, detail in real_test_replay_cases():
            if summary["event_id"] == event_id:
                payload = dict(detail)
                payload["region"] = _public_region(payload["region"])
                return payload
        raise HTTPException(404, f"Replay event '{event_id}' not found")
    payload = dict(e.payload or {})
    payload.setdefault("event_id", e.event_id)
    payload["region"] = _public_region(payload.get("region", e.region))
    payload.setdefault("variable", e.variable)
    payload.setdefault("valid_time", e.valid_time.isoformat())
    payload.setdefault("label", e.label)
    payload.setdefault("headline", payload["label"] or "Historical replay case")
    payload.setdefault("unit", UNITS.get(e.variable, ""))

    observed = payload.get("reference_value", payload.get("observed_value"))
    if observed is not None and math.isfinite(float(observed)):
        payload["reference_value"] = float(observed)

    raw_sources = payload.get("raw_sources")
    if not isinstance(raw_sources, list):
        raw_sources = []
    normalized_sources = []
    for source in raw_sources:
        if not isinstance(source, dict):
            continue
        normalized = dict(source)
        normalized.setdefault("weight", normalized.get("model_weight"))
        normalized_sources.append(normalized)
    payload["raw_sources"] = normalized_sources

    if "naive_average" not in payload and normalized_sources:
        source_values = [float(source["forecast_value"]) for source in normalized_sources]
        payload["naive_average"] = sum(source_values) / len(source_values)

    choice = payload.get("single_model_choice")
    if not isinstance(choice, dict):
        eligible_sources = [
            source for source in normalized_sources
            if source.get("historical_skill") is not None
            and math.isfinite(float(source["historical_skill"]))
            and source.get("forecast_value") is not None
            and math.isfinite(float(source["forecast_value"]))
        ]
        if eligible_sources:
            best_source = max(eligible_sources, key=lambda source: float(source["historical_skill"]))
            choice = {
                "model": best_source["model"],
                "forecast_value": float(best_source["forecast_value"]),
            }
            payload["single_model_choice"] = choice

    if payload.get("reference_value") is not None:
        reference = float(payload["reference_value"])
        blended_value = payload.get(
            "synoptiq_blend",
            payload.get("blended_value_calibrated", payload.get("blended_value_raw")),
        )
        if blended_value is not None and math.isfinite(float(blended_value)):
            payload["synoptiq_blend"] = float(blended_value)
            payload.setdefault("synoptiq_error", abs(float(blended_value) - reference))
        if choice and choice.get("forecast_value") is not None:
            payload.setdefault(
                "single_model_error",
                abs(float(choice["forecast_value"]) - reference),
            )
        if payload.get("naive_average") is not None:
            payload.setdefault(
                "naive_average_error",
                abs(float(payload["naive_average"]) - reference),
            )

    narrative = payload.get("narrative")
    if isinstance(narrative, str):
        payload["narrative"] = [narrative]
    elif not isinstance(narrative, list):
        payload["narrative"] = []
    return payload


# ---------------------------------------------------------------------------
# Research & verification suite (read-only, from the archived REAL_12M record)
# ---------------------------------------------------------------------------

@router.get("/research/summary")
def research_overview():
    """Suite provenance + headline numbers (calibration sanity, oracle regret)."""
    return research_summary()


@router.get("/research/baselines")
def research_baselines():
    """Persistence, climatology, per-model and naive-average baselines with
    cluster-bootstrap 95% confidence intervals on the headline improvements."""
    return research_section("baselines")


@router.get("/research/hard-days")
def research_hard_days():
    """Error on easy (models agreed) vs hard (models split) days."""
    return research_section("hard_days")


@router.get("/research/oracle")
def research_oracle():
    """Hindsight-oracle regret analysis: how much of the oracle's advantage
    over the best fixed single model the blend recovers."""
    return research_section("oracle")


@router.get("/research/ai-ablation")
def research_ai_ablation():
    """GFS+IFS physics-only blend vs the full AI-including blend."""
    return research_section("ai_ablation")


@router.get("/research/imd-classes")
def research_imd_classes():
    """IMD rainfall-class (15.6 / 64.5 / 115.5 mm) POD / FAR / CSI."""
    return research_section("imd_classes")


@router.get("/research/trust-audit")
def research_trust_audit():
    """Observed error by issued trust tier, abstention audit, rank correlation."""
    return research_section("trust_audit")


@router.get("/research/diagnostics")
def research_diagnostics():
    """Lead-time crossovers, rolling skill evolution, model bias fingerprints."""
    return {k: research_section(k) for k in
            ("crossovers", "skill_evolution", "bias_fingerprints")}


@router.get("/alerts/rpi")
def alerts_rpi(date: str | None = None):
    """Risk Priority Index over the latest archived cycle, or any archived
    day via ?date=YYYY-MM-DD (impact classes follow IMD terminology)."""
    return rpi_for_date(date)


@router.get("/archive/dates")
def archive_dates():
    """Dates available to archived-day controls in operational pages."""
    return archived_dates()


# ---------------------------------------------------------------------------
# Model Trust Atlas — the PS-mandated "model weight map"
# ---------------------------------------------------------------------------

@router.get("/trust/atlas")
def model_trust_atlas(region: str | None = None, variable: str | None = None):
    """Which model is trusted where and why: issued weights, measured
    test skill and the regime breakdown for every region x variable x lead
    cell, plus a region x lead dominating-model matrix for the map."""
    atlas = trust_atlas()
    cells = atlas["cells"]
    if region:
        cells = [c for c in cells if c["region"] == _internal_region(region)]
    if variable:
        cells = [c for c in cells if c["variable"] == variable]
    return {
        "scope": atlas.get("scope"),
        "model_colours": atlas.get("model_colours"),
        "zone_centroids": atlas.get("zone_centroids"),
        "region_lead_matrix": atlas.get("region_lead_matrix"),
        "cells": cells,
    }


@router.get("/regime/router")
def regime_router_endpoint():
    """Synoptic regime catalogue, regime-conditional weight/skill profiles
    and the measured policy shift each regime induces."""
    return regime_router()


# ---------------------------------------------------------------------------
# Peak preservation (extreme blending layer)
# ---------------------------------------------------------------------------

@router.get("/research/peak-preservation")
def research_peak_preservation():
    """Peak-smearing audit, calibrated extreme-preserving fusion (alpha),
    GPD tail fit and the conformal band with its measured test coverage."""
    return peak_preservation()


# ---------------------------------------------------------------------------
# Impact & Vulnerability Engine (disaster-management layer)
# ---------------------------------------------------------------------------

@router.get("/impact/severity")
def impact_severity(date: str | None = None):
    """Impact Severity Index (0-10), IMD colour code and NDRF-style advisory
    per pilot zone, from the archived blend record (any archived day)."""
    return impact_for_date(date)


@router.get("/impact/cap/{zone}.xml")
def impact_cap(zone: str, date: str | None = None):
    """OASIS CAP 1.2 alert XML for one zone — the SACHET/NDMA consumable."""
    from fastapi.responses import Response
    xml = cap_alert(zone, date)
    return Response(content=xml, media_type="application/xml",
                    headers={"Content-Disposition":
                             f'attachment; filename="cap_{zone}.xml"'})


# ---------------------------------------------------------------------------
# Operational workflow
# ---------------------------------------------------------------------------

@router.get("/ops/status")
def ops_status_endpoint():
    """Last routine-run record, generated bulletin days and artifact health."""
    return ops_status()


@router.get("/ops/bulletin")
def ops_bulletin(date: str | None = None, format: str = "json"):
    """Download the standard bulletin (json | csv | txt) for a day."""
    from fastapi.responses import FileResponse, JSONResponse
    path = bulletin_path(date, format)
    if format == "json":
        return JSONResponse(json.loads(path.read_text(encoding="utf-8")))
    media = {"csv": "text/csv", "txt": "text/plain"}[format]
    return FileResponse(path, media_type=media, filename=path.name)


# ---------------------------------------------------------------------------
# Automatic live-refresh control
# ---------------------------------------------------------------------------

@router.get("/ops/live-refresh/status")
def live_refresh_status():
    """Scheduler state: is it enabled, when did it last run, did it succeed."""
    from app import live_scheduler
    state = live_scheduler.read_state()
    return {
        **state,
        "manifest_age_minutes": live_scheduler.manifest_age_minutes(),
        "worker": "training/realdata/live_refresh.py --once",
        "note": "the scheduler runs inside the API process; cron/Task Scheduler "
                "can run the same worker independently",
    }


@router.post("/ops/live-refresh")
def live_refresh_now(background: bool = True):
    """Trigger a live refresh immediately (does not wait by default)."""
    from app import live_scheduler
    if background:
        import threading
        threading.Thread(target=live_scheduler.run_refresh_once, daemon=True).start()
        return {"status": "STARTED", "note": "refresh running in the background; "
                                             "poll /ops/live-refresh/status"}
    return live_scheduler.run_refresh_once()


# ---------------------------------------------------------------------------
# Demand-driven refresh (production: no cron, no scheduled service)
# ---------------------------------------------------------------------------

@router.post("/system/refresh-if-stale")
def refresh_if_stale_endpoint(wait: bool = False):
    """Refresh the live cycle only when the cached data is stale.

    The frontend renders from cached data first and calls this afterwards, so
    it must never block the UI. By default (``wait=false``) the call returns as
    soon as a background refresh has been started; the frontend then refetches
    the affected queries. Pass ``wait=true`` to run the refresh synchronously
    and receive its final status.

    Concurrency is serialised with a PostgreSQL advisory lock, so several
    clients opening the app at once still run at most one refresh. Secrets and
    stack traces are never returned to the caller.
    """
    from app.on_demand_refresh import refresh_if_stale

    try:
        return refresh_if_stale(wait=wait)
    except Exception:  # never expose internals; the UI keeps its cached data
        log.exception("refresh-if-stale failed")
        return {"status": "refresh_failed", "refreshed": False, "error": "refresh unavailable"}
