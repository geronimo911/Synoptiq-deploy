"""Load real Parquet tables into the isolated training database.

Run from anywhere; it finds the Synoptiq backend automatically:
  SYNOPTIQ_BACKEND=/path/to/Synoptiq-main/backend python load_into_db.py
(or place this folder next to the repo root and just run it).

This mirrors scripts/generate_synthetic_data.py exactly (same tables, same
post-pass), so afterwards you run the SAME commands as before:
    D:/SIH2026/.venv/Scripts/python.exe backend/scripts/train_model.py
    D:/SIH2026/.venv/Scripts/python.exe backend/scripts/evaluate_blend.py
"""
from __future__ import annotations
import argparse
import os
import sys

import pandas as pd

from common import CACHE_DIR


def find_backend() -> str:
    env = os.environ.get("SYNOPTIQ_BACKEND")
    if env:
        return env
    here = os.path.dirname(os.path.abspath(__file__))
    for cand in (
        os.path.join(here, "..", "backend"),
        os.path.join(here, "backend"),
        os.path.join(here, "..", "..", "backend"),
        os.path.join(here, "..", "backend"),
        os.path.join(here, "backend"),
    ):
        if os.path.exists(os.path.join(cand, "app", "models_db.py")):
            return os.path.abspath(cand)
    sys.exit("Set SYNOPTIQ_BACKEND to the Synoptiq backend directory "
             "(the one containing app/models_db.py)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true", help="keep existing rows instead of wiping")
    args = ap.parse_args()

    if os.getenv("SYNOPTIQ_DATABASE_ROLE") != "TRAINING":
        raise SystemExit("Refusing to load training data without SYNOPTIQ_DATABASE_ROLE=TRAINING")
    training_url = os.getenv("TRAINING_DATABASE_URL", "").strip()
    production_url = os.getenv("DATABASE_URL", "").strip()
    if not training_url:
        raise SystemExit("TRAINING_DATABASE_URL is required; production DATABASE_URL is never used for training")
    if production_url and training_url == production_url:
        raise SystemExit("TRAINING_DATABASE_URL must not equal DATABASE_URL")

    backend = find_backend()
    sys.path.insert(0, backend)
    from app.database import Base, engine, SessionLocal
    from app.models_db import ForecastRow, GroundTruthRow, SkillRow, ReplayEvent

    fpath = os.path.join(CACHE_DIR, "training", "forecasts.parquet")
    tpath = os.path.join(CACHE_DIR, "training", "truth.parquet")
    fdf = pd.read_parquet(fpath)
    tdf = pd.read_parquet(tpath)

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    if not args.keep:
        db.query(ReplayEvent).delete()
        db.query(SkillRow).delete()
        db.query(ForecastRow).delete()
        db.query(GroundTruthRow).delete()
        db.commit()

    buf = []

    def flush():
        if buf:
            db.bulk_save_objects(buf)
            db.commit()
            buf.clear()

    for _, r in tdf.iterrows():
        buf.append(GroundTruthRow(region=r["region"], valid_time=r["valid_time"],
                                  variable=r["variable"], lat=r["lat"], lon=r["lon"],
                                  observed_value=r["observed_value"],
                                  source=r.get("source", "REAL")))
        if len(buf) >= 2000:
            flush()
    for _, r in fdf.iterrows():
        buf.append(ForecastRow(model=r["model"], model_generation=r.get("model_generation"), region=r["region"],
                               run_time=pd.Timestamp(r["run_time"]).to_pydatetime(),
                               valid_time=pd.Timestamp(r["valid_time"]).to_pydatetime(),
                               lead_hours=int(r["lead_hours"]), variable=r["variable"],
                               lat=r["lat"], lon=r["lon"],
                               forecast_value=float(r["forecast_value"]),
                               season=r["season"], regime=r["regime"],
                               regime_probs=dict(r["regime_probs"])))
        if len(buf) >= 2000:
            flush()
    flush()
    print(f"inserted {len(fdf)} forecast rows, {len(tdf)} truth rows")

    # Same post-pass as the synthetic seeder: time-based split + leakage-free
    # expanding features, so train_model.py / evaluate_blend.py work untouched.
    from app.data_prep import compute_split_boundaries
    import pandas as _pd
    all_vts = _pd.read_sql(db.query(ForecastRow.valid_time).distinct().statement, db.bind)["valid_time"]
    val_start, test_start = compute_split_boundaries(all_vts)
    from app.config import VARIABLES as VARS
    from app.data_prep import prepare_variable_frame
    total = 0
    for variable in VARS:
        df = prepare_variable_frame(db, variable, val_start, test_start)
        if df.empty:
            continue
        updates = df[["id", "split", "historical_skill_expanding",
                      "climatological_anomaly_norm"]].to_dict("records")
        db.bulk_update_mappings(ForecastRow, updates)
        db.commit()
        total += len(updates)
    print(f"tagged splits + expanding features on {total} rows")
    print(f"  train: valid_time < {val_start}\n  val:   {val_start} .. {test_start}\n  test:  >= {test_start}")
    db.close()
    print("\nTraining database load complete; subsequent training scripts use TRAINING_DATABASE_URL.")


if __name__ == "__main__":
    main()
