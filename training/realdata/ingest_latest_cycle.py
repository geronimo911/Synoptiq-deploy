"""
Operational live cycle: fetch the most recent common completed cycle of GFS,
IFS and AIFS, turn them into future-dated forecast rows, and insert them
into the Synoptiq DB so the dashboard/API shows today's live blend.

This is the live-data ingestion step — note that no project API key is needed for
GFS or ECMWF open data; these are plain public HTTP/HTTPS fetches.

After this runs, your existing /forecast endpoints work on fresh data:
  python ingest_latest_cycle.py            # fetches + inserts
  python ingest_latest_cycle.py --dry-run  # fetch + report only

(Truth rows obviously don't exist yet for future days — verification happens
once IMERG/ERA5 catch up, which you can backfill with fetch_imerg/fetch_era5.)
"""
from __future__ import annotations
import argparse
import os
import sys
import numpy as np
from datetime import datetime, timedelta, timezone

import pandas as pd

from common import CACHE_DIR, forecast_cache_is_complete, season_for_month


PUBLISH_DELAYS_HOURS = {"GFS": 4, "IFS": 8, "AIFS": 8}


def candidate_cycles(now: datetime, publish_delay_hours: int) -> list[datetime]:
    latest_available = now - timedelta(hours=publish_delay_hours)
    latest_cycle = latest_available.replace(
        hour=(latest_available.hour // 6) * 6, minute=0, second=0, microsecond=0
    )
    return [latest_cycle - timedelta(hours=6 * offset) for offset in range(8)]


def valid_time_for_run(run_date: str, run_hour: int, lead_hours: int) -> pd.Timestamp:
    return pd.Timestamp(run_date) + pd.Timedelta(hours=run_hour + lead_hours)


def valid_provider_cycle(frame: pd.DataFrame, model: str, steps: list[int]) -> bool:
    if not forecast_cache_is_complete(frame, model, steps):
        return False
    fields = {
        "GFS": ("t2m_c", "wind_ms", "apcp6_mm"),
        "ifs": ("t2m_c", "wind_ms", "tp_cum_m"),
        "aifs-single": ("t2m_c", "wind_ms", "tp_cum_m"),
    }[model]
    values = frame.loc[:, fields].to_numpy(dtype=float)
    return bool(np.isfinite(values).all())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    import fetch_gfs, fetch_ecmwf
    from build_training_table import (zone_center, detect_regime, top_regime,
                                      VARIABLES, MODEL_RENAME)

    steps = list(range(24, 145, 6))
    now = datetime.now(timezone.utc)
    fetchers = {
        "GFS": (lambda date, hour: fetch_gfs.fetch_cycle(date, hour, steps), "GFS"),
        "IFS": (lambda date, hour: fetch_ecmwf.fetch_cycle("ifs", date, hour, steps), "ifs"),
        "AIFS": (lambda date, hour: fetch_ecmwf.fetch_cycle("aifs-single", date, hour, steps), "aifs-single"),
    }
    cycles = {}
    latest_common_candidate = candidate_cycles(
        now, max(PUBLISH_DELAYS_HOURS.values())
    )
    for candidate in latest_common_candidate:
        run_date = candidate.strftime("%Y-%m-%d")
        candidate_cycles_by_model = {}
        for model in ("IFS", "AIFS"):
            fetch_cycle, validation_model = fetchers[model]
            raw = fetch_cycle(run_date, candidate.hour)
            if not raw:
                continue
            if not valid_provider_cycle(pd.DataFrame(raw), validation_model, steps):
                print(f"  {model} {run_date} {candidate.hour:02d}Z is incomplete", flush=True)
                continue
            candidate_cycles_by_model[model] = raw
        if candidate_cycles_by_model:
            fetch_cycle, validation_model = fetchers["GFS"]
            raw = fetch_cycle(run_date, candidate.hour)
            if raw and valid_provider_cycle(pd.DataFrame(raw), validation_model, steps):
                candidate_cycles_by_model["GFS"] = raw
            elif raw:
                print(f"  GFS {run_date} {candidate.hour:02d}Z is incomplete", flush=True)
        if len(candidate_cycles_by_model) >= 2:
            cycles = candidate_cycles_by_model
            print(
                f"using common {run_date} {candidate.hour:02d}Z cycle "
                f"for {list(cycles)}",
                flush=True,
            )
            break
    if len(cycles) < 2:
        sys.exit(f"Fewer than two complete recent real model cycles are available: {list(cycles)}")

    # build per-day consensus fields first
    from build_training_table import model_day_table
    tables = {}
    raws = {"GFS": pd.DataFrame(cycles.get("GFS", [])),
            "IFS": pd.DataFrame(cycles.get("IFS", [])),
            "AIFS": pd.DataFrame(cycles.get("AIFS", []))}
    for name, raw in raws.items():
        if raw.empty:
            continue
        tables[name] = model_day_table(
            raw,
            "GFS" if name == "GFS" else ("ifs" if name == "IFS" else "aifs-single"),
            hour=int(raw["run_hour"].iloc[0]),
        )
    all_days = pd.concat(tables.values(), ignore_index=True)
    all_days["valid_date"] = [
        valid_time_for_run(row.run_date, int(row.run_hour), int(row.lead_hours))
        for row in all_days.itertuples()
    ]
    cons = all_days.groupby(["zone", "valid_date"]).agg(
        precipitation=("precipitation", "mean"),
        temperature=("temperature", "mean"),
        wind_speed=("wind_ms_max", "mean")).reset_index()
    cons["wind_speed"] = cons["wind_speed"] * 3.6
    cons["season"] = cons["valid_date"].dt.month.map(season_for_month)
    cons["regime_probs"] = cons.apply(
        lambda r: detect_regime({"precipitation": r["precipitation"],
                                 "temperature": r["temperature"],
                                 "wind_speed": r["wind_speed"]},
                                r["season"], zone_center(r["zone"])[0]), axis=1)
    cons["regime"] = cons["regime_probs"].apply(top_regime)
    regime_map = cons.set_index(["zone", "valid_date"])[["regime_probs", "regime", "season"]]

    rows = []
    for name, t in tables.items():
        t = t.copy()
        t["valid_date"] = [
            valid_time_for_run(row.run_date, int(row.run_hour), int(row.lead_hours))
            for row in t.itertuples()
        ]
        t = t.merge(regime_map, left_on=["zone", "valid_date"], right_index=True, how="inner")
        for _, r in t.iterrows():
            lat, lon = zone_center(r["zone"])
            for var in VARIABLES:
                val = (r["wind_ms_max"] * 3.6) if var == "wind_speed" else r[var]
                rows.append(dict(model=name, region=r["zone"],
                                 model_generation=r.get("model_generation", name),
                                 run_time=r["run_date"] + pd.Timedelta(hours=int(r["run_hour"])),
                                 valid_time=r["valid_date"],
                                 lead_hours=int(r["lead_hours"]), variable=var,
                                 lat=lat, lon=lon, forecast_value=round(float(val), 3),
                                 season=r["season"], regime=r["regime"],
                                 regime_probs=r["regime_probs"]))
    fdf = pd.DataFrame(rows)
    print(f"live rows ready: {len(fdf)}")
    print(fdf.groupby(["model", "variable"]).size().unstack(fill_value=0))

    if args.dry_run:
        return

    # insert (upsert-ish: remove today's rows for this run first)
    backend = os.environ.get("SYNOPTIQ_BACKEND")
    if not backend:
        here = os.path.dirname(os.path.abspath(__file__))
        for cand in (os.path.join(here, "..", "backend"), os.path.join(here, "backend"), os.path.join(here, "..", "backend"), os.path.join(here, "backend")):
            if os.path.exists(os.path.join(cand, "app", "models_db.py")):
                backend = os.path.abspath(cand)
    sys.path.insert(0, backend)
    from app.database import SessionLocal
    from app.models_db import ForecastRow
    db = SessionLocal()
    selected_runs = [
        pd.Timestamp(value).to_pydatetime()
        for value in fdf["run_time"].drop_duplicates().tolist()
    ]
    db.query(ForecastRow).filter(ForecastRow.run_time.in_(selected_runs)).delete(synchronize_session=False)
    db.commit()
    buf = []
    for _, r in fdf.iterrows():
        buf.append(ForecastRow(
            model=r["model"], region=r["region"],
            model_generation=r["model_generation"],
            run_time=pd.Timestamp(r["run_time"]).to_pydatetime(),
            valid_time=pd.Timestamp(r["valid_time"]).to_pydatetime(),
            lead_hours=int(r["lead_hours"]), variable=r["variable"],
            lat=r["lat"], lon=r["lon"], forecast_value=float(r["forecast_value"]),
            season=r["season"], regime=r["regime"], regime_probs=dict(r["regime_probs"])))
        if len(buf) >= 1000:
            db.bulk_save_objects(buf); db.commit(); buf.clear()
    if buf:
        db.bulk_save_objects(buf); db.commit()
    db.close()
    print("inserted into synoptiq.db — open the dashboard and blend today's forecast")


if __name__ == "__main__":
    main()
