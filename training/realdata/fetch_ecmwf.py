"""Retrieve real IFS/AIFS forecasts through ECMWF Open Data.

Standard AWS mirror path uses the official ``ecmwf.opendata`` client with
``source="aws", beta=False``. It requests only the required fields/steps,
downloads GRIB, decodes it with cfgrib, and crops the exact Synoptiq zones.
The public AWS Open Data archive is used for historical dates from
2025-02-25; this adapter never substitutes MARS or fabricated data.

IMPORTANT units (this is the #1 normalization gotcha):
    * ECMWF `tp` is TOTAL precipitation ACCUMULATED SINCE FORECAST START.
        cfgrib can expose it as kg m**-2 (numerically mm) rather than metres;
        values are normalized to metres before differencing and multiplying by 1000.
  * GFS APCP is a per-bucket accumulation in kg/m^2 (== mm).
  * 2t is in K, winds in m/s.

Notes:
    * AIFS Single v1 applies before 2026-05-12; v2 applies from that operational
        transition date. The generation is recorded with every forecast row.
    * MARS retrieval exists only as an explicit optional ``--source mars`` path.

Output: one Parquet row per (model, run_date, run_hour, step, zone):
  {model, run_date, run_hour, step, zone, t2m_c, wind_ms, tp_cum_m}

Usage:
  python fetch_ecmwf.py --start 2026-07-01 --end 2026-09-23
  python fetch_ecmwf.py --latest
Deps: pip install ecmwf-opendata xarray cfgrib eccodes pyarrow
"""
from __future__ import annotations
import argparse
import os
from pathlib import Path
from datetime import datetime, timedelta, timezone

import pandas as pd

from common import (PILOT_ZONES, STEPS, CACHE_DIR, ensure_dir, crop,
                    forecast_cache_is_complete, forecast_cache_missing)

OPEN_DATA_START = datetime(2025, 2, 25).date()
AIFS_V2_START = datetime(2026, 5, 12).date()
PARAMS = "tp/2t/10u/10v"


def _tp_to_metres(value: float, units: str) -> float:
    normalized_units = units.strip().lower().replace(" ", "").replace("**", "^")
    if normalized_units in {"m", "meter", "meters", "metre", "metres"}:
        return value
    if normalized_units in {"kgm^-2", "kgm-2", "kg/m^2", "mm"}:
        return value / 1000.0
    raise RuntimeError(f"Unsupported ECMWF total precipitation units: {units!r}")


def decode_zone_means(model: str, grib_bytes: bytes) -> dict:
    import numpy as np
    import xarray as xr
    tmp = os.path.join(CACHE_DIR, f"_ecmwf_{model}_tmp.grib2")
    ensure_dir(CACHE_DIR)
    with open(tmp, "wb") as f:
        f.write(grib_bytes)
    opened = []
    try:
        ds2 = xr.open_dataset(
            tmp, engine="cfgrib",
            backend_kwargs={"filter_by_keys": {"typeOfLevel": "heightAboveGround", "level": 2}},
        )
        ds10 = xr.open_dataset(
            tmp, engine="cfgrib",
            backend_kwargs={"filter_by_keys": {"typeOfLevel": "heightAboveGround", "level": 10}},
        )
        dssfc = xr.open_dataset(
            tmp, engine="cfgrib",
            backend_kwargs={"filter_by_keys": {"typeOfLevel": "surface"}},
        )
        opened.extend((ds2, ds10, dssfc))
        out = {}
        for zkey, z in PILOT_ZONES.items():
            tsub = crop(ds2, *z["lat_range"], *z["lon_range"])
            wsub = crop(ds10, *z["lat_range"], *z["lon_range"])
            psub = crop(dssfc, *z["lat_range"], *z["lon_range"])
            wind = np.sqrt(wsub["u10"] ** 2 + wsub["v10"] ** 2)
            out[zkey] = dict(
                t2m_c=float(tsub["t2m"].mean()) - 273.15,
                wind_ms=float(wind.mean()),
                tp_cum_m=_tp_to_metres(
                    float(psub["tp"].mean()), str(psub["tp"].attrs.get("units", ""))
                ),
            )
        return out
    finally:
        for dataset in opened:
            dataset.close()
        try:
            os.remove(tmp)
        except OSError:
            pass


def model_generation(model: str, run_date: str) -> str:
    if model == "aifs-single":
        return "AIFS Single v2" if datetime.strptime(run_date, "%Y-%m-%d").date() >= AIFS_V2_START else "AIFS Single v1"
    return "IFS Open Data"


def open_data_client(model: str, maximum_retries: int = 4, source: str = "aws"):
    from ecmwf.opendata import Client
    
    return Client(
        source=source,
        model=model,
        resol="0p25",
        beta=False,
        maximum_retries=maximum_retries,
        retry_after=5,
        use_server_retry_after=False,
    )


def validate_grib_fields(path: Path) -> tuple[bool, set[str]]:
    """Reject interrupted GRIB downloads missing required forecast fields."""
    import eccodes

    if not path.exists() or path.stat().st_size <= 4:
        return False, set()
    fields = set()
    try:
        with path.open("rb") as handle:
            while True:
                message = eccodes.codes_grib_new_from_file(handle)
                if message is None:
                    break
                try:
                    fields.add(str(eccodes.codes_get(message, "shortName")).lower())
                finally:
                    eccodes.codes_release(message)
    except Exception:
        return False, fields
    has_temperature = bool(fields.intersection({"t2m", "2t"}))
    has_u_wind = bool(fields.intersection({"u10", "10u"}))
    has_v_wind = bool(fields.intersection({"v10", "10v"}))
    has_precipitation = "tp" in fields
    return has_temperature and has_u_wind and has_v_wind and has_precipitation, fields


def fetch_cycle(model: str, run_date: str, hour: int, steps, source: str = "aws",
                maximum_retries: int = 4) -> list[dict]:
    if datetime.strptime(run_date, "%Y-%m-%d").date() < OPEN_DATA_START and source != "mars":
        raise RuntimeError("ECMWF Open Data training period begins 2025-02-25; no older data will be substituted.")
    if source == "mars":
        from ecmwf_archive import retrieve_step
        rows = []
        for step in steps:
            raw = Path(CACHE_DIR) / "raw" / model / f"{run_date}_{hour:02d}z_f{step:03d}.grib2"
            retrieve_step(model, run_date, hour, step, raw)
            for zkey, values in decode_zone_means(model, raw.read_bytes()).items():
                rows.append(dict(model=model, run_date=run_date, run_hour=hour, step=step,
                                 zone=zkey, model_generation=model_generation(model, run_date), **values))
        return rows
    ensure_dir(CACHE_DIR)
    client = open_data_client(model, maximum_retries=maximum_retries, source=source)
    rows = []
    for step in steps:
        raw = Path(CACHE_DIR) / "raw" / model / f"{run_date}_{hour:02d}z_f{step:03d}.grib2"
        raw.parent.mkdir(parents=True, exist_ok=True)
        valid_cached, _ = validate_grib_fields(raw)
        if not valid_cached:
            if raw.exists():
                raw.unlink()
            partial = raw.with_suffix(raw.suffix + ".part")
            try:
                client.retrieve(
                    date=run_date.replace("-", ""), time=f"{hour:02d}",
                    step=str(step), stream="oper", type="fc", param=PARAMS,
                    target=str(partial),
                )
            except Exception as exc:
                try:
                    partial.unlink()
                except OSError:
                    pass
                text = str(exc).lower()
                if any(code in text for code in ("404", "not found", "no data")):
                    continue
                if "503" in text or "slowdown" in text or "slow down" in text:
                    reason = f"{source} returned HTTP 503 SlowDown after bounded retries"
                else:
                    reason = f"{type(exc).__name__} from {source} Open Data"
                raise RuntimeError(
                    f"ECMWF {source} Open Data retrieval failed for {model} {run_date} {hour:02d}Z f{step}: {reason}"
                ) from exc
            complete, fields = validate_grib_fields(partial)
            if not complete:
                try:
                    partial.unlink()
                except OSError:
                    pass
                raise RuntimeError(
                    f"ECMWF {source} Open Data returned incomplete GRIB for {model} {run_date} "
                    f"{hour:02d}Z f{step}; required 2t/10u/10v/tp fields, found {sorted(fields)}"
                )
            partial.replace(raw)
        complete, fields = validate_grib_fields(raw)
        if not complete:
            raise RuntimeError(
                f"ECMWF {source} Open Data cached GRIB is incomplete for {model} {run_date} "
                f"{hour:02d}Z f{step}; found {sorted(fields)}"
            )
        means = decode_zone_means(model, raw.read_bytes())
        for zkey, m in means.items():
            rows.append(dict(model=model, run_date=run_date, run_hour=hour,
                            step=step, zone=zkey,
                            model_generation=model_generation(model, run_date), **m))
        print(f"  {model} {run_date} {hour:02d}z f{step:03d}: ok", flush=True)
    return rows


def out_path(model: str, run_date: str, hour: int) -> str:
    d = os.path.join(CACHE_DIR, model)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, f"{model}_{run_date}_{hour:02d}z.parquet")


def cache_is_current(path: str, model: str, run_date: str, steps=None) -> bool:
    if not os.path.exists(path):
        return False
    try:
        cached = pd.read_parquet(path)
        expected = model_generation(model, run_date)
        return (not cached.empty
            and set(cached["model_generation"].dropna().astype(str)) == {expected}
            and forecast_cache_is_complete(cached, model, steps))
    except Exception:
        return False


def run(model: str, start: str, end: str, hour: int, steps, latest: bool = False, source: str = "aws"):
    if source == "operational" and start and not latest:
        requested = datetime.strptime(start, "%Y-%m-%d").date()
        if requested < (datetime.now(timezone.utc).date() - timedelta(days=120)):
            raise SystemExit(
                "Operational ECMWF access requested for an older run; use --source aws for the public AWS mirror."
            )
    if latest:
        now = datetime.now(timezone.utc)
        for back in range(0, 3):
            ds = (now - timedelta(days=back)).strftime("%Y-%m-%d")
            p = out_path(model, ds, hour)
            if cache_is_current(p, model, ds, steps):
                continue
            if os.path.exists(p):
                cached = pd.read_parquet(p)
                print(f"  removing incomplete {model} cache {os.path.basename(p)}: "
                      f"{forecast_cache_missing(cached, model, steps)}", flush=True)
                os.remove(p)
            rows = fetch_cycle(model, ds, hour, steps, source=source)
            if rows:
                frame = pd.DataFrame(rows)
                frame.to_parquet(p, index=False)
                missing = forecast_cache_missing(frame, model, steps)
                if any(missing.values()):
                    print(f"  {model} {ds} {hour:02d}z remains incomplete: {missing}", flush=True)
                print("wrote", p)
                return
        return
    d = datetime.strptime(start, "%Y-%m-%d")
    e = datetime.strptime(end, "%Y-%m-%d")
    n = 0
    while d <= e:
        ds = d.strftime("%Y-%m-%d")
        p = out_path(model, ds, hour)
        if not cache_is_current(p, model, ds, steps):
            if os.path.exists(p):
                cached = pd.read_parquet(p)
                print(f"  removing incomplete {model} cache {os.path.basename(p)}: "
                      f"{forecast_cache_missing(cached, model, steps)}", flush=True)
                os.remove(p)
            rows = fetch_cycle(model, ds, hour, steps, source=source)
            if rows:
                frame = pd.DataFrame(rows)
                frame.to_parquet(p, index=False)
                missing = forecast_cache_missing(frame, model, steps)
                if any(missing.values()):
                    print(f"  {model} {ds} {hour:02d}z remains incomplete: {missing}", flush=True)
                n += 1
            else:
                print(f"  {model} {ds} {hour:02d}z: no data", flush=True)
        d += timedelta(days=1)
    print(f"done {model}: {n} new cycle files under {CACHE_DIR}/{model}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=False)
    ap.add_argument("--end", required=False)
    ap.add_argument("--hour", type=int, default=0, choices=[0, 6, 12, 18])
    ap.add_argument("--latest", action="store_true")
    ap.add_argument("--models", default="ifs,aifs-single")
    default_source = os.getenv("ECMWF_SOURCE_MODE", "aws").strip().lower()
    if default_source == "historical":
        default_source = "aws"
    ap.add_argument("--source", choices=["aws", "operational", "mars"], default=default_source)
    ap.add_argument("--steps", default=",".join(str(s) for s in STEPS))
    args = ap.parse_args()
    steps = [int(s) for s in args.steps.split(",") if s]
    for model in args.models.split(","):
        model = model.strip()
        if args.latest:
            run(model, "", "", args.hour, steps, latest=True, source=args.source)
        else:
            run(model, args.start, args.end or args.start, args.hour, steps, source=args.source)


if __name__ == "__main__":
    main()
