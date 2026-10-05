"""Real ECMWF historical forecasts through Open-Meteo Previous Runs API.

The API exposes fixed offsets from each valid timestamp:
previous_day1..previous_day5 = 24..120 hour forecast leads.
Only regional representative points are requested; API responses are never cached.
"""
from __future__ import annotations

import argparse
import json
import os
import time
import urllib.parse
import urllib.request
from datetime import date, timedelta
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

from common import CACHE_DIR, PILOT_ZONES
from window import (REQUIRED_LEADS, TRAINING_END, TRAINING_START,
                    validate_prototype_window, validate_window)

API_URL = "https://previous-runs-api.open-meteo.com/v1/forecast"
LIVE_API_URL = "https://api.open-meteo.com/v1/forecast"
MODELS = {
    "gfs": ("gfs_global", "NCEP GFS Global 0.11/0.25"),
    "ifs": ("ecmwf_ifs025", "ECMWF IFS 0.25"),
    # NOTE: Open-Meteo serves AIFS under `ecmwf_aifs025_single`. The bare
    # `ecmwf_aifs025` id returns HTTP 200 with an all-null array (verified
    # 2026-10-01: 0/24 non-null values), which silently kept AIFS out of
    # LIVE mode while GFS and IFS worked. Keep the `_single` suffix.
    "aifs-single": ("ecmwf_aifs025_single", "ECMWF AIFS 0.25 Single"),
}
AGGREGATION_METHOD = "openmeteo_representative_point_mean"
PRECIPITATION_AGGREGATION = "fixed_lead_hourly_sum"
REPRESENTATIVE_POINTS = {
    region: [
        (round((bounds["lat_range"][0] * 3 + bounds["lat_range"][1]) / 4, 3),
         round((bounds["lon_range"][0] * 3 + bounds["lon_range"][1]) / 4, 3)),
        (round((bounds["lat_range"][0] + bounds["lat_range"][1]) / 2, 3),
         round((bounds["lon_range"][0] + bounds["lon_range"][1]) / 2, 3)),
        (round((bounds["lat_range"][0] + bounds["lat_range"][1] * 3) / 4, 3),
         round((bounds["lon_range"][0] + bounds["lon_range"][1] * 3) / 4, 3)),
    ]
    for region, bounds in PILOT_ZONES.items()
}


def _hourly_names() -> list[str]:
    names = []
    for lead in range(1, 6):
        names.extend((
            f"temperature_2m_previous_day{lead}",
            f"wind_speed_10m_previous_day{lead}",
            f"precipitation_previous_day{lead}",
        ))
    return names


def _request(model_id: str, start_date: str, end_date: str) -> list[dict]:
    points = [point for region_points in REPRESENTATIVE_POINTS.values() for point in region_points]
    query = urllib.parse.urlencode({
        "latitude": ",".join(str(lat) for lat, _ in points),
        "longitude": ",".join(str(lon) for _, lon in points),
        "start_date": start_date,
        "end_date": end_date,
        "timezone": "GMT",
        "models": model_id,
        "hourly": ",".join(_hourly_names()),
    })
    request = urllib.request.Request(
        f"{API_URL}?{query}", headers={"User-Agent": "synoptiq-openmeteo/1.0"}
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if isinstance(payload, dict) and payload.get("error"):
        raise RuntimeError(f"Open-Meteo {model_id} request failed: {payload.get('reason', 'unknown error')}")
    records = payload if isinstance(payload, list) else [payload]
    if len(records) != len(points):
        raise RuntimeError(f"Open-Meteo returned {len(records)} points; expected {len(points)}")
    for record, (lat, lon) in zip(records, points):
        record["_requested_lat"] = lat
        record["_requested_lon"] = lon
    return records


def _finite_values(values: list[float], label: str) -> np.ndarray:
    array = np.asarray(values, dtype=float)
    if array.size != 24 or not np.isfinite(array).all():
        raise RuntimeError(f"Open-Meteo {label} returned missing or non-finite hourly values")
    return array


def _rows_for_date(model: str, target_date: str, target_index: int,
                   records: list[dict]) -> list[dict]:
    model_id, source_model = MODELS[model]
    rows = []
    point_index = 0
    start = target_index * 24
    end = start + 24
    for region, points in REPRESENTATIVE_POINTS.items():
        region_records = records[point_index:point_index + len(points)]
        point_index += len(points)
        for lead in REQUIRED_LEADS:
            suffix = f"previous_day{lead // 24}"
            temperatures = []
            winds_kmh = []
            precipitation = []
            for record in region_records:
                hourly = record.get("hourly", {})
                temperatures.append(float(_finite_values(
                    hourly.get(f"temperature_2m_{suffix}", [])[start:end],
                    f"{model_id} temperature"
                )[0]))
                winds_kmh.append(float(_finite_values(
                    hourly.get(f"wind_speed_10m_{suffix}", [])[start:end],
                    f"{model_id} wind"
                )[0]))
                precipitation.append(float(_finite_values(
                    hourly.get(f"precipitation_{suffix}", [])[start:end],
                    f"{model_id} precipitation"
                ).sum()))
            rows.append({
                "model": model,
                "run_date": (date.fromisoformat(target_date) - timedelta(hours=lead)).strftime("%Y-%m-%d"),
                "run_hour": 0,
                "step": lead,
                "zone": region,
                "t2m_c": float(np.mean(temperatures)),
                "wind_ms": float(np.mean(winds_kmh)) / 3.6,
                "precip_mm": float(np.mean(precipitation)),
                "source_provider": "Open-Meteo",
                "source_model": source_model,
                "aggregation_method": AGGREGATION_METHOD,
                "precipitation_aggregation": PRECIPITATION_AGGREGATION,
                "selected_valid_timestamp": f"{target_date}T00:00:00Z",
                "representative_points": json.dumps(points),
            })
    return rows


def _rows_from_records(model: str, target_dates: list[str], records: list[dict]) -> tuple[dict[str, list[dict]], dict[str, str]]:
    if model not in MODELS:
        raise ValueError(f"Unsupported Open-Meteo model: {model}")
    model_id, source_model = MODELS[model]
    rows_by_date = {target_date: [] for target_date in target_dates}
    missing_dates = {}
    point_count = sum(len(points) for points in REPRESENTATIVE_POINTS.values())
    expected_hours = len(target_dates) * 24
    if any(len(record.get("hourly", {}).get("time", [])) != expected_hours for record in records):
        raise RuntimeError(f"Open-Meteo {model_id} returned an unexpected hourly date span")
    for target_index, target_date in enumerate(target_dates):
        try:
            rows_by_date[target_date] = _rows_for_date(model, target_date, target_index, records)
        except RuntimeError as exc:
            missing_dates[target_date] = str(exc)
    return rows_by_date, missing_dates


def fetch_day(model: str, target_date: str) -> list[dict]:
    model_id = MODELS[model][0]
    rows, missing = _rows_from_records(model, [target_date], _request(model_id, target_date, target_date))
    if missing:
        raise RuntimeError(missing[target_date])
    return rows[target_date]


def fetch_live_cycle(model: str, run_date: str) -> list[dict]:
    """Fetch current real forecasts in the raw shape consumed by live ingestion."""
    model_id, source_model = MODELS[model]
    points = [point for region_points in REPRESENTATIVE_POINTS.values() for point in region_points]
    query = urllib.parse.urlencode({
        "latitude": ",".join(str(lat) for lat, _ in points),
        "longitude": ",".join(str(lon) for _, lon in points),
        "forecast_days": 7,
        "timezone": "GMT",
        "models": model_id,
        "hourly": "temperature_2m,wind_speed_10m,precipitation",
    })
    request = urllib.request.Request(f"{LIVE_API_URL}?{query}", headers={"User-Agent": "synoptiq-live/1.0"})
    with urllib.request.urlopen(request, timeout=180) as response:
        payload = json.loads(response.read().decode("utf-8"))
    records = payload if isinstance(payload, list) else [payload]
    rows = []
    point_index = 0
    for region, region_points in REPRESENTATIVE_POINTS.items():
        region_records = records[point_index:point_index + len(region_points)]
        point_index += len(region_points)
        for day_number in range(1, 6):
            start = day_number * 24
            end = start + 24
            temperatures = []
            winds = []
            precipitations = []
            for record in region_records:
                hourly = record["hourly"]
                temperatures.append(np.asarray(hourly["temperature_2m"][start:end], dtype=float)[[0, 6, 12, 18]])
                winds.append(np.asarray(hourly["wind_speed_10m"][start:end], dtype=float))
                precipitations.append(np.asarray(hourly["precipitation"][start:end], dtype=float))
            if any(not np.isfinite(values).all() for group in (temperatures, winds, precipitations) for values in group):
                raise RuntimeError(
                    f"Open-Meteo live model '{model_id}' returned non-finite values "
                    f"(an all-null response usually means the model id is wrong or "
                    f"that model is not served on this endpoint; check MODELS)"
                )
            rows.append({
                "model": model, "run_date": run_date, "run_hour": 0,
                "step": 24 * day_number, "zone": region,
                "t2m_c": float(np.mean(np.concatenate(temperatures))),
                "wind_ms": float(np.max(np.concatenate(winds))) / 3.6,
                "precip_mm": float(np.mean([values.sum() for values in precipitations])),
                "source_provider": "Open-Meteo", "source_model": source_model,
            })
    return rows


def output_path(model: str, target_date: str) -> Path:
    directory = Path(CACHE_DIR) / f"{model}_openmeteo"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"openmeteo_{model}_{target_date}.parquet"


def fetch_range(model: str, start: str, end: str, batch_size: int = 14) -> dict:
    started = time.monotonic()
    current = date.fromisoformat(start)
    final = date.fromisoformat(end)
    written = 0
    requests = 0
    missing_dates = {}
    while current <= final:
        batch = []
        while current <= final and len(batch) < batch_size:
            target_date = current.isoformat()
            path = output_path(model, target_date)
            if path.exists():
                try:
                    cached = pd.read_parquet(path)
                    if len(cached) == len(PILOT_ZONES) * len(REQUIRED_LEADS):
                        current += timedelta(days=1)
                        continue
                except Exception:
                    path.unlink()
            batch.append(target_date)
            current += timedelta(days=1)
        if not batch:
            continue
        records = _request(MODELS[model][0], batch[0], batch[-1])
        requests += 1
        rows_by_date, batch_missing = _rows_from_records(model, batch, records)
        missing_dates.update(batch_missing)
        for target_date, rows in rows_by_date.items():
            if not rows:
                continue
            pd.DataFrame(rows).to_parquet(output_path(model, target_date), index=False, compression="zstd")
            written += 1
            print(f"  open-meteo {model} {target_date}: {len(rows)} rows", flush=True)
    manifest = Path(CACHE_DIR) / "openmeteo_manifest.json"
    manifest.write_text(json.dumps({
        "source_provider": "Open-Meteo",
        "models": {name: value[1] for name, value in MODELS.items()},
        "aggregation_method": AGGREGATION_METHOD,
        "precipitation_aggregation": PRECIPITATION_AGGREGATION,
        "representative_points": REPRESENTATIVE_POINTS,
        "requested_window": {"start": start, "end": end},
        "missing_dates": missing_dates,
    }, indent=2), encoding="utf-8")
    return {
        "model": model,
        "completed_dates": written,
        "requests": requests,
        "missing_dates": missing_dates,
        "elapsed_seconds": round(time.monotonic() - started, 2),
    }


def run_smoke(models: list[str]) -> None:
    smoke_dates = [
        (date(2025, 2, 26) + timedelta(days=offset)).isoformat()
        for offset in range(3)
    ]
    for model in models:
        if model not in MODELS:
            raise ValueError(f"Unsupported Open-Meteo model: {model}")
        records = _request(MODELS[model][0], smoke_dates[0], smoke_dates[-1])
        rows_by_date, missing_dates = _rows_from_records(model, smoke_dates, records)
        if missing_dates:
            raise RuntimeError(f"Open-Meteo smoke {model} has missing dates: {missing_dates}")
        rows = [row for rows in rows_by_date.values() for row in rows]
        expected = len(smoke_dates) * len(PILOT_ZONES) * len(REQUIRED_LEADS)
        if len(rows) != expected:
            raise RuntimeError(f"Open-Meteo smoke {model}: expected {expected} rows, got {len(rows)}")
        if {row["zone"] for row in rows} != set(PILOT_ZONES):
            raise RuntimeError(f"Open-Meteo smoke {model}: region coverage is incomplete")
        if {row["step"] for row in rows} != set(REQUIRED_LEADS):
            raise RuntimeError(f"Open-Meteo smoke {model}: lead coverage is incomplete")
        if {row["selected_valid_timestamp"][:10] for row in rows} != set(smoke_dates):
            raise RuntimeError(f"Open-Meteo smoke {model}: date coverage is incomplete")
        print(f"smoke {model}: PASS ({len(rows)} rows, one batched request)", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default=TRAINING_START.isoformat())
    parser.add_argument("--end", default=TRAINING_END.isoformat())
    parser.add_argument("--models", default=",".join(MODELS))
    parser.add_argument("--smoke-only", action="store_true")
    parser.add_argument("--real-prototype", action="store_true")
    args = parser.parse_args()
    if args.smoke_only:
        run_smoke([model.strip() for model in args.models.split(",")])
        return
    if args.real_prototype:
        validate_prototype_window(args.start, args.end)
    else:
        validate_window(args.start, args.end)
    models = [model.strip() for model in args.models.split(",")]
    with ThreadPoolExecutor(max_workers=min(2, len(models))) as executor:
        results = list(executor.map(lambda model: fetch_range(model, args.start, args.end), models))
    print(json.dumps({"results": results, "total_requests": sum(item["requests"] for item in results)}, indent=2))


if __name__ == "__main__":
    main()
