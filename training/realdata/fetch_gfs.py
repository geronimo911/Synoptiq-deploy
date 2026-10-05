"""
Fetch GFS 0.25-degree forecast fields for the Synoptiq pilot zones.

Source (verified live): AWS S3 open bucket `noaa-gfs-bdp-pds`
  https://noaa-gfs-bdp-pds.s3.amazonaws.com/gfs.{YYYYMMDD}/{HH}/atmos/gfs.t{HH}z.pgrb2.0p25.f{LLL}
  ... + the same path with `.idx` appended = wgrib2-style index.

Strategy (this is what keeps the download small):
  1. GET the .idx (a few KB) — lines look like
       596:427495720:d=2026092400:APCP:surface:18-24 hour acc fcst:
       (message_number : byte_offset : ref_time : VAR : level : time_range)
  2. Pick only the messages we need: TMP@2m, UGRD@10m, VGRD@10m, and the
     APCP 6-hour bucket message.
  3. One HTTP Range request per message (~0.4-1 MB each) -> ~2.5 MB per
     lead file instead of ~540 MB for the full file.
  4. Concatenate the GRIB messages (valid — GRIB2 is a message stream),
     decode with cfgrib, crop to the pilot zones, store zone means.

Output: one Parquet row per (run_date, run_hour, step, zone):
  {run_date, run_hour, step, zone, t2m_c, wind_ms, apcp6_mm}

Usage:
  python fetch_gfs.py --start 2026-07-01 --end 2026-09-23            # 00Z backfill
  python fetch_gfs.py --start 2026-09-24 --end 2026-09-24 --hour 12 # 12Z too
  python fetch_gfs.py --latest                                      # most recent cycle

Deps: pip install xarray cfgrib eccodes pyarrow requests
"""
from __future__ import annotations
import argparse

import os
import urllib.error

from datetime import datetime, timedelta, timezone

import pandas as pd

from common import (PILOT_ZONES, STEPS, CACHE_DIR, http_get, ensure_dir,
                    season_for_month, forecast_cache_is_complete, forecast_cache_missing)

BASE = os.getenv("NOAA_NOMADS_BASE_URL", "https://noaa-gfs-bdp-pds.s3.amazonaws.com").rstrip("/")

# (shortName in file, level string in idx, time_range must contain)
WANTED = [
    ("TMP",  "2 m above ground",  None),           # instantaneous 2 m temp
    ("UGRD", "10 m above ground", None),           # instantaneous 10 m U
    ("VGRD", "10 m above ground", None),           # instantaneous 10 m V
    ("APCP", "surface",           "hour acc"),     # 6-hour accumulation bucket
]


def parse_idx(text: str):
    msgs = []
    for line in text.strip().split("\n"):
        parts = line.split(":")
        if len(parts) >= 6 and parts[2].startswith("d="):
            msgs.append(dict(n=int(parts[0]), off=int(parts[1]), var=parts[3],
                            lev=parts[4], tr=parts[5]))
    return msgs


def fetch_file_fields(run_date: str, hour: int, step: int) -> bytes:
    """Return raw GRIB bytes containing just the wanted messages for one lead file."""
    d = run_date.replace("-", "")
    prefix = f"{BASE}/gfs.{d}/{hour:02d}/atmos/gfs.t{hour:02d}z.pgrb2.0p25.f{step:03d}"
    idx = http_get(prefix + ".idx").decode()
    msgs = parse_idx(idx)
    blobs = []
    for want_var, want_lev, want_tr in WANTED:
        for i, m in enumerate(msgs):
            if m["var"] != want_var or m["lev"] != want_lev:
                continue
            if want_tr is not None and want_tr not in m["tr"]:
                continue
            end = msgs[i + 1]["off"] - 1 if i + 1 < len(msgs) else m["off"] + 5_000_000
            blobs.append(http_get(prefix, rng=f"{m['off']}-{end}"))
            break
    if not blobs:
        raise FileNotFoundError(f"no wanted fields in {prefix}")
    return b"".join(blobs)


def decode_zone_means(grib_bytes: bytes) -> dict:
    """Decode the subset GRIB and return per-zone means.
    GFS puts 2 m and 10 m vars at the same typeOfLevel ('heightAboveGround')
    but different scalar levels -> cfgrib needs one open per level."""
    import numpy as np
    import xarray as xr
    from common import crop

    tmp = os.path.join(CACHE_DIR, "_gfs_tmp.grib2")
    ensure_dir(CACHE_DIR)
    with open(tmp, "wb") as f:
        f.write(grib_bytes)
    try:
        d2 = xr.open_dataset(tmp, engine="cfgrib",
                             backend_kwargs={"filter_by_keys": {"typeOfLevel": "heightAboveGround", "level": 2.0}})
        d10 = xr.open_dataset(tmp, engine="cfgrib",
                              backend_kwargs={"filter_by_keys": {"typeOfLevel": "heightAboveGround", "level": 10.0}})
        dsp = xr.open_dataset(tmp, engine="cfgrib",
                              backend_kwargs={"filter_by_keys": {"typeOfLevel": "surface", "stepType": "accum"}})
        out = {}
        for zkey, z in PILOT_ZONES.items():
            s2, s10, ssp = (crop(x, *z["lat_range"], *z["lon_range"]) for x in (d2, d10, dsp))
            w = np.sqrt(s10["u10"] ** 2 + s10["v10"] ** 2)
            out[zkey] = dict(
                t2m_c=float(s2["t2m"].mean()) - 273.15,
                wind_ms=float(w.mean()),
                apcp6_mm=float(ssp["tp"].mean()),  # cfgrib names GFS APCP 'tp'; units kg m-2 == mm
            )
        return out
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def fetch_cycle(run_date: str, hour: int, steps) -> list[dict]:
    ensure_dir(CACHE_DIR)
    rows = []
    for step in steps:
        try:
            blob = fetch_file_fields(run_date, hour, step)
            means = decode_zone_means(blob)
        except FileNotFoundError:
            continue  # cycle/file not published yet -> skip
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                print(f"  gfs {run_date} {hour:02d}z f{step:03d}: provider lead unavailable (HTTP 404)", flush=True)
                continue
            raise
        for zkey, m in means.items():
            rows.append(dict(run_date=run_date, run_hour=hour, step=step, zone=zkey, **m))
        print(f"  gfs {run_date} {hour:02d}z f{step:03d}: ok", flush=True)
    return rows


def out_path(run_date: str, hour: int) -> str:
    d = os.path.join(CACHE_DIR, "gfs")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, f"gfs_{run_date}_{hour:02d}z.parquet")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=False)
    ap.add_argument("--end", required=False)
    ap.add_argument("--hour", type=int, default=0, choices=[0, 6, 12, 18])
    ap.add_argument("--latest", action="store_true")
    ap.add_argument("--steps", default=",".join(str(s) for s in STEPS))
    args = ap.parse_args()

    steps = [int(s) for s in args.steps.split(",") if s]

    if args.latest:
        now = datetime.now(timezone.utc)
        d = now - timedelta(days=1)
        while d <= now:
            run_date = d.strftime("%Y-%m-%d")
            p = out_path(run_date, args.hour)
            cached = pd.read_parquet(p) if os.path.exists(p) else pd.DataFrame()
            if forecast_cache_is_complete(cached, "GFS", steps):
                d += timedelta(days=1)
                continue
            if os.path.exists(p):
                print(f"  removing incomplete GFS cache {os.path.basename(p)}: "
                      f"{forecast_cache_missing(cached, 'GFS', steps)}", flush=True)
                os.remove(p)
            rows = fetch_cycle(run_date, args.hour, steps)
            if rows:
                pd.DataFrame(rows).to_parquet(p, index=False)
                print("wrote", p)
                break
            d += timedelta(days=1)
        return

    start = datetime.strptime(args.start, "%Y-%m-%d")
    end = datetime.strptime(args.end or args.start, "%Y-%m-%d")
    d = start
    n = 0
    while d <= end:
        ds = d.strftime("%Y-%m-%d")
        p = out_path(ds, args.hour)
        cached = pd.read_parquet(p) if os.path.exists(p) else pd.DataFrame()
        if forecast_cache_is_complete(cached, "GFS", steps):
            d += timedelta(days=1)
            continue
        if os.path.exists(p):
            print(f"  removing incomplete GFS cache {os.path.basename(p)}: "
                  f"{forecast_cache_missing(cached, 'GFS', steps)}", flush=True)
            os.remove(p)
        rows = fetch_cycle(ds, args.hour, steps)
        if rows:
            pd.DataFrame(rows).to_parquet(p, index=False)
            missing = forecast_cache_missing(pd.DataFrame(rows), "GFS", steps)
            if any(missing.values()):
                print(f"  GFS {ds} {args.hour:02d}z remains incomplete: {missing}", flush=True)
            n += 1
        else:
            print(f"  gfs {ds} {args.hour:02d}z: no data (cycle missing?)", flush=True)
        d += timedelta(days=1)
    print(f"done: {n} new cycle files written to {CACHE_DIR}/gfs")


if __name__ == "__main__":
    main()
