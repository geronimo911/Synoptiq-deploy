"""
Offline Historical Replay Mode (Section 11 "showstopper": Counterfactual Bust
Replay; Section 17 demo script; Section 18 guardrail: "pre-cache
representative cases, Historical Replay Mode"). This is the DEFAULT judging
path — every replay event is fully precomputed and stored, so opening it
needs zero live model inference and is immune to any API/network flakiness
on demo day.
"""
from __future__ import annotations
import json
import math
import pandas as pd
from datetime import datetime
from functools import lru_cache
from sqlalchemy.orm import Session

from app.config import ARTIFACTS_DIR, MODELS, VARIABLES
from app.models_db import ForecastRow, GroundTruthRow, ReplayEvent
from app.pipeline import run_blend_pipeline

N_EVENTS_PER_REGION = 3


def build_real_replay_case(row: dict) -> tuple[dict, dict] | None:
    """Build a replay from a frozen held-out inference row, never training data."""
    if row.get("split") != "test" or row.get("variable") not in VARIABLES:
        return None
    source_values = row.get("sources")
    source_weights = row.get("weights")
    if not isinstance(source_values, dict) or not isinstance(source_weights, dict):
        return None
    if any(model not in source_values for model in MODELS):
        return None

    try:
        forecasts = {model: float(source_values[model]) for model in MODELS}
        weights = {model: float(source_weights[model]) for model in MODELS}
        observed = float(row["observed_value"])
        blend = float(row["calibrated"])
        valid_time = pd.Timestamp(row["valid_time"]).isoformat()
        lead_hours = int(row["lead_hours"])
    except (KeyError, TypeError, ValueError):
        return None
    if not all(math.isfinite(value) for value in (*forecasts.values(), *weights.values(), observed, blend)):
        return None

    naive_average = sum(forecasts.values()) / len(forecasts)
    single_model = max(MODELS, key=lambda model: weights[model])
    errors = {
        "naive_average_error": abs(naive_average - observed),
        "single_model_error": abs(forecasts[single_model] - observed),
        "synoptiq_error": abs(blend - observed),
    }
    baseline_errors = (errors["naive_average_error"], errors["single_model_error"])
    if errors["synoptiq_error"] < min(baseline_errors):
        outcome, label = "WIN", "Blend beats both baselines"
    elif errors["synoptiq_error"] > max(baseline_errors):
        outcome, label = "MISS", "Blend misses both baselines"
    else:
        outcome, label = "MIXED", "Mixed baseline result"

    event_id = "real12m_{}_{}_{}h_{}".format(
        row["region"], row["variable"], lead_hours,
        valid_time.replace("-", "").replace(":", "").replace("T", "_")[:15],
    )
    headline = (
        f"Held-out test absolute error: Synoptiq {errors['synoptiq_error']:.2f}, "
        f"naive average {errors['naive_average_error']:.2f}, "
        f"top-weight model {single_model} {errors['single_model_error']:.2f}."
    )
    summary = {
        "event_id": event_id,
        "region": row["region"],
        "variable": row["variable"],
        "valid_time": valid_time,
        "lead_hours": lead_hours,
        "label": label,
        "headline": headline,
        "outcome": outcome,
        "severity": "high" if outcome == "MISS" else "low",
        "naive_average_error": errors["naive_average_error"],
        "single_model_error": errors["single_model_error"],
        "synoptiq_error": errors["synoptiq_error"],
    }
    detail = {
        **summary,
        "unit": {"precipitation": "mm/24h", "temperature": "deg_C", "wind_speed": "km/h"}[row["variable"]],
        "raw_sources": [
            {"model": model, "forecast_value": forecasts[model], "weight": weights[model], "historical_skill": None}
            for model in MODELS
        ],
        "naive_average": naive_average,
        "single_model_choice": {"model": single_model, "forecast_value": forecasts[single_model]},
        "synoptiq_blend": blend,
        "reference_value": observed,
        "split": "test",
        "narrative": [
            "Frozen REAL_12M held-out TEST case; this row was not used to fit the blend or calibrators.",
            f"The naive three-model average was {naive_average:.2f}; absolute error {errors['naive_average_error']:.2f}.",
            f"The highest-weight source was {single_model}; absolute error {errors['single_model_error']:.2f}.",
            f"Synoptiq output was {blend:.2f}; absolute error {errors['synoptiq_error']:.2f}.",
        ],
    }
    return summary, detail


@lru_cache(maxsize=1)
def real_test_replay_cases() -> tuple[tuple[dict, dict], ...]:
    path = ARTIFACTS_DIR / "inference" / "historical_blends.jsonl"
    if not path.is_file():
        return ()
    cases = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                case = build_real_replay_case(json.loads(line))
            except (json.JSONDecodeError, TypeError):
                continue
            if case:
                cases.append(case)
    cases.sort(key=lambda case: case[0]["valid_time"], reverse=True)
    return tuple(cases)


def _pick_candidate_valid_times(db: Session, region: str, variable: str, lead_hours: int) -> pd.DataFrame:
    fdf = pd.read_sql(
        db.query(ForecastRow).filter(ForecastRow.region == region, ForecastRow.variable == variable,
                                      ForecastRow.lead_hours == lead_hours).statement, db.bind)
    gdf = pd.read_sql(
        db.query(GroundTruthRow).filter(GroundTruthRow.region == region,
                                         GroundTruthRow.variable == variable).statement, db.bind)
    merged = fdf.merge(gdf[["region", "valid_time", "variable", "observed_value"]],
                        on=["region", "valid_time", "variable"], how="inner")
    if merged.empty:
        return merged
    agg = merged.groupby("valid_time").agg(
        observed_value=("observed_value", "first"),
        naive_avg=("forecast_value", "mean"),
        spread=("forecast_value", "std"),
    ).reset_index()
    agg["naive_error"] = (agg["naive_avg"] - agg["observed_value"]).abs()
    return agg


def precompute_replay_cache(db: Session, region: str, variable: str = "precipitation",
                             lead_hours: int = 72) -> list[str]:
    agg = _pick_candidate_valid_times(db, region, variable, lead_hours)
    if agg.empty:
        return []

    ids_written = []
    # One clear "bust" case (naive average would have been badly wrong)...
    bust_row = agg.loc[agg["naive_error"].idxmax()]
    # ...one clean high-confidence "success" case...
    calm_row = agg.loc[agg["spread"].idxmin()]
    # ...and one high-disagreement case in between.
    mid_row = agg.iloc[(agg["spread"] - agg["spread"].median()).abs().idxmin()]

    for row, label, severity in [
        (bust_row, "Naive-average bust event", "high"),
        (mid_row, "High model disagreement", "medium"),
        (calm_row, "Calm, high-confidence case", "low"),
    ]:
        vt = row["valid_time"]
        if isinstance(vt, str):
            vt = pd.to_datetime(vt)
        try:
            result = run_blend_pipeline(db, region, variable, vt.to_pydatetime() if hasattr(vt, "to_pydatetime") else vt, lead_hours)
        except ValueError:
            continue
        event_id = f"{region}_{variable}_{lead_hours}h_{vt.strftime('%Y%m%dT%H%M')}"
        payload = json.loads(result.model_dump_json())
        payload["naive_average"] = round(float(row["naive_avg"]), 2)
        payload["naive_average_error"] = round(float(row["naive_error"]), 2)
        payload["observed_value"] = round(float(row["observed_value"]), 2)

        existing = db.get(ReplayEvent, event_id)
        if existing:
            db.delete(existing)
            db.flush()
        db.add(ReplayEvent(
            event_id=event_id, region=region, variable=variable, valid_time=vt,
            label=label, severity=severity, payload=payload,
        ))
        ids_written.append(event_id)
    db.commit()
    return ids_written
