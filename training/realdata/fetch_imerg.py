"""Fetch real NASA GPM IMERG V07 Late rainfall with Earthaccess/OPeNDAP."""
from __future__ import annotations
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import numpy as np
import requests

from common import PILOT_ZONES, CACHE_DIR, crop

PRODUCT = "GPM_3IMERGDL"
VERSION = "07"
TRUTH_DATASET = "NASA GPM IMERG"
TRUTH_PRODUCT = "V07 Late"
TRUTH_ROLE = "precipitation_reference"
TRANSPORT = "GES DISC OPeNDAP spatial subset"
REGION_NAMES = {"KWG": "kerala_western_ghats", "BOB": "bay_of_bengal_east_coast",
                "IGP": "indo_gangetic_plains"}
MAX_DOWNLOAD_THREADS = 1
MAX_DATE_WORKERS = 2
DATE_TIMEOUT_SECONDS = 300


def _is_netcdf(content: bytes) -> bool:
    return content.startswith(b"CDF") or content.startswith(b"\x89HDF\r\n\x1a\n")


def _valid_imerg_file(path: str) -> bool:
    if not os.path.isfile(path) or os.path.getsize(path) < 8:
        return False


def _valid_output(path: Path, day: str) -> bool:
    try:
        frame = pd.read_parquet(path)
        return (
            len(frame) == len(PILOT_ZONES)
            and set(frame["zone"].astype(str)) == set(PILOT_ZONES)
            and set(frame["valid_date"].astype(str).str[:10]) == {day}
            and not frame[["valid_date", "zone", "rain_mm"]].isna().any().any()
            and np.isfinite(frame["rain_mm"].astype(float)).all()
        )
    except Exception:
        return False
    try:
        with open(path, "rb") as file:
            if not _is_netcdf(file.read(8)):
                return False
        import xarray as xr
        with xr.open_dataset(path, engine="netcdf4") as dataset:
            has_precipitation = any(name in dataset.data_vars for name in ("precipitation", "precipitationCal"))
            has_coordinates = {"lat", "lon"}.issubset(dataset.coords) or {"latitude", "longitude"}.issubset(dataset.coords)
            variable = next((name for name in ("precipitation", "precipitationCal") if name in dataset.data_vars), None)
            return (has_precipitation and has_coordinates and dataset.sizes.get("time", 0) > 0
                    and variable is not None and int(dataset[variable].count()) > 0)
    except Exception:
        return False


def _bbox(region: str) -> tuple[float, float, float, float]:
    bounds = PILOT_ZONES[REGION_NAMES[region]]
    lon0, lon1 = bounds["lon_range"]
    lat0, lat1 = bounds["lat_range"]
    return lon0, lat0, lon1, lat1


def _earthaccess():
    import earthaccess

    token = os.getenv("EARTHDATA_TOKEN", "").strip()
    username = os.getenv("NASA_EARTHDATA_USERNAME", "").strip()
    password = os.getenv("NASA_EARTHDATA_PASSWORD", "").strip()
    if token:
        os.environ["EARTHDATA_TOKEN"] = token
    elif username and password:
        os.environ["EARTHDATA_USERNAME"] = username
        os.environ["EARTHDATA_PASSWORD"] = password
    else:
        raise RuntimeError("Set EARTHDATA_TOKEN or NASA_EARTHDATA_USERNAME/NASA_EARTHDATA_PASSWORD")
    auth = earthaccess.login(strategy="environment", persist=False)
    if not auth.authenticated:
        raise RuntimeError("Earthdata authentication failed")
    return earthaccess


def search(day_start: str, day_end: str, region: str | None = None, count: int = 2000):
    earthaccess = _earthaccess()
    if region:
        regions = [region]
    else:
        regions = list(REGION_NAMES)
    found = {}
    for code in regions:
        granules = earthaccess.search_data(
            short_name=PRODUCT, version=VERSION,
            temporal=(day_start, day_end), bounding_box=_bbox(code), count=count,
        )
        for granule in granules:
            found[str(granule)] = granule
    return list(found.values())


def _opendap_base(granule) -> str:
    title = str(granule.get("umm", {}).get("GranuleUR", ""))
    filename = title.split(":", 1)[-1]
    day = filename.split(".3IMERG.", 1)[-1][:8]
    return (
        "https://gpm1.gesdisc.eosdis.nasa.gov/opendap/"
        f"GPM_L3/{PRODUCT}.{VERSION}/{day[:4]}/{day[4:6]}/{filename}"
    )


def _parse_opendap_values(text: str) -> list[float]:
    values = []
    for line in text.splitlines():
        if "precipitation.precipitation[" not in line or "], " not in line:
            continue
        for token in line.rsplit("], ", 1)[-1].split(","):
            try:
                values.append(float(token.strip()))
            except ValueError:
                continue
    return values


def _opendap_zone_means(day: str, granule) -> dict[str, float]:
    session = _earthaccess().login(strategy="environment", persist=False).get_session()
    base = _opendap_base(granule)
    means = {}
    for region, bounds in PILOT_ZONES.items():
        lat0, lat1 = bounds["lat_range"]
        lon0, lon1 = bounds["lon_range"]
        lon_start = int(np.ceil((lon0 + 179.95) / 0.1 - 1e-9))
        lon_end = int(np.floor((lon1 + 179.95) / 0.1 + 1e-9))
        lat_start = int(np.ceil((lat0 + 89.95) / 0.1 - 1e-9))
        lat_end = int(np.floor((lat1 + 89.95) / 0.1 + 1e-9))
        constraint = f"precipitation[0][{lon_start}:1:{lon_end}][{lat_start}:1:{lat_end}]"
        url = base + ".ascii?" + urllib.parse.quote(constraint, safe="")
        last_error = None
        for attempt in range(3):
            try:
                response = session.get(url, timeout=90)
                response.raise_for_status()
                break
            except (requests.RequestException, ConnectionError, TimeoutError, OSError) as exc:
                last_error = exc
                if attempt == 2:
                    raise
                time.sleep(2 ** attempt)
        values = [value for value in _parse_opendap_values(response.text)
                  if np.isfinite(value) and value >= 0]
        if not values:
            raise RuntimeError(f"NASA {TRUTH_PRODUCT} returned no finite cells for {region} on {day}")
        means[region] = float(np.mean(values))
    return means


def _download_granules(granules) -> list[str]:
    if not granules:
        return []
    earthaccess = _earthaccess()
    local = Path(CACHE_DIR) / "imerg_raw"
    local.mkdir(parents=True, exist_ok=True)
    last_error = None
    for attempt in range(3):
        try:
            paths = earthaccess.download(granules, local_path=str(local), threads=MAX_DOWNLOAD_THREADS)
            checked = [str(path) for path in paths]
            invalid = [path for path in checked if not _valid_imerg_file(path)]
            if invalid:
                raise RuntimeError("downloaded response is not a valid non-empty IMERG NetCDF/HDF file")
            return checked
        except Exception as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"Earthaccess IMERG download failed after 3 attempts: {last_error}") from last_error


def download(day: str, product: str = "GPM_3IMERGDF.07") -> str:
    if product != f"{PRODUCT}.{VERSION}":
        raise ValueError(f"IMERG product must be {PRODUCT}.{VERSION}")
    existing = [path for path in (Path(CACHE_DIR) / "imerg_raw").glob("*")
                if day.replace("-", "") in path.name and _valid_imerg_file(str(path))]
    if existing:
        return str(existing[0])
    granules = search(day, day)
    if not granules:
        raise RuntimeError(f"No {PRODUCT} V{VERSION} granule found for {day} in the requested regions")
    paths = _download_granules(granules)
    if not paths:
        raise RuntimeError(f"Earthaccess returned no downloaded files for {day}")
    return paths[0]


def zone_means_for_day(day: str, region: str | None = None,
                       product: str = "GPM_3IMERGDL.07") -> list[dict]:
    if product != f"{PRODUCT}.{VERSION}":
        raise ValueError(f"IMERG product must be {PRODUCT}.{VERSION}")
    region_code = next((code for code, name in REGION_NAMES.items() if name == region), None)
    granules = search(day, day, region=region_code)
    if not granules:
        raise RuntimeError(f"No {PRODUCT} V{VERSION} granule found for {day}")
    means = _opendap_zone_means(day, granules[0])
    selected = [region] if region else list(PILOT_ZONES)
    return [dict(valid_date=day, zone=zone, rain_mm=round(max(0.0, means[zone]), 3))
            for zone in selected]


def smoke_test(day: str) -> int:
    status = {key: "FAIL" for key in (
        "IMERG_AUTH", "IMERG_SEARCH", "IMERG_GRANULE", "IMERG_DOWNLOAD",
        "IMERG_NETCDF", "IMERG_PRECIP_VARIABLE", "KWG", "BOB", "IGP",
    )}
    granules = []
    try:
        _earthaccess()
        status["IMERG_AUTH"] = "PASS"
    except Exception as exc:
        print(f"IMERG_ERROR={type(exc).__name__}: {exc}")
        _print_smoke(status)
        return 1
    try:
        granules = search(day, day)
        status["IMERG_SEARCH"] = "PASS"
        if granules:
            status["IMERG_GRANULE"] = "PASS"
        else:
            raise RuntimeError(f"CMR returned no {PRODUCT} V{VERSION} granules for {day}")
    except Exception as exc:
        print(f"IMERG_ERROR={type(exc).__name__}: {exc}")
        _print_smoke(status)
        return 1
    try:
        rows = zone_means_for_day(day)
        status["IMERG_DOWNLOAD"] = "PASS"
        status["IMERG_NETCDF"] = "PASS"
        status["IMERG_PRECIP_VARIABLE"] = "PASS"
        if len(rows) != len(PILOT_ZONES) or not all(np.isfinite(row["rain_mm"]) for row in rows):
            raise RuntimeError("NASA IMERG OPeNDAP returned incomplete or non-finite regional values")
    except Exception as exc:
        print(f"IMERG_ERROR={type(exc).__name__}: {exc}")
        _print_smoke(status)
        return 1
    try:
        for code, region_name in REGION_NAMES.items():
            if any(row["zone"] == region_name for row in rows):
                status[code] = "PASS"
    except Exception as exc:
        print(f"IMERG_ERROR={type(exc).__name__}: {exc}")
    _print_smoke(status)
    return 0 if all(value == "PASS" for value in status.values()) else 1


def _print_smoke(status: dict[str, str]) -> None:
    for key, value in status.items():
        print(f"{key}={value}")


def _write_date(day: str, product: str) -> None:
    out = Path(CACHE_DIR) / "imerg"
    out.mkdir(parents=True, exist_ok=True)
    target = out / f"imerg_{day}.parquet"
    rows = zone_means_for_day(day, product=product)
    frame = pd.DataFrame(rows)
    temporary = target.with_suffix(target.suffix + ".part")
    frame.to_parquet(temporary, index=False, compression="zstd")
    if not _valid_output(temporary, day):
        temporary.unlink(missing_ok=True)
        raise RuntimeError(f"IMERG output validation failed for {day}")
    temporary.replace(target)


def _date_worker(day: str, product: str) -> tuple[str, str, str | None]:
    try:
        completed = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--start", day,
             "--worker-date", day, "--product", product],
            cwd=str(Path(__file__).resolve().parents[2]),
            env=os.environ.copy(), capture_output=True, text=True,
            timeout=DATE_TIMEOUT_SECONDS, check=False,
        )
        if completed.returncode:
            error = (completed.stderr or completed.stdout or "worker failed").strip()
            return day, "FAILED", f"worker exit {completed.returncode}: {error[-400:]}"
        return day, "VALID", None
    except subprocess.TimeoutExpired:
        return day, "FAILED", f"timeout after {DATE_TIMEOUT_SECONDS}s"
    except Exception as exc:
        return day, "FAILED", f"{type(exc).__name__}: {str(exc)[:400]}"


def _run_bounded_dates(start: str, end: str, product: str) -> None:
    d = datetime.strptime(start, "%Y-%m-%d").date()
    final = datetime.strptime(end, "%Y-%m-%d").date()
    pending = []
    while d <= final:
        target = Path(CACHE_DIR) / "imerg" / f"imerg_{d.isoformat()}.parquet"
        if _valid_output(target, d.isoformat()):
            print(f"{d.isoformat()} VALID", flush=True)
        else:
            print(f"{d.isoformat()} MISSING", flush=True)
            pending.append(d.isoformat())
        d += timedelta(days=1)

    failures = {}
    with ThreadPoolExecutor(max_workers=MAX_DATE_WORKERS) as executor:
        futures = {
            executor.submit(_date_worker, day, product): day for day in pending
        }
        for future in as_completed(futures):
            day = futures[future]
            try:
                completed_day, status, error = future.result(timeout=DATE_TIMEOUT_SECONDS)
            except Exception as exc:
                completed_day, status, error = day, "FAILED", f"{type(exc).__name__}: {str(exc)[:400]}"
            print(f"{completed_day} {status}", flush=True)
            if status != "VALID":
                failures[completed_day] = error

    report = Path(CACHE_DIR) / "imerg_failures.json"
    report.write_text(json.dumps(failures, indent=2), encoding="utf-8")
    if failures:
        raise SystemExit(f"IMERG failed dates: {', '.join(sorted(failures))}; see {report}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=False)
    ap.add_argument("--product", default=f"{PRODUCT}.{VERSION}",
                    choices=(f"{PRODUCT}.{VERSION}",))
    ap.add_argument("--region", choices=("KWG", "BOB", "IGP"),
                    help="smoke only one Synoptiq region; defaults to all three")
    ap.add_argument("--smoke-test", action="store_true")
    ap.add_argument("--discover-coverage", action="store_true")
    ap.add_argument("--worker-date", help=argparse.SUPPRESS)
    args = ap.parse_args()

    if args.smoke_test:
        raise SystemExit(smoke_test(args.start))
    if args.discover_coverage:
        granules = search(args.start, args.end or datetime.utcnow().strftime("%Y-%m-%d"))
        if not granules:
            raise SystemExit("CMR returned no IMERG granules in the requested temporal/bounding-box range")
        dates = sorted({str(value)[:10] for granule in granules
                for value in [granule.get("umm", {}).get("TemporalExtent", {})
                          .get("RangeDateTime", {}).get("BeginningDateTime", "")] if value})
        if not dates:
            raise SystemExit("CMR granules did not expose parseable start dates")
        print(f"IMERG_FIRST_AVAILABLE={dates[0]}")
        print(f"IMERG_LAST_AVAILABLE={dates[-1]}")
        return

    if args.worker_date:
        _write_date(args.worker_date, args.product)
        return

    _run_bounded_dates(args.start, args.end or args.start, args.product)
    print("done")


if __name__ == "__main__":
    main()
