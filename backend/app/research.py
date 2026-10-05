"""Read-only access to the research & verification suite artifacts.

The suite (backend/scripts/research_suite.py) pre-computes every "proof"
claim from the archived 12-month blend record. This module loads the
resulting JSON lazily and exposes section accessors for the API layer.
Nothing here touches the blending pipeline, the database or the network.

If the artifact is missing the endpoints raise a 404 whose message tells
the user exactly which command produces it, instead of silently
degrading to anything synthetic.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from fastapi import HTTPException

from app.config import ARTIFACTS_DIR

OPS_DIR = ARTIFACTS_DIR.parents[1] / "ops"  # repo_root/ops (artifacts/<bundle>)

RESEARCH_DIR = ARTIFACTS_DIR / "research"
RESEARCH_SUITE_PATH = RESEARCH_DIR / "research_suite.json"
BLENDS_JSONL_PATH = ARTIFACTS_DIR / "inference" / "historical_blends.jsonl"

_RESEARCH_COMMAND = (
    "python backend/scripts/research_suite.py "
    "--blends artifacts/real_12m_aifs_corrected/inference/historical_blends.jsonl "
    "--out artifacts/real_12m_aifs_corrected/research/research_suite.json"
)


@lru_cache(maxsize=2)
def _load_suite() -> dict:
    if not RESEARCH_SUITE_PATH.is_file():
        raise HTTPException(
            404,
            "Research suite artifact not found. It is generated offline from the "
            f"archived blend record with: {_RESEARCH_COMMAND}",
        )
    return json.loads(RESEARCH_SUITE_PATH.read_text(encoding="utf-8"))


def research_section(name: str) -> dict:
    suite = _load_suite()
    if name not in suite:
        raise HTTPException(500, f"Research suite is missing section '{name}'.")
    return suite[name]


@lru_cache(maxsize=8)
def _load_named(name: str) -> dict:
    path = RESEARCH_DIR / f"{name}.json"
    if not path.is_file():
        raise HTTPException(
            404,
            f"Artifact '{name}.json' not found. Generate it with: "
            "python backend/scripts/build_advanced_artifacts.py "
            "--blends artifacts/real_12m_aifs_corrected/inference/historical_blends.jsonl "
            "--out-dir artifacts/real_12m_aifs_corrected/research",
        )
    return json.loads(path.read_text(encoding="utf-8"))


def trust_atlas() -> dict:
    return _load_named("trust_atlas")


def regime_router() -> dict:
    return _load_named("regime_router")


def peak_preservation() -> dict:
    return _load_named("peak_preservation")


def impact_engine() -> dict:
    return _load_named("impact_engine")


def impact_for_date(date: str | None) -> dict:
    """Impact Severity Index for an archived day (defaults to latest)."""
    import sys
    from datetime import datetime as _dt, timedelta
    from app.impact import VULNERABILITY, cap_xml, compute_isi

    engine = impact_engine()
    latest = engine["latest"]["date"]
    day = date or latest
    if day == latest:
        zones = engine["latest"]["zones"]
    else:
        import pandas as pd
        if not BLENDS_JSONL_PATH.is_file():
            raise HTTPException(404, "Archived blend record not found.")
        rows = []
        with BLENDS_JSONL_PATH.open(encoding="utf-8") as fh:
            for line in fh:
                if f'"{day}' in line:
                    r = json.loads(line)
                    if r["valid_time"].startswith(day):
                        rows.append(r)
        if not rows:
            available = archived_dates()
            range_text = f"Available archive: {available['earliest']} to {available['latest']}." if available["dates"] else "The archive has no dated contexts."
            raise HTTPException(404, f"No archived contexts for {day}. {range_text}")
        df = pd.DataFrame(rows)
        zones = []
        for zone, g in df.groupby("region"):
            values = {}
            for variable in ("precipitation", "temperature", "wind_speed"):
                v = g[g.variable == variable]
                if len(v):
                    values[variable] = float(v.calibrated.max())
            if values:
                res = compute_isi(zone, values)
                res["vulnerability_rationale"] = VULNERABILITY.get(zone, {}).get("rationale")
                zones.append(res)
        zones.sort(key=lambda r: -r["isi"])
    return {
        "date": day,
        "source": engine.get("scope"),
        "hazard_weights": engine.get("hazard_weights"),
        "colour_bands": engine.get("colour_bands"),
        "zones": zones,
    }


@lru_cache(maxsize=1)
def archived_dates() -> dict:
    """Return the valid dates in the served archive for date-picker clients."""
    if not BLENDS_JSONL_PATH.is_file():
        raise HTTPException(404, "Archived blend record not found.")
    dates: set[str] = set()
    with BLENDS_JSONL_PATH.open(encoding="utf-8") as fh:
        for line in fh:
            try:
                valid_time = json.loads(line).get("valid_time", "")
            except json.JSONDecodeError:
                continue
            if isinstance(valid_time, str) and len(valid_time) >= 10:
                dates.add(valid_time[:10])
    ordered = sorted(dates)
    return {
        "dates": ordered,
        "earliest": ordered[0] if ordered else None,
        "latest": ordered[-1] if ordered else None,
    }


def cap_alert(zone: str, date: str | None = None) -> str:
    from datetime import datetime as _dt, timedelta
    from app.impact import cap_xml

    block = impact_for_date(date)
    match = next((z for z in block["zones"] if z["zone"] == zone), None)
    if match is None:
        raise HTTPException(404, f"No impact assessment for zone '{zone}' on {block['date']}.")
    vfrom = _dt.fromisoformat(f"{block['date']}T00:00:00+00:00")
    return cap_xml(zone=zone, isi_result=match, valid_from=vfrom,
                   valid_to=vfrom + timedelta(hours=48))


def ops_status() -> dict:
    """Last routine-run status from ops/run_log.jsonl, plus artifact presence."""
    log = OPS_DIR / "run_log.jsonl"
    entries = []
    if log.is_file():
        for line in log.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    last = entries[-1] if entries else None
    bulletins_dir = ARTIFACTS_DIR / "bulletins"
    days = sorted(p.name for p in bulletins_dir.glob("*") if p.is_dir()) if bulletins_dir.is_dir() else []
    return {
        "last_run": last,
        "runs_recorded": len(entries),
        "bulletin_days": days[-10:],
        "artifacts_present": {
            name: (RESEARCH_DIR / f"{name}.json").is_file()
            for name in ("research_suite", "trust_atlas", "regime_router",
                         "peak_preservation", "impact_engine")
        },
        "commands": {
            "routine": "scripts/run_routine_blend.sh  (or run_routine_blend.ps1)",
            "bulletin_only": "python backend/scripts/export_bulletin.py",
        },
    }


def bulletin_path(date: str | None, fmt: str) -> Path:
    bulletins_dir = ARTIFACTS_DIR / "bulletins"
    if not bulletins_dir.is_dir():
        raise HTTPException(404, "No bulletins have been generated yet. Run "
                                 "scripts/run_routine_blend.sh or "
                                 "python backend/scripts/export_bulletin.py")
    days = sorted(p.name for p in bulletins_dir.glob("*") if p.is_dir())
    if not days:
        raise HTTPException(404, "Bulletin directory is empty.")
    day = date or days[-1]
    fname = {"json": "bulletin.json", "csv": "bulletin.csv",
             "txt": "bulletin.txt"}.get(fmt)
    if fname is None:
        raise HTTPException(422, "format must be one of json, csv, txt")
    path = bulletins_dir / day / fname
    if not path.is_file():
        raise HTTPException(404, f"No {fmt} bulletin for {day}.")
    return path


def research_summary() -> dict:
    suite = _load_suite()
    return {
        "generated_at": suite.get("generated_at"),
        "source": suite.get("source"),
        "calibration_sanity": suite.get("calibration_sanity"),
        "oracle_overall": suite.get("oracle", {}).get("overall"),
        "ai_ablation_rows": suite.get("ai_ablation", {}).get("rows"),
        "commands": {"regenerate": _RESEARCH_COMMAND},
    }


# --- Risk Priority Index for an arbitrary archived day ---------------------
#
# The suite JSON pins the RPI to the newest archived day; the endpoint also
# accepts ?date=YYYY-MM-DD so the panel can replay any archived day (e.g. a
# monsoon event) without recomputing the whole suite. The blend record is
# stream-filtered by date, so this stays cheap.

RPI_LEVELS = [("SEVERE", 85.0), ("WARNING", 65.0), ("ALERT", 45.0), ("WATCH", 25.0)]


def _rpi_severity(variable: str, value: float):
    if variable == "precipitation":
        if value >= 115.5:
            return 100, "very_heavy_rain"
        if value >= 64.5:
            return 75, "heavy_rain"
        if value >= 15.6:
            return 50, "moderate_rain"
    elif variable == "temperature":
        if value >= 40.0:
            return 75, "heat_alert"
        if value <= 5.0:
            return 50, "cold_alert"
    elif variable == "wind_speed":
        if value >= 50.0:
            return 75, "damaging_wind"
        if value >= 40.0:
            return 50, "strong_wind"
    return 0, None


def rpi_for_date(date: str | None = None) -> dict:
    if not BLENDS_JSONL_PATH.is_file():
        raise HTTPException(404, "Archived blend record not found.")
    rows = []
    with BLENDS_JSONL_PATH.open(encoding="utf-8") as fh:
        for line in fh:
            # fast prefilter, tolerant of JSON spacing, then exact parse
            if not date or f'"{date}' in line:
                rows.append(line) if not date else None
                if date:
                    vt = json.loads(line).get("valid_time", "")
                    if vt.startswith(date):
                        rows.append(line)
    if not rows:
        available = archived_dates()
        range_text = f"Available archive: {available['earliest']} to {available['latest']}." if available["dates"] else "The archive has no dated contexts."
        raise HTTPException(404, f"No archived contexts for {date}. {range_text}")
    if not date:
        newest = max(json.loads(r)["valid_time"] for r in rows)
        rows = [r for r in rows if json.loads(r)["valid_time"] == newest]
        date = newest[:10]

    alerts = []
    for line in rows:
        r = json.loads(line)
        points, label = _rpi_severity(r["variable"], float(r["calibrated"]))
        if points == 0:
            continue
        lead_decay = 1.0 - 0.10 * (int(r["lead_hours"]) / 24.0 - 1.0)
        score = points * (0.5 + 0.5 * float(r["trust"])) * lead_decay
        level = "INFO"
        for name, thr in RPI_LEVELS:
            if score >= thr:
                level = name
                break
        sources = r["sources"]
        alerts.append({
            "region": r["region"], "variable": r["variable"],
            "lead_hours": int(r["lead_hours"]),
            "value": round(float(r["calibrated"]), 2),
            "trust": round(float(r["trust"]), 3),
            "bust_probability": round(float(r["bust_probability"]), 3),
            "event": label, "severity_points": points,
            "rpi_score": round(score, 1), "level": level,
            "model_spread": round(max(sources.values()) - min(sources.values()), 2),
        })
    alerts.sort(key=lambda a: -a["rpi_score"])
    return {
        "as_of": date,
        "note": "computed on the archived blend record; severity follows IMD "
                "classes (rain 15.6/64.5/115.5 mm, heat 40 degC, wind 50 km/h) "
                "scaled by issued trust and lead-time decay",
        "levels": {name: thr for name, thr in RPI_LEVELS},
        "alerts": alerts,
    }
