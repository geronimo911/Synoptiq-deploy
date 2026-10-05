"""
Seed the SQLite archive with synthetic multi-model forecasts + ground truth,
then run a single vectorized post-pass that tags every ForecastRow with its
time-based split ("train"/"val"/"test") and its leakage-free expanding
features (historical_skill_expanding, climatological_anomaly_norm) — see
app/data_prep.py for exactly how those are computed. Doing this once here,
at seed time, means every downstream script/endpoint can just read the
column instead of recomputing it.
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.database import Base, engine, SessionLocal
from app.models_db import ForecastRow, GroundTruthRow
from app.data_sim.synthetic_generator import generate_history
from app.data_prep import prepare_variable_frame, compute_split_boundaries
from app.config import RUNTIME_MODE, VARIABLES

if RUNTIME_MODE != "fake":
    raise SystemExit("Synthetic archive generation requires SYNOPTIQ_MODE=fake")


def seed_raw_archive(db, n_cycles_per_region: int = 260):
    db.query(ForecastRow).delete()
    db.query(GroundTruthRow).delete()
    db.commit()

    n_f = n_g = 0
    buffer = []
    for row in generate_history(n_cycles_per_region=n_cycles_per_region):
        if row["kind"] == "truth":
            buffer.append(GroundTruthRow(
                region=row["region"], valid_time=row["valid_time"], variable=row["variable"],
                lat=row["lat"], lon=row["lon"], observed_value=row["value"],
            ))
            n_g += 1
        else:
            buffer.append(ForecastRow(
                model=row["model"], region=row["region"], run_time=row["run_time"],
                valid_time=row["valid_time"], lead_hours=row["lead_hours"], variable=row["variable"],
                lat=row["lat"], lon=row["lon"], forecast_value=row["forecast_value"],
                season=row["season"], regime=row["regime"], regime_probs=row["regime_probs"],
            ))
            n_f += 1
        if len(buffer) >= 2000:
            db.bulk_save_objects(buffer)
            db.commit()
            buffer = []
    if buffer:
        db.bulk_save_objects(buffer)
        db.commit()
    return n_g, n_f


def tag_splits_and_expanding_features(db):
    """Vectorized post-pass: one query+compute per variable, then one bulk
    UPDATE per variable — no per-row DB round trips."""
    import pandas as pd
    all_vts = pd.read_sql(db.query(ForecastRow.valid_time).distinct().statement, db.bind)["valid_time"]
    val_start, test_start = compute_split_boundaries(all_vts)

    total_updated = 0
    for variable in VARIABLES:
        df = prepare_variable_frame(db, variable, val_start, test_start)
        if df.empty:
            continue
        updates = df[["id", "split", "historical_skill_expanding", "climatological_anomaly_norm"]].to_dict("records")
        db.bulk_update_mappings(ForecastRow, updates)
        db.commit()
        total_updated += len(updates)
    return val_start, test_start, total_updated


def main(n_cycles_per_region: int = 260):
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    n_g, n_f = seed_raw_archive(db, n_cycles_per_region)
    print(f"Seeded {n_g} ground-truth rows and {n_f} forecast rows.")

    val_start, test_start, n_updated = tag_splits_and_expanding_features(db)
    print(f"Tagged splits + expanding features on {n_updated} forecast rows.")
    print(f"  train: valid_time < {val_start}")
    print(f"  val:   {val_start} <= valid_time < {test_start}")
    print(f"  test:  valid_time >= {test_start}")
    db.close()


if __name__ == "__main__":
    main()
