"""Fetch ERA5 truth fields from Open-Meteo's Historical Weather API."""
from __future__ import annotations
import argparse
import json
import math
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from common import PILOT_ZONES, CACHE_DIR

from fetch_openmeteo import REPRESENTATIVE_POINTS

API_URL = "https://archive-api.open-meteo.com/v1/archive"
SOURCE_DATASET = "ERA5"
TRANSPORT = "Open-Meteo Historical Weather API"
RESOLUTION = "ERA5 0.25°"
VARIABLES = ("temperature_2m", "precipitation", "wind_speed_10m")
SYNOPTIC_HOURS = (0, 6, 12, 18)
OUTPUT_ROOT = Path(CACHE_DIR) / "era5"
REGIONAL_CACHE = OUTPUT_ROOT / "openmeteo"


def _request(start: str, end: str, regions: list[str]) -> list[dict]:
    points = [point for region in regions for point in REPRESENTATIVE_POINTS[region]]
    query = urllib.parse.urlencode({
        "latitude": ",".join(str(lat) for lat, _ in points),
        "longitude": ",".join(str(lon) for _, lon in points),
        "start_date": start,
        "end_date": end,
        "timezone": "GMT",
        "models": "era5",
        "hourly": ",".join(VARIABLES),
    })
    request = urllib.request.Request(
        f"{API_URL}?{query}", headers={"User-Agent": "synoptiq-era5/1.0"}
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if isinstance(payload, dict) and payload.get("error"):
        raise RuntimeError(f"Open-Meteo ERA5 request failed: {payload.get('reason', 'unknown error')}")
    records = payload if isinstance(payload, list) else [payload]
    if len(records) != len(points):
        raise RuntimeError(f"Open-Meteo ERA5 returned {len(records)} points; expected {len(points)}")
    expected_hours = (date.fromisoformat(end) - date.fromisoformat(start)).days * 24 + 24
    expected_times = [
        (datetime.fromisoformat(start) + timedelta(hours=hour)).strftime("%Y-%m-%dT%H:%M")
        for hour in range(expected_hours)
    ]
    for index, record in enumerate(records):
        hourly = record.get("hourly", {})
        if hourly.get("time") != expected_times:
            raise RuntimeError(f"Open-Meteo ERA5 point {index} returned an unexpected UTC timestamp span")
        units = record.get("hourly_units", {})
        expected_units = {"temperature_2m": "°C", "precipitation": "mm", "wind_speed_10m": "km/h"}
        if any(units.get(variable) != unit for variable, unit in expected_units.items()):
            raise RuntimeError(f"Open-Meteo ERA5 point {index} returned unexpected units: {units}")
        for variable in VARIABLES:
            values = hourly.get(variable, [])
            if len(values) != expected_hours or any(
                value is None or not math.isfinite(float(value)) for value in values
            ):
                raise RuntimeError(f"Open-Meteo ERA5 point {index} has missing/non-finite {variable}")
    return records


def _rows_for_days(records: list[dict], start: str, requested_days: list[str],
                   regions: list[str]) -> list[dict]:
    start_date = date.fromisoformat(start)
    rows = []
    point_index = 0
    for region in regions:
        region_records = records[point_index:point_index + len(REPRESENTATIVE_POINTS[region])]
        point_index += len(REPRESENTATIVE_POINTS[region])
        for target in requested_days:
            target_date = date.fromisoformat(target)
            offset = (target_date - start_date).days * 24
            temperature_values = []
            wind_maxima = []
            for record in region_records:
                hourly = record["hourly"]
                temperature_values.extend(
                    float(hourly["temperature_2m"][offset + hour])
                    for hour in SYNOPTIC_HOURS
                )
                wind_maxima.append(max(
                    float(hourly["wind_speed_10m"][offset + hour])
                    for hour in SYNOPTIC_HOURS
                ))
            rows.append({
                "valid_date": target,
                "zone": region,
                "t2m_c": sum(temperature_values) / len(temperature_values),
                "wind_kmh": sum(wind_maxima) / len(wind_maxima),
            })
    return rows


def _atomic_parquet(frame: pd.DataFrame, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".part")
    frame.to_parquet(temporary, index=False, compression="zstd")
    temporary.replace(target)


def fetch_month(year: str, month: str, day: int | None = None,
                requested_days: list[str] | None = None,
                regions: list[str] | None = None, smoke: bool = False) -> dict[str, Path]:
    selected_regions = regions or list(PILOT_ZONES)
    unknown = sorted(set(selected_regions) - set(PILOT_ZONES))
    if unknown:
        raise ValueError("Unknown ERA5 project regions: " + ", ".join(unknown))
    days = requested_days or ([f"{day:02d}"] if day is not None else None)
    if days:
        requested = [date(int(year), int(month), int(value)).isoformat() for value in days]
    else:
        first = date(int(year), int(month), 1)
        next_month = date(int(year) + (int(month) == 12), int(month) % 12 + 1, 1)
        requested = [(first + timedelta(days=offset)).isoformat()
                     for offset in range((next_month - first).days)]
    start, end = requested[0], requested[-1]
    records = _request(start, end, selected_regions)
    rows = _rows_for_days(records, start, requested, selected_regions)
    expected_rows = len(requested) * len(selected_regions)
    if len(rows) != expected_rows:
        raise RuntimeError(f"ERA5 returned {len(rows)} daily region rows; expected {expected_rows}")
    frame = pd.DataFrame(rows)
    if frame.duplicated(["valid_date", "zone"]).any() or frame.isna().any().any():
        raise RuntimeError("ERA5 daily region rows contain duplicates or null truth values")
    output = {}
    span = f"{requested[0][-2:]}-{requested[-1][-2:]}"
    for region in selected_regions:
        target = REGIONAL_CACHE / f"era5_{year}{month}_{span}_{region}.parquet"
        _atomic_parquet(frame[frame["zone"] == region], target)
        output[region] = target
    manifest = {
        "source_dataset": SOURCE_DATASET,
        "transport": TRANSPORT,
        "resolution": RESOLUTION,
        "provenance": True,
        "model_parameter": "era5",
        "variables_requested": list(VARIABLES),
        "synoptic_hours_utc": list(SYNOPTIC_HOURS),
        "regions": {region: REPRESENTATIVE_POINTS[region] for region in selected_regions},
        "requested_window": {"start": start, "end": end},
        "requested_days": requested,
        "rows_validated": len(frame),
    }
    (OUTPUT_ROOT / "era5_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return output


def daily_zone_means(path: str, region_key: str) -> list[dict]:
    frame = pd.read_parquet(path)
    if set(frame["zone"].astype(str)) != {region_key}:
        raise RuntimeError(f"ERA5 regional cache {path} does not contain only {region_key}")
    return frame.to_dict(orient="records")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", help="comma list YYYY-MM")
    ap.add_argument("--start", help="inclusive UTC date; fetches only dates in this interval")
    ap.add_argument("--end", help="inclusive UTC date")
    ap.add_argument("--smoke-date", help="download one day for every project region")
    ap.add_argument("--plan-only", action="store_true", help="print region bounds and request counts only")
    args = ap.parse_args()
    if args.plan_only:
        for region, points in REPRESENTATIVE_POINTS.items():
            print(f"{region}: points={points}")
        return
    if args.smoke_date:
        year, month, day_text = args.smoke_date.split("-")
        fetch_month(year, month, day=int(day_text), smoke=True)
        print("ERA5 regional smoke passed for all project regions")
        return
    if args.start or args.end:
        if not args.start or not args.end:
            ap.error("--start and --end must be supplied together")
        start_date = pd.Timestamp(args.start).date()
        end_date = pd.Timestamp(args.end).date()
        if end_date < start_date:
            ap.error("--end must be on or after --start")
        month_days_by_month = {}
        current = start_date
        while current <= end_date:
            month_days_by_month.setdefault(current.strftime("%Y-%m"), []).append(f"{current.day:02d}")
            current += pd.Timedelta(days=1)
    elif args.months:
        month_days_by_month = {month: None for month in args.months.split(",")}
    else:
        ap.error("--months, --start/--end, or --smoke-date is required")
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    manifest_chunks = []
    for m, requested_days in month_days_by_month.items():
        year, month = m.split("-")
        paths = fetch_month(year, month, requested_days=requested_days)
        manifest_chunks.append(json.loads((OUTPUT_ROOT / "era5_manifest.json").read_text(encoding="utf-8")))
        rows = []
        for region, path in paths.items():
            rows.extend(daily_zone_means(str(path), region))
        span = f"{requested_days[0]}-{requested_days[-1]}" if requested_days else "all-days"
        out = OUTPUT_ROOT / f"era5_{year}{month}_{span}_zones.parquet"
        _atomic_parquet(pd.DataFrame(rows), out)
        print(f"  era5 {m} days={span}: {len(rows)} zone-days")
    if manifest_chunks:
        all_days = [day for chunk in manifest_chunks for day in chunk["requested_days"]]
        manifest = dict(manifest_chunks[-1])
        manifest.update({
            "requested_window": {"start": all_days[0], "end": all_days[-1]},
            "requested_days": all_days,
            "request_chunks": [chunk["requested_window"] for chunk in manifest_chunks],
            "rows_validated": sum(chunk["rows_validated"] for chunk in manifest_chunks),
        })
        (OUTPUT_ROOT / "era5_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print("done")


if __name__ == "__main__":
    main()
