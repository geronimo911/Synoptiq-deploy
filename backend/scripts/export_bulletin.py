"""Operational bulletin exporter — the routine workflow's final step.

Reads the operational artifacts (impact engine, peak preservation, regime
router, trust atlas) plus the archived blend record, and writes the standard
morning products a forecast office consumes:

  bulletins/<YYYY-MM-DD>/bulletin.json   machine-readable, all zones
  bulletins/<YYYY-MM-DD>/bulletin.csv    one row per zone, spreadsheet-ready
  bulletins/<YYYY-MM-DD>/bulletin.txt    plain-text bulletin for fax/email
  bulletins/<YYYY-MM-DD>/cap/<zone>.xml  CAP 1.2 alerts (one per zone)

It also appends one line to ops/run_log.jsonl so the dashboard can show when
the workflow last ran and what it produced. Re-running for the same day is
idempotent: the directory is rewritten, the log gains one entry.

Usage:
  python backend/scripts/export_bulletin.py            # latest archived day
  python backend/scripts/export_bulletin.py --date 2025-05-30
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

ARTIFACTS = ROOT / "artifacts" / "real_12m_aifs_corrected"
RESEARCH = ARTIFACTS / "research"
BULLETINS = ARTIFACTS / "bulletins"
OPS_DIR = ROOT / "ops"


def _load(path: Path) -> dict:
    if not path.is_file():
        raise SystemExit(f"missing artifact: {path} — run "
                         f"backend/scripts/build_advanced_artifacts.py first")
    return json.loads(path.read_text(encoding="utf-8"))


def build_bulletin(date: str | None) -> dict:
    from app.impact import VULNERABILITY, cap_xml, compute_isi
    from app.regimes import policy_shift

    impact = _load(RESEARCH / "impact_engine.json")
    peaks = _load(RESEARCH / "peak_preservation.json")
    router = _load(RESEARCH / "regime_router.json")
    atlas = _load(RESEARCH / "trust_atlas.json")

    # choose the day: explicit, else the artifact's latest
    latest_date = impact["latest"]["date"]
    day = date or latest_date
    if day == latest_date:
        zones = impact["latest"]["zones"]
    else:
        zones = _zones_for_day(day)

    regime = router.get("current", {})
    if day != latest_date:
        regime = _regime_for_day(day, router)

    conformal = {v: e.get("conformal", {}) for v, e in peaks["variables"].items()}
    peak_notes = {v: e.get("conclusion", "") for v, e in peaks["variables"].items()}

    zones_out = []
    for z in zones:
        zone = z["zone"]
        cells = [c for c in atlas["cells"] if c["region"] == zone]
        dom = {}
        for c in cells:
            dom.setdefault(c["variable"], c["dominating_model"])
        shift = policy_shift(regime.get("regime", "normal"),
                             regime.get("profile", {}).get("mean_weights", {})
                             if regime.get("profile") else {},
                             router.get("global_mean_weights", {}))
        zones_out.append({
            **z,
            "dominating_model": dom,
            "conformal_90": {
                v: conformal.get(v, {}).get("q90") for v in
                ("precipitation", "temperature", "wind_speed")
            },
        })

    return {
        "bulletin_id": f"synoptiq-bulletin-{day}",
        "issued_utc": datetime.now(timezone.utc).isoformat(),
        "valid_date": day,
        "source": {
            "record": "REAL_12M archived blend record",
            "blend": "Synoptiq hybrid GFS/IFS/AIFS meta-model",
            "truth_reference": "IMERG Late V07 rainfall; NASA POWER temperature/wind",
        },
        "synoptic_regime": {
            "regime": regime.get("regime"),
            "label": (regime.get("profile") or {}).get("label", regime.get("regime")),
            "policy_shift": regime.get("profile", {}).get("policy_shift", {}).get("statement")
            if regime.get("profile") else None,
        },
        "zones": zones_out,
        "extreme_layer_notes": peak_notes,
        "generated_by": "backend/scripts/export_bulletin.py",
    }


def _zones_for_day(day: str) -> list:
    """Rebuild per-zone ISI for an arbitrary archived day."""
    import pandas as pd
    from app.impact import VULNERABILITY, compute_isi

    blends = ARTIFACTS / "inference" / "historical_blends.jsonl"
    rows = []
    with blends.open(encoding="utf-8") as fh:
        for line in fh:
            if f'"{day}' in line:
                r = json.loads(line)
                if r["valid_time"].startswith(day):
                    rows.append(r)
    if not rows:
        raise SystemExit(f"no archived contexts for {day}")
    df = pd.DataFrame(rows)
    out = []
    for zone, g in df.groupby("region"):
        values = {}
        for variable in ("precipitation", "temperature", "wind_speed"):
            v = g[g.variable == variable]
            if len(v):
                values[variable] = float(v.calibrated.max())
        if values:
            res = compute_isi(zone, values)
            res["vulnerability_rationale"] = VULNERABILITY.get(zone, {}).get("rationale")
            out.append(res)
    out.sort(key=lambda r: -r["isi"])
    return out


def _regime_for_day(day: str, router: dict) -> dict:
    import pandas as pd
    blends = ARTIFACTS / "inference" / "historical_blends.jsonl"
    rows = []
    with blends.open(encoding="utf-8") as fh:
        for line in fh:
            if f'"{day}' in line:
                r = json.loads(line)
                if r["valid_time"].startswith(day):
                    rows.append(r)
    if not rows:
        return router.get("current", {})
    probs = rows[0].get("regime_probs", {})
    regime = max(probs, key=probs.get) if probs else "normal"
    profile = next((p for p in router.get("profiles", []) if p["regime"] == regime), None)
    return {"as_of": day, "regime": regime,
            "regime_probs": {k: round(float(v), 4) for k, v in probs.items()},
            "profile": profile}


def write_outputs(bulletin: dict) -> dict:
    from app.impact import cap_xml

    day = bulletin["valid_date"]
    outdir = BULLETINS / day
    (outdir / "cap").mkdir(parents=True, exist_ok=True)
    (outdir / "bulletin.json").write_text(
        json.dumps(bulletin, indent=1, allow_nan=False), encoding="utf-8")

    # CSV — one row per zone
    with (outdir / "bulletin.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["valid_date", "zone", "isi", "colour", "severity",
                    "precipitation_mm", "wind_kmh", "temperature_c",
                    "dominating_model_rain", "conformal90_rain",
                    "regime", "advisory"])
        for z in bulletin["zones"]:
            comp = z.get("components", {})
            w.writerow([
                day, z["zone"], z["isi"], z["colour"], z["severity"],
                comp.get("precipitation", {}).get("value", ""),
                comp.get("wind_speed", {}).get("value", ""),
                comp.get("temperature", {}).get("value", ""),
                z.get("dominating_model", {}).get("precipitation", ""),
                z.get("conformal_90", {}).get("precipitation", ""),
                bulletin["synoptic_regime"]["regime"], z.get("advisory", ""),
            ])

    # Plain text bulletin
    lines = [
        "=" * 72,
        f"SYNOPTIQ HYBRID AI-NWP BLEND — DAILY BULLETIN",
        f"Valid date: {day}    Issued: {bulletin['issued_utc']}",
        f"Synoptic regime: {bulletin['synoptic_regime']['label']}",
        "=" * 72,
    ]
    if bulletin["synoptic_regime"].get("policy_shift"):
        lines += ["", f"Blending policy: {bulletin['synoptic_regime']['policy_shift']}"]
    for z in bulletin["zones"]:
        comp = z.get("components", {})
        lines += [
            "",
            f"[{z['colour']}] {z['zone'].replace('_', ' ')} — ISI {z['isi']}/10 ({z['severity']})",
            f"    Rain {comp.get('precipitation', {}).get('value', 'n/a')} mm | "
            f"Wind {comp.get('wind_speed', {}).get('value', 'n/a')} km/h | "
            f"Heat {comp.get('temperature', {}).get('value', 'n/a')} C",
            f"    Dominant model (rain): {z.get('dominating_model', {}).get('precipitation', 'n/a')}",
            f"    {z.get('advisory', '')}",
        ]
    lines += ["", "-" * 72,
              "Extreme-layer status (honest notes):"]
    for var, note in bulletin["extreme_layer_notes"].items():
        lines.append(f"  * {var}: {note[:220]}")
    lines += ["", "=" * 72, "Generated by Synoptiq. Pilot zones only; district "
              "shapefiles extend the same chain.", ""]
    (outdir / "bulletin.txt").write_text("\n".join(lines), encoding="utf-8")

    # CAP alerts
    cap_files = []
    for z in bulletin["zones"]:
        from datetime import datetime as _dt
        vfrom = _dt.fromisoformat(f"{day}T00:00:00+00:00")
        xml = cap_xml(zone=z["zone"], isi_result=z, valid_from=vfrom,
                      valid_to=vfrom + timedelta(hours=48))
        path = outdir / "cap" / f"{z['zone']}.xml"
        path.write_text(xml, encoding="utf-8")
        cap_files.append(str(path.relative_to(ROOT)))

    # ops log
    OPS_DIR.mkdir(parents=True, exist_ok=True)
    entry = {
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "valid_date": day,
        "zones": len(bulletin["zones"]),
        "max_colour": max((z["colour"] for z in bulletin["zones"]),
                          key=lambda c: ["GREEN", "YELLOW", "ORANGE", "RED"].index(c))
        if bulletin["zones"] else "GREEN",
        "artifacts": [
            str((outdir / "bulletin.json").relative_to(ROOT)),
            str((outdir / "bulletin.csv").relative_to(ROOT)),
            str((outdir / "bulletin.txt").relative_to(ROOT)),
            *cap_files,
        ],
    }
    with (OPS_DIR / "run_log.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")
    return entry


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None)
    args = ap.parse_args()
    bulletin = build_bulletin(args.date)
    entry = write_outputs(bulletin)
    print(f"bulletin written for {entry['valid_date']} — "
          f"{entry['zones']} zones, highest colour {entry['max_colour']}")
    for z in bulletin["zones"]:
        print(f"  [{z['colour']:6}] {z['zone']}: ISI {z['isi']}")


if __name__ == "__main__":
    main()
