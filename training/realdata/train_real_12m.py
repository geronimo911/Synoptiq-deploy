"""Run the existing Synoptiq training stack against the validated REAL_12M table."""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
CACHE = Path(os.getenv("SYNOPTIQ_CACHE", str(ROOT / "training" / "realdata" / "cache" / "12m"))).resolve()
ARTIFACTS = Path(os.getenv("SYNOPTIQ_ARTIFACTS_DIR", str(ROOT / "artifacts" / "real_12m"))).resolve()
MODELS = Path(os.getenv("SYNOPTIQ_MODELS_DIR", str(ARTIFACTS / "models"))).resolve()
CALIBRATION = Path(os.getenv("SYNOPTIQ_CALIBRATION_DIR", str(ARTIFACTS / "calibration"))).resolve()
METRICS = Path(os.getenv("SYNOPTIQ_METRICS_DIR", str(ARTIFACTS / "metrics"))).resolve()
MODEL_VERSION = os.getenv("SYNOPTIQ_MODEL_VERSION", "synoptiq-real-12m-20260929")


def run(script: Path, env: dict[str, str]) -> None:
    command = [sys.executable, str(script)]
    print("> " + " ".join(command), flush=True)
    subprocess.run(command, cwd=ROOT, env=env, check=True)


def validate_input() -> dict:
    forecasts = pd.read_parquet(CACHE / "training" / "forecasts.parquet")
    truth = pd.read_parquet(CACHE / "training" / "truth.parquet")
    forecast_keys = ["model", "region", "valid_time", "lead_hours", "variable"]
    truth_keys = ["region", "valid_time", "variable"]
    if pd.to_datetime(forecasts["valid_time"]).min().date().isoformat() != "2025-02-26":
        raise SystemExit("REAL_12M forecast input does not start on 2025-02-26")
    if pd.to_datetime(forecasts["valid_time"]).max().date().isoformat() != "2026-02-25":
        raise SystemExit("REAL_12M forecast input does not end on 2026-02-25")
    if forecasts[forecast_keys].duplicated().any() or truth[truth_keys].duplicated().any():
        raise SystemExit("REAL_12M input contains duplicate training keys")
    if forecasts.isna().any().any() or truth.isna().any().any():
        raise SystemExit("REAL_12M input contains nulls")
    return {"forecast_rows": len(forecasts), "truth_rows": len(truth)}


def main() -> None:
    stats = validate_input()
    for path in (ARTIFACTS, MODELS, CALIBRATION, METRICS):
        path.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update({
        "SYNOPTIQ_MODE": "real",
        "SYNOPTIQ_DATABASE_ROLE": "TRAINING",
        "SYNOPTIQ_CACHE": str(CACHE),
        "SYNOPTIQ_BACKEND": str(BACKEND),
        "SYNOPTIQ_ARTIFACTS_DIR": str(ARTIFACTS),
        "SYNOPTIQ_MODELS_DIR": str(MODELS),
        "SYNOPTIQ_CALIBRATION_DIR": str(CALIBRATION),
        "SYNOPTIQ_METRICS_DIR": str(METRICS),
        "SYNOPTIQ_MODEL_VERSION": MODEL_VERSION,
        "TRAINING_DATABASE_URL": env.get("TRAINING_DATABASE_URL", "sqlite:///./data/real/training.sqlite3"),
        "DATABASE_URL": env.get("DATABASE_URL", "postgresql+psycopg://user:password@host:5432/synoptiq"),
    })
    run(ROOT / "training" / "realdata" / "load_into_db.py", env)
    run(BACKEND / "scripts" / "train_model.py", env)
    run(BACKEND / "scripts" / "evaluate_blend.py", env)
    run(BACKEND / "scripts" / "precompute_replay_cache.py", env)
    report = {
        "mode": "REAL_12M",
        "model_version": MODEL_VERSION,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "training_input": str(CACHE / "training"),
        "forecast_rows": stats["forecast_rows"],
        "truth_rows": stats["truth_rows"],
        "model_root": str(MODELS),
        "calibration_root": str(CALIBRATION),
        "metrics_root": str(METRICS),
    }
    (ARTIFACTS / "training_run.json").write_text(__import__("json").dumps(report, indent=2), encoding="utf-8")
    print("REAL_12M TRAINING STACK COMPLETE", flush=True)


if __name__ == "__main__":
    main()
