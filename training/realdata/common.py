"""
Synoptiq real-data ingestion — shared helpers.

Verified against the live endpoints on 2026-09-25:
  * GFS 0.25 deg GRIB2 on AWS S3 (noaa-gfs-bdp-pds)  — full archive since 2021,
    byte-range subsetting via the .idx files (a ~541 MB file reduces to ~3 MB
    for TMP@2m + UGRD/VGRD@10m + APCP@surface).
  * ECMWF Open Data 0.25 deg (IFS + AIFS) on S3 (ecmwf-forecasts, eu-central-1)
    — rolling archive (~3-4 months), JSON-lines .index with per-message
    _offset/_length for byte-range subsetting.
    * IMERG daily (GES DISC) — needs a free Earthdata account; see fetch_imerg.py.
    * ERA5 truth uses Open-Meteo Historical Weather API with the ERA5 model.

Canonical units used everywhere downstream (matches backend/app/config.py):
  precipitation  mm / 24 h          temperature  deg C (daily mean)
  wind_speed     km/h (daily max of 10 m wind)

Verification day convention: a row with valid_time = D 00:00 UTC describes
the 24 h window [D 00:00, D+1 00:00) UTC (IMERG daily and ERA5 daily-mean
compatible).
"""
from __future__ import annotations
import json
import os
import time
import urllib.request
import urllib.error
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

try:
    from dotenv import dotenv_values
    _ROOT = __import__("pathlib").Path(__file__).resolve().parents[2]
    _merged = {}
    for _path in (_ROOT / "backend" / ".env", _ROOT / ".env"):
        for _key, _value in dotenv_values(_path).items():
            if _value is not None and _value.strip():
                _merged[_key] = _value
    for _key, _value in _merged.items():
        if not os.getenv(_key, "").strip():
            os.environ[_key] = _value
except ImportError:
    pass

if os.getenv("SYNOPTIQ_MODE", "real").strip().lower() in {"fake", "demo"}:
    raise RuntimeError("Real-data ingestion tools require SYNOPTIQ_MODE=real")

# ---------------------------------------------------------------------------
# Pilot zones (identical to backend/app/config.py). If this package is copied
# into the Synoptiq repo next to backend/, we import the real config instead.
# ---------------------------------------------------------------------------
PILOT_ZONES = {
    "kerala_western_ghats": {"lat_range": (8.3, 12.8), "lon_range": (74.9, 77.4)},
    "bay_of_bengal_east_coast": {"lat_range": (12.5, 20.5), "lon_range": (80.0, 87.5)},
    "indo_gangetic_plains": {"lat_range": (24.0, 30.5), "lon_range": (75.0, 83.0)},
}

try:  # running inside the Synoptiq repo -> use the authoritative config
    import sys as _sys, pathlib as _pl
    _repo = _pl.Path(__file__).resolve().parents[1] / "backend"
    if (_repo / "app" / "config.py").exists():
        _sys.path.insert(0, str(_repo))
        from app.config import PILOT_ZONES as _ZONES  # type: ignore
        PILOT_ZONES = {k: {"lat_range": v["lat_range"], "lon_range": v["lon_range"]}
                       for k, v in _ZONES.items()}
except Exception:
    pass

LEAD_HOURS = [24, 48, 72, 96, 120]      # verification lead days 1..5
STEPS = list(range(6, 145, 6))          # 6..144 by 6 h (covers day windows)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_REAL_CACHE = _PROJECT_ROOT / "training" / "realdata" / "cache" / "12m"
CACHE_DIR = Path(os.environ.get("SYNOPTIQ_CACHE", str(_DEFAULT_REAL_CACHE))).resolve()
_FAKE_DATA_ROOT = (_PROJECT_ROOT / "data" / "fake").resolve()
if CACHE_DIR == _FAKE_DATA_ROOT or _FAKE_DATA_ROOT in CACHE_DIR.parents:
    raise RuntimeError("Real-data ingestion cache cannot be placed under data/fake")
CACHE_DIR = str(CACHE_DIR)


def forecast_cache_missing(raw, model: str, steps=None) -> dict:
    """Describe missing rows/fields in a provider cycle without accepting partial data."""
    expected_steps = set(STEPS if steps is None else steps)
    required_fields = {
        "GFS": {"t2m_c", "wind_ms", "apcp6_mm"},
        "IFS": {"t2m_c", "wind_ms", "tp_cum_m"},
        "AIFS": {"t2m_c", "wind_ms", "tp_cum_m"},
        "ifs": {"t2m_c", "wind_ms", "tp_cum_m"},
        "aifs-single": {"t2m_c", "wind_ms", "tp_cum_m"},
    }[model]
    fields = set(getattr(raw, "columns", []))
    if model in {"AIFS", "aifs-single"} and "precip_mm" in fields and "tp_cum_m" not in fields:
        required_fields = {"t2m_c", "wind_ms", "precip_mm"}
        expected_steps = set(LEAD_HOURS if steps is None else steps)
    missing_fields = sorted(required_fields - fields)
    if raw is None or raw.empty or not {"step", "zone"}.issubset(fields):
        return {"steps": sorted(expected_steps), "step_regions": [], "fields": missing_fields}
    observed_steps = set(raw["step"].dropna().astype(int))
    missing_steps = sorted(expected_steps - observed_steps)
    expected_pairs = {(step, zone) for step in expected_steps for zone in PILOT_ZONES}
    observed_pairs = set(zip(raw["step"].astype(int), raw["zone"].astype(str)))
    missing_pairs = sorted(expected_pairs - observed_pairs)
    invalid_fields = [field for field in required_fields & fields if raw[field].isna().any()]
    return {
        "steps": missing_steps,
        "step_regions": missing_pairs,
        "fields": sorted(set(missing_fields + invalid_fields)),
    }


def forecast_cache_is_complete(raw, model: str, steps=None) -> bool:
    missing = forecast_cache_missing(raw, model, steps)
    return not any(missing.values())

# ---------------------------------------------------------------------------
# HTTP with exponential backoff. S3 occasionally answers 503 "Slow Down";
# wait and retry instead of failing the backfill.
# ---------------------------------------------------------------------------

def http_get(url: str, rng: str | None = None, tries: int = 6,
             wait: float = 10.0, timeout: float = 180) -> bytes:
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "synoptiq-ingest/1.0"})
            if rng:
                req.add_header("Range", f"bytes={rng}")
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (429, 503):
                time.sleep(wait * (i + 1))
                continue
            raise
        except Exception as e:  # connection resets etc.
            last = e
            time.sleep(wait * (i + 1))
    raise last  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Grid cropping + zone statistics
# ---------------------------------------------------------------------------

def crop(ds, lat0: float, lat1: float, lon0: float, lon1: float):
    """Crop an xarray dataset to a lat/lon box, handling both ascending and
    descending latitude orientation (GFS/ECMWF are descending, IMERG ascending)."""
    lat_desc = bool(ds["latitude"][0] > ds["latitude"][-1])
    if lat_desc:
        ds = ds.sel(latitude=slice(lat1, lat0))
    else:
        ds = ds.sel(latitude=slice(lat0, lat1))
    return ds.sel(longitude=slice(lon0, lon1))


def zone_means(ds) -> dict:
    """ds must contain t2m (K), u10, v10 (m/s) and tp or apcp (see callers).
    Returns per-zone means with canonical units."""
    out = {}
    for zkey, z in PILOT_ZONES.items():
        sub = crop(ds, *z["lat_range"], *z["lon_range"])
        row = {}
        if "t2m" in sub:
            row["t2m_c"] = float(sub["t2m"].mean()) - 273.15
        if "u10" in sub and "v10" in sub:
            w = np.sqrt(sub["u10"] ** 2 + sub["v10"] ** 2)  # wind per cell BEFORE averaging
            row["wind_ms"] = float(w.mean())
        if "tp" in sub:      # ECMWF/AIFS: cumulative total precip in metres
            row["tp_m"] = float(sub["tp"].mean())
        if "apcp" in sub:    # GFS: 6-hour bucket APCP in kg/m^2 (== mm)
            row["apcp6_mm"] = float(sub["apcp"].mean())
        out[zkey] = row
    return out


def season_for_month(month: int) -> str:
    if month in (3, 4, 5):
        return "pre_monsoon"
    if month in (6, 7, 8, 9):
        return "sw_monsoon"
    if month in (10, 11):
        return "post_monsoon"
    return "winter"


def ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)
    return path
