"""
Build the Synoptiq training table from the raw fetcher caches.

Input  : cache/gfs/*.parquet, cache/ifs/*.parquet, cache/aifs-single/*.parquet,
         cache/imerg/*.parquet, cache/era5/*.parquet
Output : cache/training/forecasts.parquet  (one row per model/region/valid_day/lead/variable)
         cache/training/truth.parquet      (one row per region/valid_day/variable)

Everything matches backend/app/models_db.py ForecastRow / GroundTruthRow exactly,
so scripts/train_model.py and scripts/evaluate_blend.py run UNCHANGED on the
result (see load_into_db.py for the DB write).

Conventions (document these in your pitch — they are the "normalization
contract" from Section 5 of your blueprint):
  * run = 00 UTC cycle of run_date (add --hour 12 support if you backfilled 12Z)
  * valid_time = D 00:00 UTC where D = run_date + k days, k = 1..5
    -> the verification window for that row is [D 00, D 24) UTC
  * lead_hours = 24k (24, 48, 72, 96, 120)
  * precipitation = 24-h total over the verification window (mm)
      GFS   : sum of the four 6-hour APCP buckets ending 06/12/18/24 UTC of D
      I/AIFS: (tp_cum at 24 UTC of D) - (tp_cum at 00 UTC of D), in metres -> mm
  * temperature = daily mean 2 m temperature (deg C) over 4 synoptic times
  * wind_speed  = daily max 10 m wind (km/h) over 4 synoptic times
  * regime_probs = rule-based regime detector on the 3-model CONSENSUS fields
    (same detector as backend/app/regime_detector.py — imported if the package
     sits inside the Synoptiq repo, else an identical inline copy)

Usage:
  python build_training_table.py
"""
from __future__ import annotations
import json
import os
import sys
from itertools import product
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from common import (PILOT_ZONES, LEAD_HOURS, CACHE_DIR, season_for_month,
                    forecast_cache_is_complete, forecast_cache_missing)
from window import (REQUIRED_LEADS, REQUIRED_MODELS as WINDOW_MODELS,
                    REQUIRED_REGIONS, REQUIRED_VARIABLES, TRAINING_END,
                    TRAINING_START, requested_dates, validate_window,
                    validate_prototype_window)

MODEL_RENAME = {"GFS": "GFS", "gfs": "GFS", "gfs_openmeteo": "GFS", "ifs": "IFS", "ifs_openmeteo": "IFS", "aifs-single": "AIFS", "aifs-single_openmeteo": "AIFS"}
REQUIRED_MODELS = set(WINDOW_MODELS)
VARIABLES = REQUIRED_VARIABLES
LEADS = REQUIRED_LEADS

# ---------------------------------------------------------------------------
# Regime detector: prefer the repo's own auditable implementation; fall back
# to an identical inline copy when the package runs outside the repo.
# ---------------------------------------------------------------------------
try:
    _repo = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend")
    if os.path.exists(os.path.join(_repo, "app", "regime_detector.py")):
        sys.path.insert(0, _repo)
        from app.regime_detector import detect_regime, top_regime  # type: ignore
    else:
        raise ImportError
except Exception:
    import numpy as _np
    _REGIMES = ["active_monsoon", "break_monsoon", "depression",
                "western_disturbance", "heatwave", "pre_monsoon", "normal"]

    def detect_regime(variable_medians: dict, season: str, lat: float) -> dict:
        """Verbatim copy of backend/app/regime_detector.py (rule-based, auditable)."""
        p = variable_medians.get("precipitation", 0.0)
        t = variable_medians.get("temperature", 28.0)
        w = variable_medians.get("wind_speed", 15.0)
        scores = {r: 0.0 for r in _REGIMES}
        if p >= 80:
            scores["depression"] += 3.0
            scores["active_monsoon"] += 1.5
        elif p >= 40:
            scores["active_monsoon"] += 2.5
        elif p <= 5 and season == "sw_monsoon":
            scores["break_monsoon"] += 2.5
        elif p >= 15 and season == "pre_monsoon":
            scores["pre_monsoon"] += 2.0
        if w >= 45:
            scores["depression"] += 2.5
        if t >= 39:
            scores["heatwave"] += 3.0
        if season == "winter":
            scores["western_disturbance"] += 1.5
            scores["normal"] += 1.0
        if season == "post_monsoon":
            scores["depression"] += 0.5
        scores["normal"] += 0.4
        arr = _np.array([scores[r] for r in _REGIMES])
        probs = _np.exp(arr) / _np.exp(arr).sum()
        return {r: round(float(pr), 4) for r, pr in zip(_REGIMES, probs)}

    def top_regime(regime_probs: dict) -> str:
        return max(regime_probs, key=regime_probs.get)


def zone_center(zone_key: str) -> tuple[float, float]:
    z = PILOT_ZONES[zone_key]
    return round((z["lat_range"][0] + z["lat_range"][1]) / 2, 2), \
           round((z["lon_range"][0] + z["lon_range"][1]) / 2, 2)


# ---------------------------------------------------------------------------
# Loading raw caches
# ---------------------------------------------------------------------------

def load_model_cache(subdir: str) -> pd.DataFrame:
    aliases = {
        "gfs": "gfs",
        "gfs_openmeteo": "gfs_openmeteo",
        "ifs": "ifs_openmeteo",
        "ifs_openmeteo": "ifs_openmeteo",
        "aifs-single": "aifs-single",
        "aifs-single_openmeteo": "aifs-single_openmeteo",
    }
    resolved = aliases.get(subdir, subdir)
    candidate_dirs = [resolved]
    if resolved == "aifs-single":
        candidate_dirs.append("aifs-single_openmeteo")
    frames = []
    seen = set()
    for directory_name in candidate_dirs:
        d = os.path.join(CACHE_DIR, directory_name)
        if not os.path.isdir(d):
            continue
        for f in sorted(os.listdir(d)):
            if not (f.endswith(".parquet") and not f.endswith("_all.parquet")):
                continue
            full_path = os.path.join(d, f)
            if full_path in seen:
                continue
            seen.add(full_path)
            frames.append(pd.read_parquet(full_path))
    if not frames:
        for legacy in ["gfs", "ifs", "aifs-single_openmeteo"]:
            if legacy != resolved and os.path.isdir(os.path.join(CACHE_DIR, legacy)):
                for f in sorted(os.listdir(os.path.join(CACHE_DIR, legacy))):
                    if f.endswith(".parquet") and not f.endswith("_all.parquet"):
                        frames.append(pd.read_parquet(os.path.join(CACHE_DIR, legacy, f)))
    if not frames:
        return pd.DataFrame()
    normalized_frames = []
    for frame in frames:
        if subdir in {"aifs-single", "aifs-single_openmeteo"} and {"selected_valid_timestamp", "run_date", "step"}.issubset(frame.columns) and "precip_mm" in frame.columns and "tp_cum_m" not in frame.columns:
            normalized = frame.copy()
            normalized["selected_valid_timestamp"] = pd.to_datetime(normalized["selected_valid_timestamp"], errors="coerce", utc=True)
            normalized["step"] = pd.to_numeric(normalized["step"], errors="coerce").astype(int)
            normalized["run_date"] = normalized["selected_valid_timestamp"].dt.tz_localize(None).dt.floor("D")
            normalized_frames.append(normalized)
        else:
            normalized_frames.append(frame)
    frames = normalized_frames
    model = {
        "gfs": "GFS", "gfs_openmeteo": "GFS", "ifs": "ifs", "ifs_openmeteo": "ifs",
        "aifs-single": "aifs-single", "aifs-single_openmeteo": "aifs-single",
        "imerg": "truth", "era5": "truth",
    }[resolved]
    raw = pd.concat(frames, ignore_index=True)
    if subdir in {"aifs-single", "aifs-single_openmeteo"} and {"selected_valid_timestamp", "run_date", "step"}.issubset(raw.columns) and "precip_mm" in raw.columns and "tp_cum_m" not in raw.columns:
        raw = raw.copy()
        raw["selected_valid_timestamp"] = pd.to_datetime(raw["selected_valid_timestamp"], errors="coerce", utc=True)
        raw["step"] = pd.to_numeric(raw["step"], errors="coerce").astype(int)
        raw["run_date"] = raw["selected_valid_timestamp"].dt.tz_localize(None).dt.floor("D")
    if subdir in {"imerg", "era5"}:
        key = ["valid_date", "zone"]
        if not set(key).issubset(raw.columns):
            raise SystemExit(f"Invalid {subdir} truth cache; required columns are missing.")
        required = ["valid_date", "zone"] + (["rain_mm"] if subdir == "imerg" else ["t2m_c", "wind_kmh"])
        if raw[required].isna().any().any():
            raise SystemExit(f"Invalid {subdir} truth cache; required values contain nulls.")
        return raw.drop_duplicates(key, keep="last").reset_index(drop=True)
    if subdir.endswith("_openmeteo"):
        required = {"run_date", "run_hour", "step", "zone", "t2m_c", "wind_ms", "precip_mm"}
        if not required.issubset(raw.columns) or raw[list(required)].isna().any().any():
            raise SystemExit(f"Incomplete Open-Meteo {subdir} cache; required fields or values are missing.")
        expected = {(step, zone) for step in LEADS for zone in PILOT_ZONES}
        observed = set(zip(raw["step"].astype(int), raw["zone"].astype(str)))
        if observed != expected:
            raise SystemExit(f"Incomplete Open-Meteo {subdir} cache; missing lead/region records.")
        return raw
    if subdir == "aifs-single":
        return pd.concat(frames, ignore_index=True)
    complete_cycles = []
    incomplete = []
    for frame in frames:
        if "precip_mm" in frame.columns and "tp_cum_m" not in frame.columns:
            if forecast_cache_is_complete(frame, model):
                complete_cycles.append(frame)
            else:
                missing = forecast_cache_missing(frame, model)
                incomplete.append((frame["run_date"].min(), frame["run_hour"].min(), missing))
            continue
        for (run_date, run_hour), cycle in frame.groupby(["run_date", "run_hour"]):
            if forecast_cache_is_complete(cycle, model):
                complete_cycles.append(cycle)
            else:
                incomplete.append((run_date, run_hour, forecast_cache_missing(cycle, model)))
    for run_date, run_hour, missing in incomplete:
        print(f"  excluding incomplete {subdir} cycle {run_date} {int(run_hour):02d}Z: "
              f"{len(missing['steps'])} missing leads, {len(missing['step_regions'])} missing lead/regions, "
              f"missing fields={missing['fields']}")
    if not complete_cycles:
        raise SystemExit(f"No complete {subdir} forecast cycles remain; check provider lead coverage.")
    merged = pd.concat(complete_cycles, ignore_index=True)
    if {"run_date", "run_hour", "step", "zone"}.issubset(merged.columns):
        merged = merged.drop_duplicates(subset=["run_date", "run_hour", "step", "zone"]).reset_index(drop=True)
    return merged


def model_day_table(raw: pd.DataFrame, model: str, hour: int = 0) -> pd.DataFrame:
    """Aggregate the per-step zone means into per-(run, day-k) variable values."""
    raw = raw[raw["run_hour"] == hour].copy()
    raw["run_date"] = pd.to_datetime(raw["run_date"])
    if {"selected_valid_timestamp", "step", "precip_mm"}.issubset(raw.columns):
        legacy = raw["selected_valid_timestamp"].notna() & raw["precip_mm"].notna()
        selected_valid = pd.to_datetime(raw.loc[legacy, "selected_valid_timestamp"], errors="coerce", utc=True)
        raw.loc[legacy, "run_date"] = (
            selected_valid.dt.tz_localize(None)
            - pd.to_timedelta(pd.to_numeric(raw.loc[legacy, "step"], errors="coerce"), unit="h")
        )
    rows = []
    for (run_date, run_hour, zone), g in raw.groupby(["run_date", "run_hour", "zone"]):
        g = g.copy()
        g["step"] = pd.to_numeric(g["step"], errors="coerce").astype(int)
        g = g.drop_duplicates(subset=["step"]).sort_values("step")
        g = g.set_index("step")
        # GFS carries 6-hourly accumulations in `apcp6_mm`; ECMWF carries a
        # running total in `tp_cum_m`. Gate on whichever column this model
        # actually provides — gating on `tp_cum_m` alone silently dropped every
        # GFS row (GFS never has that column), so GFS produced no table at all.
        has_cumulative = "tp_cum_m" in g.columns and g["tp_cum_m"].notna().any()
        has_gfs_precip = "apcp6_mm" in g.columns and g["apcp6_mm"].notna().any()
        if has_cumulative or has_gfs_precip:
            for k in range(1, 6):
                stats_steps = [24 * k + s for s in (0, 6, 12, 18)]
                if any(s not in g.index for s in stats_steps):
                    continue
                gk = g.loc[stats_steps]
                row = dict(model=model, run_date=run_date, run_hour=int(run_hour), zone=zone, day_k=k,
                           lead_hours=24 * k, source_priority=0)
                if "model_generation" in g.columns:
                    generations = g["model_generation"].dropna()
                    if not generations.empty:
                        row["model_generation"] = str(generations.iloc[0])
                    else:
                        row["model_generation"] = model
                if model == "GFS":
                    if not has_gfs_precip:
                        continue
                    precip_steps = [24 * k + s for s in (6, 12, 18, 24)]
                    if any(s not in g.index for s in precip_steps):
                        continue
                    row["precipitation"] = float(g.loc[precip_steps, "apcp6_mm"].clip(lower=0).sum())
                    row["wind_ms_max"] = float(gk["wind_ms"].max())
                else:
                    if not has_cumulative:
                        continue
                    if 24 * k in g.index and 24 * k + 24 in g.index:
                        row["precipitation"] = max(
                            0.0, float(g.loc[24 * k + 24, "tp_cum_m"] - g.loc[24 * k, "tp_cum_m"]) * 1000.0)
                    else:
                        continue
                    row["wind_ms_max"] = float(gk["wind_ms"].max())
                row["temperature"] = float(gk["t2m_c"].mean())
                rows.append(row)
        if "precip_mm" in g.columns:
            for step in sorted(g.index.unique()):
                if int(step) not in set(LEAD_HOURS):
                    continue
                row_run_date = run_date
                if "selected_valid_timestamp" in g.columns:
                    selected_valid = pd.to_datetime(g.loc[step, "selected_valid_timestamp"], errors="coerce", utc=True)
                    if pd.notna(selected_valid):
                        row_run_date = selected_valid.tz_localize(None) - pd.Timedelta(hours=int(step))
                row = dict(model=model, run_date=row_run_date, run_hour=int(run_hour), zone=zone,
                           day_k=int(step) // 24, lead_hours=int(step),
                           source_priority=1 if "selected_valid_timestamp" in g.columns else 0)
                row["precipitation"] = float(g.loc[step, "precip_mm"])
                row["wind_ms_max"] = float(g.loc[step, "wind_ms"])
                row["temperature"] = float(g.loc[step, "t2m_c"])
                if "model_generation" in g.columns:
                    generations = g["model_generation"].dropna()
                    if not generations.empty:
                        row["model_generation"] = str(generations.iloc[0])
                    else:
                        row["model_generation"] = model
                rows.append(row)
    return pd.DataFrame(rows)


def validate_requested_coverage(fdf: pd.DataFrame, tdf: pd.DataFrame,
                                start_date: pd.Timestamp | None = None,
                                end_date: pd.Timestamp | None = None) -> list[pd.Timestamp]:
    """Write coverage evidence and reject any gap in the fixed window."""
    start_date = start_date or pd.Timestamp(TRAINING_START)
    end_date = end_date or pd.Timestamp(TRAINING_END)
    expected_dates = list(pd.date_range(start_date, end_date, freq="D"))
    expected_forecast = set(product(REQUIRED_MODELS, REQUIRED_REGIONS, REQUIRED_LEADS, REQUIRED_VARIABLES))
    expected_truth = set(product(REQUIRED_REGIONS, REQUIRED_VARIABLES))
    forecast_keys = {(
        row.model, row.region, int(row.lead_hours), row.variable
    ) for row in fdf.itertuples()}
    truth_keys = {(row.region, row.variable) for row in tdf.itertuples()}

    complete_dates = []
    missing_by_date = {}
    for valid_time in expected_dates:
        day_forecasts = fdf[fdf["valid_time"] == valid_time]
        day_truth = tdf[tdf["valid_time"] == valid_time]
        forecast_day_keys = {(
            row.model, row.region, int(row.lead_hours), row.variable
        ) for row in day_forecasts.itertuples()}
        truth_day_keys = {(row.region, row.variable) for row in day_truth.itertuples()}
        missing_forecasts = sorted(expected_forecast - forecast_day_keys)
        missing_truth = sorted(expected_truth - truth_day_keys)
        if missing_forecasts or missing_truth:
            missing_by_date[valid_time.date().isoformat()] = {
                "forecast": [
                    {"provider": model, "region": region, "lead_hours": lead, "variable": variable}
                    for model, region, lead, variable in missing_forecasts
                ],
                "truth": [
                    {"region": region, "variable": variable}
                    for region, variable in missing_truth
                ],
            }
        else:
            complete_dates.append(valid_time)

    longest_start = longest_end = None
    current = []
    for valid_time in expected_dates + [None]:
        if valid_time is not None and valid_time in complete_dates:
            current.append(valid_time)
            continue
        if len(current) > (0 if longest_start is None else (longest_end - longest_start).days + 1):
            longest_start, longest_end = current[0], current[-1]
        current = []

    forecast_key_columns = ["model", "region", "valid_time", "lead_hours", "variable"]
    truth_key_columns = ["region", "valid_time", "variable"]
    duplicate_forecasts = int(fdf.duplicated(forecast_key_columns).sum())
    duplicate_truth = int(tdf.duplicated(truth_key_columns).sum())
    report = {
        "requested_date_range": {
            "start": start_date.date().isoformat(), "end": end_date.date().isoformat()
        },
        "actual_complete_date_range": {
            "start": complete_dates[0].date().isoformat() if complete_dates else None,
            "end": complete_dates[-1].date().isoformat() if complete_dates else None,
        },
        "longest_complete_common_contiguous_interval": {
            "start": longest_start.date().isoformat() if longest_start is not None else None,
            "end": longest_end.date().isoformat() if longest_end is not None else None,
        },
        "missing_dates": sorted(missing_by_date),
        "missing_coverage": missing_by_date,
        "missing_providers": sorted({
            item["provider"]
            for details in missing_by_date.values()
            for item in details["forecast"]
        }),
        "row_counts": {"forecasts": int(len(fdf)), "truth": int(len(tdf))},
        "duplicate_counts": {"forecasts": duplicate_forecasts, "truth": duplicate_truth},
        "null_counts": {
            "forecasts": int(fdf.isna().sum().sum()), "truth": int(tdf.isna().sum().sum())
        },
        "complete_date_count": len(complete_dates),
    }
    report_path = os.path.join(CACHE_DIR, "completeness_report.json")
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2)

    if missing_by_date or duplicate_forecasts or duplicate_truth:
        first_missing = next(iter(missing_by_date), "none")
        raise SystemExit(
            "Requested fixed training window is incomplete; see "
            f"{report_path}. Missing dates={len(missing_by_date)}, "
            f"first missing={first_missing}, longest complete interval="
            f"{report['longest_complete_common_contiguous_interval']['start']} through "
            f"{report['longest_complete_common_contiguous_interval']['end']}."
        )
    return complete_dates
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Main build
# ---------------------------------------------------------------------------

def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=TRAINING_START.isoformat())
    ap.add_argument("--end", default=TRAINING_END.isoformat())
    ap.add_argument("--hour", type=int, default=0, choices=[0])
    ap.add_argument("--real-prototype", action="store_true")
    args = ap.parse_args()
    if args.real_prototype:
        validate_prototype_window(args.start, args.end)
    else:
        validate_window(args.start, args.end)
    start_date = pd.Timestamp(args.start)
    end_date = pd.Timestamp(args.end)
    print("loading caches...")
    gfs_raw = load_model_cache("gfs_openmeteo")
    ifs_raw = load_model_cache("ifs_openmeteo")
    aifs_raw = load_model_cache("aifs-single")
    imerg = load_model_cache("imerg")
    era5 = load_model_cache("era5")

    required_forecasts = {"GFS": gfs_raw, "IFS": ifs_raw, "AIFS": aifs_raw}
    missing = [name for name, df in required_forecasts.items() if df.empty]
    if missing:
        raise SystemExit(f"Missing real forecast caches: {missing}. Fetch GFS/IFS/AIFS before training.")
    if imerg.empty:
        raise SystemExit("Missing IMERG verification cache. Configure NASA Earthdata credentials and fetch IMERG before training.")
    if era5.empty:
        raise SystemExit("Missing ERA5 verification cache. Fetch ERA5 before training.")

    tables = {}
    for name, raw in (("GFS", gfs_raw), ("IFS", ifs_raw), ("AIFS", aifs_raw)):
        if raw.empty:
            print(f"WARNING: no cache for {name}")
            continue
        tables[name] = model_day_table(raw, name, hour=args.hour)
        print(f"  {name}: {len(tables[name])} (run, zone, day) contexts")

    if not tables or not any(not table.empty for table in tables.values()):
        raise SystemExit("No valid forecast contexts were produced; check provider lead coverage.")

    # --- consensus regime per (zone, valid_date) ---------------------------
    all_days = pd.concat(tables.values(), ignore_index=True)
    all_days["valid_date"] = all_days["run_date"] + pd.to_timedelta(all_days["day_k"], unit="D")
    cons = all_days.groupby(["zone", "valid_date"]).agg(
        precipitation=("precipitation", "mean"),
        temperature=("temperature", "mean"),
        wind_speed=("wind_ms_max", "mean")).reset_index()
    cons["wind_speed"] = cons["wind_speed"] * 3.6  # m/s -> km/h
    cons["season"] = cons["valid_date"].dt.month.map(season_for_month)
    cons["regime_probs"] = cons.apply(
        lambda r: detect_regime(
            variable_medians={"precipitation": r["precipitation"],
                              "temperature": r["temperature"],
                              "wind_speed": r["wind_speed"]},
            season=r["season"], lat=zone_center(r["zone"])[0]), axis=1)
    cons["regime"] = cons["regime_probs"].apply(top_regime)

    # --- forecast rows ------------------------------------------------------
    regime_map = cons.set_index(["zone", "valid_date"])[["regime_probs", "regime", "season"]]
    frows = []
    for name, t in tables.items():
        t = t.copy()
        t["valid_date"] = t["run_date"] + pd.to_timedelta(t["day_k"], unit="D")
        t = t.merge(regime_map, left_on=["zone", "valid_date"], right_index=True, how="inner")
        for _, r in t.iterrows():
            lat, lon = zone_center(r["zone"])
            for var in VARIABLES:
                if var == "wind_speed":
                    val = r["wind_ms_max"] * 3.6
                else:
                    val = r[var]
                model_generation = r.get("model_generation")
                if pd.isna(model_generation):
                    model_generation = MODEL_RENAME.get(name, name)
                frows.append(dict(
                    model=MODEL_RENAME.get(name, name), region=r["zone"],
                    model_generation=model_generation,
                    source_priority=int(r.get("source_priority", 0)),
                    run_time=r["run_date"] + pd.Timedelta(hours=int(r["run_hour"])),
                    valid_time=r["valid_date"],
                    lead_hours=int(r["lead_hours"]), variable=var,
                    lat=lat, lon=lon,
                    forecast_value=round(float(val), 3),
                    season=r["season"], regime=r["regime"],
                    regime_probs=r["regime_probs"],
                ))
    fdf = pd.DataFrame(frows)
    forecast_key_columns = ["model", "region", "valid_time", "lead_hours", "variable"]
    if "source_priority" in fdf.columns:
        fdf = (
            fdf.sort_values("source_priority")
            .drop_duplicates(forecast_key_columns, keep="first")
            .drop(columns=["source_priority"])
            .reset_index(drop=True)
        )

    # --- truth rows ---------------------------------------------------------
    truths = []
    if not imerg.empty:
        imerg = imerg.copy()
        imerg["valid_date"] = pd.to_datetime(imerg["valid_date"])
        for _, r in imerg.iterrows():
            lat, lon = zone_center(r["zone"])
            truths.append(dict(region=r["zone"], valid_time=r["valid_date"],
                               variable="precipitation", lat=lat, lon=lon,
                               observed_value=round(float(r["rain_mm"]), 3),
                               source="IMERG_Daily_Late_V07"))
    if not era5.empty:
        era5 = era5.copy()
        era5["valid_date"] = pd.to_datetime(era5["valid_date"])
        for _, r in era5.iterrows():
            lat, lon = zone_center(r["zone"])
            truths.append(dict(region=r["zone"], valid_time=r["valid_date"],
                               variable="temperature", lat=lat, lon=lon,
                               observed_value=round(float(r["t2m_c"]), 3),
                               source="ERA5"))
            truths.append(dict(region=r["zone"], valid_time=r["valid_date"],
                               variable="wind_speed", lat=lat, lon=lon,
                               observed_value=round(float(r["wind_kmh"]), 3),
                               source="ERA5"))
    tdf = pd.DataFrame(truths)

    fdf["valid_time"] = pd.to_datetime(fdf["valid_time"])
    tdf["valid_time"] = pd.to_datetime(tdf["valid_time"])
    fdf = fdf[fdf["valid_time"] >= start_date]
    tdf = tdf[tdf["valid_time"] >= start_date]
    if end_date is not None:
        fdf = fdf[fdf["valid_time"] <= end_date]
        tdf = tdf[tdf["valid_time"] <= end_date]

    if fdf.empty:
        raise SystemExit("No valid forecast contexts were produced; check provider lead coverage.")

    common_dates = validate_requested_coverage(fdf, tdf, start_date, end_date)
    common_date_set = set(common_dates)
    fdf = fdf[fdf["valid_time"].isin(common_date_set)].copy()
    tdf = tdf[tdf["valid_time"].isin(common_date_set)].copy()
    print(f"COMMON_TRAIN_START={common_dates[0].date().isoformat()}")
    print(f"COMMON_TRAIN_END={common_dates[-1].date().isoformat()}")
    print(f"COMMON_TRAIN_VALID_DATES={len(common_dates)}")
    if len(common_dates) > 1:
        gaps = (common_dates[-1] - common_dates[0]).days + 1 - len(common_dates)
        print(f"COMMON_TRAIN_MISSING_DATES={gaps}")

    out = os.path.join(CACHE_DIR, "training")
    os.makedirs(out, exist_ok=True)
    fdf.to_parquet(os.path.join(out, "forecasts.parquet"), index=False)
    tdf.to_parquet(os.path.join(out, "truth.parquet"), index=False)

    # --- sanity report ------------------------------------------------------
    print(f"\nforecast rows: {len(fdf)}  |  truth rows: {len(tdf)}")
    print(f"TOTAL_ROWS={len(fdf)}")
    print("ROWS_PER_SOURCE:")
    print(fdf.groupby("model").size().to_string())
    print("ROWS_PER_REGION:")
    print(fdf.groupby("region").size().to_string())
    print("ROWS_PER_VARIABLE:")
    print(fdf.groupby("variable").size().to_string())
    print("ROWS_PER_LEAD:")
    print(fdf.groupby("lead_hours").size().to_string())
    print("\nPer model x variable counts:")
    print(fdf.groupby(["model", "variable"]).size().unstack(fill_value=0))
    print("\nValue ranges (should be physically plausible):")
    for var in VARIABLES:
        s = fdf[fdf["variable"] == var]["forecast_value"]
        print(f"  {var:15s} min {s.min():8.2f}  max {s.max():8.2f}  mean {s.mean():8.2f}")
    if not tdf.empty:
        for var in VARIABLES:
            s = tdf[tdf["variable"] == var]["observed_value"]
            if len(s):
                print(f"  truth {var:10s} min {s.min():8.2f}  max {s.max():8.2f}")
    print("\nRegime distribution:")
    print(cons["regime"].value_counts())
    print(f"\nwrote {out}/forecasts.parquet and truth.parquet")


if __name__ == "__main__":
    main()
