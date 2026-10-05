"""Run one-day real-provider validation without touching training state."""
from __future__ import annotations

import argparse
import importlib
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

import numpy as np

REALDATA = Path(__file__).resolve().parent
ROOT = REALDATA.parents[1]
for env_path in (ROOT / "backend" / ".env", ROOT / ".env"):
    if env_path.exists():
        try:
            from dotenv import dotenv_values
            for key, value in dotenv_values(env_path).items():
                if value and not os.getenv(key, "").strip():
                    os.environ[key] = value
        except ImportError:
            pass

REGIONS = (
    "kerala_western_ghats",
    "bay_of_bengal_east_coast",
    "indo_gangetic_plains",
)
SMOKE_LEAD = 24


def _safe_error(exc: Exception) -> str:
    message = str(exc)
    for key in (
        "EARTHDATA_TOKEN", "NASA_EARTHDATA_USERNAME",
        "NASA_EARTHDATA_PASSWORD", "ECMWF_API_KEY", "ECMWF_API_EMAIL",
    ):
        secret = os.getenv(key, "")
        if secret:
            message = message.replace(secret, "[REDACTED]")
    return message[:500]


def _credential_status() -> dict[str, str]:
    names = (
        "EARTHDATA_TOKEN", "NASA_EARTHDATA_USERNAME",
        "NASA_EARTHDATA_PASSWORD",
    )
    return {name: "PRESENT" if os.getenv(name, "").strip() else "MISSING" for name in names}


def _subprocess_credential_status() -> dict[str, str]:
    code = (
        "import os, json; names=('EARTHDATA_TOKEN',"
        "'NASA_EARTHDATA_USERNAME','NASA_EARTHDATA_PASSWORD'); "
        "print(json.dumps({n: ('PRESENT' if os.getenv(n, '').strip() else 'MISSING') for n in names}))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True,
        check=True, env=os.environ.copy(),
    )
    return json.loads(completed.stdout)


def _validate_rows(rows: list[dict], required_fields: set[str], date_field: str,
                   expected_date: str, expected_step: int | None = None) -> dict:
    regions = sorted({str(row.get("zone")) for row in rows})
    fields = sorted({field for row in rows for field in row})
    missing_fields = sorted(required_fields - set(fields))
    invalid = []
    for row in rows:
        if expected_step is not None and int(row.get("step", -1)) != expected_step:
            invalid.append("unexpected lead")
        if str(row.get(date_field))[:10] != expected_date:
            invalid.append("unexpected timestamp")
        if any(value is None or (isinstance(value, float) and not np.isfinite(value))
               for value in row.values()):
            invalid.append("null or non-finite value")
    return {
        "rows": len(rows),
        "regions": regions,
        "lead_hours": sorted({int(row["step"]) for row in rows if "step" in row}),
        "fields": fields,
        "missing_fields": missing_fields,
        "invalid_records": sorted(set(invalid)),
        "success": bool(rows) and set(regions) == set(REGIONS) and not missing_fields and not invalid,
    }


def _run_provider(name: str, requested: str, func, report: dict) -> list[dict]:
    try:
        result = func()
        report[name] = result
        print(f"{name}: {'PASS' if result['success'] else 'FAIL'}", flush=True)
        return result.pop("_rows", [])
    except Exception as exc:
        report[name] = {
            "provider": name,
            "requested_date": requested,
            "success": False,
            "rows": 0,
            "regions": [],
            "lead_hours": [],
            "fields": [],
            "error": _safe_error(exc),
        }
        print(f"{name}: FAIL", flush=True)
        return []


def main() -> int:
    parser = argparse.ArgumentParser(description="One-day real provider smoke gate")
    parser.add_argument("--date", default="2025-02-26")
    args = parser.parse_args()
    requested = date.fromisoformat(args.date)
    requested_text = requested.isoformat()
    report_path = ROOT / "data" / "real" / "cache" / "provider_smoke_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report: dict = {"requested_date": requested_text, "lead_hours": [SMOKE_LEAD]}
    report["credentials"] = _credential_status()
    report["subprocess_credentials"] = _subprocess_credential_status()
    report["root_env"] = {
        "path": str(ROOT / ".env"),
        "exists": (ROOT / ".env").exists(),
        "loaded_into_process": report["credentials"] == report["subprocess_credentials"],
    }
    forecast_rows: dict[str, list[dict]] = {}
    truth_rows: dict[str, list[dict]] = {}

    with tempfile.TemporaryDirectory(prefix="synoptiq-provider-smoke-") as temp_cache:
        os.environ["SYNOPTIQ_CACHE"] = temp_cache
        if str(REALDATA) not in sys.path:
            sys.path.insert(0, str(REALDATA))
        common = importlib.import_module("common")

        def forecast(name: str, module_name: str, model: str | None = None):
            module = importlib.import_module(module_name)
            rows = (module.fetch_cycle(model, requested_text, 0, [SMOKE_LEAD], source="aws",
                                       maximum_retries=1)
                    if module_name == "fetch_ecmwf"
                    else module.fetch_cycle(requested_text, 0, [SMOKE_LEAD]))
            required = {"run_date", "run_hour", "step", "zone", "t2m_c", "wind_ms"}
            required.add("apcp6_mm" if name == "GFS" else "tp_cum_m")
            checked = _validate_rows(rows, required, "run_date", requested_text, SMOKE_LEAD)
            checked.update({
                "provider": name,
                "requested_date": requested_text,
                "variables": ["temperature", "wind", "precipitation"],
                "units": {"t2m_c": "deg C", "wind_ms": "m/s",
                          "apcp6_mm": "mm/6 h" if name == "GFS" else None,
                          "tp_cum_m": "m cumulative" if name != "GFS" else None},
                "source": "NOAA GFS AWS" if name == "GFS" else "ECMWF Open Data AWS",
                "provenance": (module.model_generation(model, requested_text)
                               if model else "GFS 0.25 degree GRIB2"),
                "_rows": rows,
            })
            return checked

        forecast_rows["GFS"] = _run_provider(
            "GFS", requested_text, lambda: forecast("GFS", "fetch_gfs"), report)
        for name, model in (("IFS", "ifs"), ("AIFS", "aifs-single")):
            def run_ecmwf(name=name, model=model):
                if name != "IFS":
                    return forecast(name, "fetch_ecmwf", model)
                for attempt in range(2):
                    try:
                        return forecast(name, "fetch_ecmwf", model)
                    except Exception as exc:
                        if "503" not in str(exc) and "slowdown" not in str(exc).lower():
                            raise
                        if attempt == 1:
                            raise RuntimeError(
                                "TRANSIENT_PROVIDER_THROTTLE: ECMWF AWS 503 SlowDown"
                            ) from exc
                        time.sleep(5)
            forecast_rows[name] = _run_provider(
                name, requested_text, run_ecmwf, report)

        def era5():
            module = importlib.import_module("fetch_era5")
            year, month, day = requested_text.split("-")
            paths = module.fetch_month(year, month, day=int(day), regions=list(REGIONS))
            rows = [row for region, path in paths.items()
                    for row in module.daily_zone_means(str(path), region)]
            checked = _validate_rows(rows, {"valid_date", "zone", "t2m_c", "wind_kmh"},
                                     "valid_date", requested_text)
            checked.update({
                "provider": "ERA5", "requested_date": requested_text,
                "variables": ["temperature", "wind"],
                "units": {"t2m_c": "deg C", "wind_kmh": "km/h"},
                "source_dataset": module.SOURCE_DATASET,
                "transport": module.TRANSPORT,
                "resolution": module.RESOLUTION,
                "provenance": True, "_rows": rows,
            })
            return checked

        truth_rows["ERA5"] = _run_provider("ERA5", requested_text, era5, report)

        def imerg():
            module = importlib.import_module("fetch_imerg")
            rows = module.zone_means_for_day(requested_text)
            checked = _validate_rows(rows, {"valid_date", "zone", "rain_mm"},
                                     "valid_date", requested_text)
            checked.update({
                "provider": "IMERG", "requested_date": requested_text,
                "variables": ["precipitation"], "units": {"rain_mm": "mm/day"},
                "source": "NASA GPM_3IMERGDF V07 Final Daily",
                "provenance": "NASA Earthdata regional NetCDF/HDF reduction", "_rows": rows,
            })
            return checked

        truth_rows["IMERG"] = _run_provider("IMERG", requested_text, imerg, report)

        forecast_keys = {
            name: {(row.get("zone"), int(row.get("step", -1))) for row in rows}
            for name, rows in forecast_rows.items()
        }
        aligned_forecasts = bool(forecast_keys) and len(set(map(frozenset, forecast_keys.values()))) == 1
        truth_keys = {
            source: {(row.get("zone"), row.get("valid_date")) for row in rows}
            for source, rows in truth_rows.items()
        }
        expected_truth_date = requested_text
        aligned_truth = all(
            keys == {(region, expected_truth_date) for region in REGIONS}
            for keys in truth_keys.values()
        ) if truth_keys else False
        report["alignment"] = {
            "forecast_gfs_ifs_aifs": aligned_forecasts,
            "truth_era5_imerg": aligned_truth,
            "expected_truth_valid_date": expected_truth_date,
            "expected_regions": list(REGIONS),
            "expected_lead_hours": [SMOKE_LEAD],
        }
        report["success"] = (
            all(report.get(name, {}).get("success", False)
                for name in ("GFS", "IFS", "AIFS", "ERA5", "IMERG"))
            and aligned_forecasts and aligned_truth
        )

    transient_markers = {"raw", "raw_tmp", "imerg_raw", "tmp"}
    persistent_bytes = 0
    transient_bytes = 0
    for path in report_path.parent.rglob("*"):
        if not path.is_file():
            continue
        is_transient = (
            any(part in transient_markers for part in path.parts)
            or path.suffix in {".grib2", ".nc", ".part"}
            or path.name.startswith("_")
        )
        if is_transient:
            transient_bytes += path.stat().st_size
        else:
            persistent_bytes += path.stat().st_size
    report["cache"] = {
        "persistent_cache_bytes": persistent_bytes,
        "temporary_raw_bytes": transient_bytes,
    }
    for value in report.values():
        if isinstance(value, dict):
            value.pop("_rows", None)
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Smoke report: {report_path}", flush=True)
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
