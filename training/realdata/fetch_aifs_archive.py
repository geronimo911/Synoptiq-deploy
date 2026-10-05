"""Fetch real ECMWF AIFS Single forecasts from the public Icechunk archive."""
from __future__ import annotations

import argparse
import json
import os
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr
import icechunk

from common import CACHE_DIR, PILOT_ZONES
from fetch_openmeteo import REPRESENTATIVE_POINTS

ARCHIVE_BUCKET = "dynamical-ecmwf-aifs-single"
ARCHIVE_PREFIX = "ecmwf-aifs-single-forecast/v0.1.0.icechunk/"
ARCHIVE_SOURCE = f"s3://{ARCHIVE_BUCKET}/{ARCHIVE_PREFIX}"
MODEL = "aifs-single"
MODEL_NAME = "ECMWF AIFS Single"
TRANSPORT = "AWS public Icechunk/Zarr archive"
LEADS = tuple(range(6, 145, 6))
REQUIRED_LEADS = (24, 48, 72, 96, 120)
VARIABLES = ("temperature_2m", "precipitation_surface", "wind_u_10m", "wind_v_10m")
OUTPUT_DIR = Path(CACHE_DIR) / "aifs-single"


def open_archive():
    storage = icechunk.s3_storage(
        bucket=ARCHIVE_BUCKET,
        prefix=ARCHIVE_PREFIX,
        region="us-west-2",
        anonymous=True,
    )
    repository = icechunk.Repository.open(storage)
    return xr.open_zarr(repository.readonly_session("main").store, chunks=None)


def _points():
    return [point for points in REPRESENTATIVE_POINTS.values() for point in points]


def _select(ds: xr.Dataset, dates: list[str]) -> xr.Dataset:
    lats = xr.DataArray([lat for lat, _ in _points()], dims="point")
    lons = xr.DataArray([lon for _, lon in _points()], dims="point")
    lead_values = [np.timedelta64(lead, "h") for lead in LEADS]
    selected = ds[list(VARIABLES)].sel(
        init_time=[np.datetime64(f"{day}T00:00:00") for day in dates],
        lead_time=lead_values,
        latitude=lats,
        longitude=lons,
        method="nearest",
    ).load()
    for variable in VARIABLES:
        values = np.asarray(selected[variable].values, dtype=float)
        if values.shape != (len(dates), len(LEADS), len(_points())):
            raise RuntimeError(f"AIFS archive {variable} returned shape {values.shape}")
        if not np.isfinite(values).all():
            raise RuntimeError(f"AIFS archive {variable} contains non-finite values")
    return selected


def _rows(selected: xr.Dataset, dates: list[str]) -> list[dict]:
    rows = []
    point_index = 0
    region_slices = {}
    for region, points in REPRESENTATIVE_POINTS.items():
        region_slices[region] = slice(point_index, point_index + len(points))
        point_index += len(points)
    for date_index, run_date in enumerate(dates):
        precipitation_rate = np.asarray(selected["precipitation_surface"].isel(init_time=date_index), dtype=float)
        cumulative = np.cumsum(precipitation_rate * 6 * 3600, axis=0)
        for region, point_slice in region_slices.items():
            temperature = np.asarray(selected["temperature_2m"].isel(init_time=date_index), dtype=float)[:, point_slice]
            u_wind = np.asarray(selected["wind_u_10m"].isel(init_time=date_index), dtype=float)[:, point_slice]
            v_wind = np.asarray(selected["wind_v_10m"].isel(init_time=date_index), dtype=float)[:, point_slice]
            wind = np.hypot(u_wind, v_wind)
            precip = cumulative[:, point_slice]
            for lead_index, lead in enumerate(LEADS):
                rows.append({
                    "model": MODEL,
                    "run_date": run_date,
                    "run_hour": 0,
                    "step": lead,
                    "zone": region,
                    "t2m_c": float(temperature[lead_index].mean()),
                    "wind_ms": float(wind[lead_index].mean()),
                    "tp_cum_m": float(precip[lead_index].mean()),
                    "model_generation": "ECMWF AIFS Single archive v0.1.0",
                    "source_provider": "ECMWF / public archive",
                    "source_model": MODEL_NAME,
                    "transport": TRANSPORT,
                    "archive_source": ARCHIVE_SOURCE,
                    "provenance": True,
                })
    return rows


def _valid_frame(frame: pd.DataFrame, dates: list[str]) -> bool:
    expected = len(dates) * len(PILOT_ZONES) * len(LEADS)
    keys = ["run_date", "run_hour", "step", "zone"]
    return (
        len(frame) == expected
        and set(frame["run_date"].astype(str)) == set(dates)
        and set(frame["zone"].astype(str)) == set(PILOT_ZONES)
        and set(frame["step"].astype(int)) == set(LEADS)
        and not frame.duplicated(keys).any()
        and not frame[["t2m_c", "wind_ms", "tp_cum_m"]].isna().any().any()
        and np.isfinite(frame[["t2m_c", "wind_ms", "tp_cum_m"]].to_numpy(dtype=float)).all()
    )


def fetch_range(start: str, end: str, batch_days: int = 14) -> dict:
    current = date.fromisoformat(start)
    final = date.fromisoformat(end)
    completed = 0
    requests = 0
    missing = []
    ds = open_archive()
    try:
        while current <= final:
            dates = []
            while current <= final and len(dates) < batch_days:
                dates.append(current.isoformat())
                current += timedelta(days=1)
            target_dir = OUTPUT_DIR
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / f"aifs_{dates[0]}_{dates[-1]}.parquet"
            if target.exists():
                try:
                    cached = pd.read_parquet(target)
                    if _valid_frame(cached, dates):
                        completed += len(dates)
                        continue
                except Exception:
                    pass
            try:
                frame = pd.DataFrame(_rows(_select(ds, dates), dates))
                if not _valid_frame(frame, dates):
                    raise RuntimeError(f"AIFS chunk validation failed for {dates[0]}..{dates[-1]}")
                temporary = target.with_suffix(".parquet.part")
                frame.to_parquet(temporary, index=False, compression="zstd")
                temporary.replace(target)
                completed += len(dates)
                requests += 1
                print(f"  AIFS archive {dates[0]}..{dates[-1]}: {len(frame)} rows", flush=True)
            except Exception as exc:
                missing.extend(dates)
                print(f"  AIFS archive FAILED {dates[0]}..{dates[-1]}: {type(exc).__name__}: {exc}", flush=True)
    finally:
        ds.close()
    manifest = {
        "weather_model": "AIFS",
        "model_family": MODEL_NAME,
        "source_provider": "ECMWF / public archive",
        "transport": TRANSPORT,
        "archive_source": ARCHIVE_SOURCE,
        "provenance": True,
        "requested_window": {"start": start, "end": end},
        "leads_hours": list(REQUIRED_LEADS),
        "chunk_leads_hours": list(LEADS),
        "missing_dates": missing,
    }
    (OUTPUT_DIR / "aifs_archive_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return {"completed_dates": completed, "requests": requests, "missing_dates": missing}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    args = parser.parse_args()
    print(json.dumps(fetch_range(args.start, args.end), indent=2))


if __name__ == "__main__":
    main()
