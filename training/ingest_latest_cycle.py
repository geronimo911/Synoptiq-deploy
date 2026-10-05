"""
Operational live cycle: fetch the most recent completed 00Z cycles of GFS,
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
from datetime import datetime, timedelta, timezone

import pandas as pd
from datetime import datetime

try:
    from dotenv import dotenv_values
    _root = os.path.dirname(os.path.dirname(__file__))
    _values = {}
    for _path in (os.path.join(_root, "backend", ".env"), os.path.join(_root, ".env")):
        for _key, _value in dotenv_values(_path).items():
            if _value is not None and _value.strip():
                _values[_key] = _value
    for _key, _value in _values.items():
        if not os.getenv(_key, "").strip():
            os.environ[_key] = _value
except ImportError:
    pass

REALDATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "realdata")
sys.path.insert(0, REALDATA)
from common import season_for_month


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    import fetch_openmeteo
    from build_training_table import (zone_center, detect_regime, top_regime,
                                      VARIABLES, MODEL_RENAME)

    steps = list(range(6, 145, 6))
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")

    cycles = {}
    # Search newest-to-oldest common completed cycle. A partial cycle is not
    # usable for production blending because it changes the model contract.
    for ds in (today, yesterday):
      for hour in (18, 12, 6, 0):
        gfs = fetch_openmeteo.fetch_live_cycle("gfs", ds)
        ifs = fetch_openmeteo.fetch_live_cycle("ifs", ds)
        aifs = fetch_openmeteo.fetch_live_cycle("aifs-single", ds)
        if gfs and ifs and aifs:
            cycles = {"GFS": gfs, "IFS": ifs, "AIFS": aifs, "run_date": ds, "run_hour": hour}
            break
      if cycles:
        break
    if not cycles:
        sys.exit("No complete recent cycle found — try again in a few hours.")

    run_date = cycles.pop("run_date")
    run_hour = cycles.pop("run_hour")
    run_dt = pd.Timestamp(run_date)
    print(f"using {run_hour:02d}Z common cycle of {run_date} for models: {list(cycles)}")

    # build per-day consensus fields first
    from build_training_table import model_day_table
    tables = {}
    raws = {"GFS": pd.DataFrame(cycles.get("GFS", [])),
            "IFS": pd.DataFrame(cycles.get("IFS", [])),
            "AIFS": pd.DataFrame(cycles.get("AIFS", []))}
    for name, raw in raws.items():
        if raw.empty:
            continue
        tables[name] = model_day_table(raw, "GFS" if name == "GFS" else
                                       ("ifs" if name == "IFS" else "aifs-single"))
    all_days = pd.concat(tables.values(), ignore_index=True)
    all_days["valid_date"] = all_days["run_date"] + pd.to_timedelta(all_days["day_k"], unit="D")
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
        t["valid_date"] = t["run_date"] + pd.to_timedelta(t["day_k"], unit="D")
        t = t.merge(regime_map, left_on=["zone", "valid_date"], right_index=True, how="inner")
        for _, r in t.iterrows():
            lat, lon = zone_center(r["zone"])
            for var in VARIABLES:
                val = (r["wind_ms_max"] * 3.6) if var == "wind_speed" else r[var]
                rows.append(dict(model=name, model_generation=r.get("model_generation", name), region=r["zone"],
                                 run_time=r["run_date"] + pd.Timedelta(hours=run_hour),
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
    from app.models_db import ForecastRow, LiveForecast
    from app.pipeline import run_blend_pipeline
    db = SessionLocal()
    run_dt = run_dt + pd.Timedelta(hours=run_hour)
    db.query(ForecastRow).filter(ForecastRow.run_time == run_dt.to_pydatetime()).delete()
    db.commit()
    buf = []
    for _, r in fdf.iterrows():
        buf.append(ForecastRow(
            model=r["model"], model_generation=r["model_generation"], region=r["region"],
            run_time=pd.Timestamp(r["run_time"]).to_pydatetime(),
            valid_time=pd.Timestamp(r["valid_time"]).to_pydatetime(),
            lead_hours=int(r["lead_hours"]), variable=r["variable"],
            lat=r["lat"], lon=r["lon"], forecast_value=float(r["forecast_value"]),
            season=r["season"], regime=r["regime"], regime_probs=dict(r["regime_probs"])))
        if len(buf) >= 1000:
            db.bulk_save_objects(buf); db.commit(); buf.clear()
    if buf:
        db.bulk_save_objects(buf); db.commit()
    ingestion_time = datetime.now(timezone.utc).replace(tzinfo=None)
    db.query(LiveForecast).filter(LiveForecast.run_time == run_dt.to_pydatetime()).delete()
    db.commit()
    contexts = db.query(ForecastRow.region, ForecastRow.variable,
                        ForecastRow.valid_time, ForecastRow.lead_hours).filter(
        ForecastRow.run_time == run_dt.to_pydatetime()
    ).distinct().all()
    live_rows = []
    for region, variable, valid_time, lead_hours in contexts:
        result = run_blend_pipeline(db, region, variable, valid_time, lead_hours)
        live_rows.append(LiveForecast(
            region=region, variable=variable, valid_time=valid_time,
            lead_hours=lead_hours, run_time=run_dt.to_pydatetime(),
            ingestion_time=ingestion_time,
            model_version=os.environ["SYNOPTIQ_MODEL_VERSION"],
            source_runs={item.model: item.run_time.isoformat() for item in result.raw_sources},
            payload=result.model_dump(mode="json"),
        ))
    if live_rows:
        db.bulk_save_objects(live_rows); db.commit()
    db.close()
    print(f"inserted {len(live_rows)} frozen-model live blends into PostgreSQL")


if __name__ == "__main__":
    main()
