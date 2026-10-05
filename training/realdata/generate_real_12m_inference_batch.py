"""Vectorized historical REAL_12M inference using the production blend stack."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
ARTIFACTS = Path(os.getenv("SYNOPTIQ_ARTIFACTS_DIR", str(ROOT / "artifacts" / "real_12m"))).resolve()
OUTPUT = Path(os.getenv("SYNOPTIQ_INFERENCE_DIR", str(ARTIFACTS / "inference"))).resolve()
sys.path.insert(0, str(BACKEND))

from app.blending.blend import batch_blend  # noqa: E402
from app.blending.calibration import apply_quantile_map, exceedance_probabilities  # noqa: E402
from app.blending.features import encode  # noqa: E402
from app.blending.trust import compute_trust  # noqa: E402
from app.config import LEAD_HOURS, PILOT_ZONES, VARIABLES  # noqa: E402
from app.data_prep import build_batch_blend_inputs, compute_split_boundaries, prepare_variable_frame  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models_db import ForecastRow  # noqa: E402


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    db = SessionLocal()
    valid_times = pd.read_sql(db.query(ForecastRow.valid_time).distinct().statement, db.bind)["valid_time"]
    val_start, test_start = compute_split_boundaries(valid_times)
    rows = []
    for variable in VARIABLES:
        prepared = prepare_variable_frame(db, variable, val_start, test_start)
        feats, values, truth, meta = build_batch_blend_inputs(prepared, variable)
        if not feats:
            continue
        fallback_skill = {model: meta["historical_skill_expanding"].to_numpy() for model in feats}
        blended, weights, source = batch_blend(variable, feats, values, fallback_skill)
        for index in range(len(truth)):
            context = meta.iloc[index]
            source_values = {model: float(values[model][index]) for model in values}
            source_weights = {model: round(float(weights[model][index]), 6) for model in weights}
            calibrated = apply_quantile_map(float(blended[index]), variable, region=context["region"])
            regime_probs = context["regime_probs"] if isinstance(context["regime_probs"], dict) else {}
            historical_skill = {model: float(context["historical_skill_expanding"]) for model in values}
            disagreement = float(np.std(list(source_values.values())))
            feature = encode(
                lead_hours=int(context["lead_hours"]), regime_probs=regime_probs,
                region=context["region"], season=context["season"],
                historical_skill=float(np.mean(list(historical_skill.values()))),
                disagreement=disagreement, forecast_value=float(blended[index]),
                consensus_deviation=0.0,
                climatological_anomaly=float(context["climatological_anomaly_norm"]),
            )
            trust = compute_trust(
                variable=variable, weights=source_weights,
                historical_skill_by_model=historical_skill, disagreement=disagreement,
                lead_hours=int(context["lead_hours"]), regime_probs=regime_probs,
                n_sources_available=len(source_values), n_sources_expected=3,
                context_features=feature,
            )
            rows.append({
                "valid_time": pd.Timestamp(context["valid_time"]).isoformat(),
                "region": context["region"], "variable": variable,
                "lead_hours": int(context["lead_hours"]),
                "sources": source_values, "weights": source_weights,
                "blend": float(blended[index]), "calibrated": float(calibrated),
                "exceedance_probabilities": exceedance_probabilities(calibrated, variable, context["region"]),
                "trust": trust["trust_score"], "bust_probability": trust["bust_probability"],
                "abstain": trust["abstain"], "weight_source": source,
                "regime": context["regime"], "regime_probs": regime_probs,
                "split": context["split"], "observed_value": float(truth[index]),
            })
        print(f"{variable}: generated {len(truth)} contexts", flush=True)
    db.close()
    output_path = OUTPUT / "historical_blends.jsonl"
    with output_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, separators=(",", ":")) + "\n")
    metadata = {
        "mode": "REAL_12M", "contexts": len(rows),
        "period": {"start": "2025-02-26", "end": "2026-02-25"},
        "regions": list(PILOT_ZONES), "variables": list(VARIABLES),
        "leads": list(LEAD_HOURS), "source": "backend.app.blending.batch_blend",
        "calibration": "REAL_12M artifacts", "synthetic_fallback": False,
        "output": str(output_path), "val_start": str(val_start), "test_start": str(test_start),
    }
    (OUTPUT / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print(json.dumps(metadata, indent=2))


if __name__ == "__main__":
    main()
