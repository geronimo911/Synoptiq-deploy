"""Authenticated ECMWF historical IFS/AIFS retrieval through the Web API."""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path


def _client():
    url = os.getenv("ECMWF_API_URL", "https://api.ecmwf.int/v1").strip()
    key = os.getenv("ECMWF_API_KEY", "").strip()
    email = os.getenv("ECMWF_API_EMAIL", "").strip()
    if not key or not email:
        raise RuntimeError(
            "ECMWF historical archive access unavailable: ECMWF_API_KEY and "
            "ECMWF_API_EMAIL are required, and the account must have archive/MARS authorization."
        )
    try:
        from ecmwfapi import ECMWFDataServer
    except ImportError as exc:
        raise RuntimeError(
            "ECMWF historical archive access unavailable: install ecmwf-api-client in D:/SIH2026/.venv."
        ) from exc
    return ECMWFDataServer(url=url, key=key, email=email)


def retrieve_step(model: str, run_date: str, hour: int, step: int, target: Path) -> Path:
    """Retrieve one India-domain surface GRIB step for IFS or AIFS Single."""
    if model == "aifs-single" and date.fromisoformat(run_date) < date(2024, 2, 1):
        raise RuntimeError("ECMWF historical AIFS archive is available only from February 2024 onward.")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.stat().st_size > 0:
        return target
    request = {
        "dataset": "mars",
        "class": "od" if model == "ifs" else "ai",
        "stream": "oper",
        "type": "fc",
        "expver": "1",
        "levtype": "sfc",
        "param": "134/167/165/166/228",
        "date": run_date.replace("-", ""),
        "time": f"{hour:02d}00",
        "step": str(step),
        "grid": "0.25/0.25",
        "area": "38/66/6/98",
        "format": "grib",
        "target": str(target),
    }
    try:
        _client().retrieve(request)
    except Exception as exc:
        message = str(exc)
        if any(word in message.lower() for word in ("permission", "mars", "access")):
            raise RuntimeError(
                "ECMWF historical archive access unavailable: the configured account "
                "does not have the required MARS/archive authorization."
            ) from exc
        raise RuntimeError(f"ECMWF historical retrieval failed for {model} {run_date} {hour:02d}Z f{step}: {message}") from exc
    if not target.exists() or target.stat().st_size == 0:
        raise RuntimeError(f"ECMWF historical retrieval returned no GRIB data: {target}")
    return target