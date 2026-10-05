"""Generate historical REAL_12M blend inference through the serving pipeline."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
ARTIFACTS = ROOT / "artifacts" / "real_12m"
OUTPUT = ARTIFACTS / "inference"

sys.path.insert(0, str(BACKEND))

from app.config import PILOT_ZONES, VARIABLES, LEAD_HOURS  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models_db import ForecastRow  # noqa: E402
from app.pipeline import run_blend_pipeline  # noqa: E402


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    db = SessionLocal()
    contexts = (
        db.query(ForecastRow.region, ForecastRow.variable, ForecastRow.valid_time, ForecastRow.lead_hours)
        .distinct()
        .order_by(ForecastRow.valid_time, ForecastRow.region, ForecastRow.variable, ForecastRow.lead_hours)
        .all()
    )
    output_path = OUTPUT / "historical_blends.jsonl"
    count = 0
    with output_path.open("w", encoding="utf-8") as handle:
        for index, (region, variable, valid_time, lead_hours) in enumerate(contexts, start=1):
            result = run_blend_pipeline(db, region, variable, valid_time, int(lead_hours))
            handle.write(json.dumps(result.model_dump(mode="json"), separators=(",", ":")) + "\n")
            count += 1
            if index % 500 == 0:
                print(f"inference contexts: {index}/{len(contexts)}", flush=True)
    db.close()
    metadata = {
        "mode": "REAL_12M",
        "contexts": count,
        "period": {"start": "2025-02-26", "end": "2026-02-25"},
        "regions": list(PILOT_ZONES),
        "variables": list(VARIABLES),
        "leads": list(LEAD_HOURS),
        "source": "backend.app.pipeline.run_blend_pipeline",
        "synthetic_fallback": False,
        "output": str(output_path),
    }
    (OUTPUT / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print(json.dumps(metadata, indent=2))


if __name__ == "__main__":
    main()
