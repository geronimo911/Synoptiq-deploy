"""Read-only inventory and builder-discovery diff for REAL_12M caches."""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import re
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
TRAINING_ROOT = ROOT / "training" / "realdata" / "cache" / "12m"
LEGACY_ROOT = ROOT / "data" / "real" / "cache"
OUTPUT_ROOT = ROOT / "artifacts" / "real_12m"
START = pd.Timestamp("2025-02-26")
END = pd.Timestamp("2026-02-25")
PROVIDER_DIRS = {
    "GFS": ("gfs", "gfs_openmeteo"),
    "IFS": ("ifs", "ifs_openmeteo"),
    "AIFS": ("aifs-single", "aifs-single_openmeteo"),
    "ERA5": ("era5",),
    "IMERG": ("imerg",),
}
DATE_RE = re.compile(r"(20\d{2}-\d{2}-\d{2})")


def provider_for(path: Path) -> str | None:
    parts = set(path.parts)
    for provider, names in PROVIDER_DIRS.items():
        if parts.intersection(names):
            return provider
    return None


def parse_dates(frame: pd.DataFrame, filename: str) -> set[str]:
    values: set[str] = set()
    for column in ("valid_date", "selected_valid_timestamp", "run_date"):
        if column in frame:
            parsed = pd.to_datetime(frame[column], errors="coerce", utc=True)
            values.update(parsed.dropna().dt.strftime("%Y-%m-%d").tolist())
    values.update(DATE_RE.findall(filename))
    return values


def scalar_values(frame: pd.DataFrame, column: str) -> list[str]:
    if column not in frame:
        return []
    return sorted({str(value) for value in frame[column].dropna().unique()})


def file_record(path: Path, provider: str, root: Path) -> dict:
    record = {
        "provider": provider,
        "root": str(root),
        "path": str(path),
        "filename": path.name,
        "rows": 0,
        "columns": [],
        "dates": [],
        "zones": [],
        "runs": [],
        "leads": [],
        "read_error": None,
    }
    try:
        frame = pd.read_parquet(path)
        record.update({
            "rows": int(len(frame)),
            "columns": [str(column) for column in frame.columns],
            "dates": sorted(parse_dates(frame, path.name)),
            "zones": scalar_values(frame, "zone"),
            "runs": sorted({
                f"{run_date} {run_hour}Z"
                for run_date, run_hour in zip(
                    frame.get("run_date", pd.Series(dtype=object)),
                    frame.get("run_hour", pd.Series(dtype=object)),
                )
            }),
            "leads": scalar_values(frame, "step") or scalar_values(frame, "lead_hours"),
        })
    except Exception as exc:  # diagnostic must retain the rejection reason
        record["read_error"] = f"{type(exc).__name__}: {exc}"
    return record


def inventory(root: Path) -> list[dict]:
    records = []
    if not root.exists():
        return records
    for path in sorted(root.rglob("*.parquet")):
        provider = provider_for(path)
        if provider:
            records.append(file_record(path, provider, root))
    return records


def summarize(records: list[dict]) -> dict:
    result = {}
    for provider in PROVIDER_DIRS:
        subset = [record for record in records if record["provider"] == provider]
        result[provider] = {
            "file_count": len(subset),
            "row_count": sum(record["rows"] for record in subset),
            "dates": sorted({date_value for record in subset for date_value in record["dates"]}),
            "zones": sorted({zone for record in subset for zone in record["zones"]}),
            "runs": sorted({run for record in subset for run in record["runs"]}),
            "leads": sorted({lead for record in subset for lead in record["leads"]}),
            "read_errors": [record for record in subset if record["read_error"]],
        }
    return result


def immediate_builder_files(root: Path, provider: str) -> list[Path]:
    files = []
    for directory_name in PROVIDER_DIRS[provider]:
        directory = root / directory_name
        if directory.is_dir():
            files.extend(
                path for path in sorted(directory.iterdir())
                if path.is_file() and path.suffix == ".parquet" and not path.name.endswith("_all.parquet")
            )
    return files


def accepted_reasons(path: Path, provider: str, root: Path) -> list[str]:
    reasons = []
    if not path.exists():
        return ["not_found"]
    try:
        frame = pd.read_parquet(path)
    except Exception as exc:
        return [f"read_error:{type(exc).__name__}:{exc}"]
    if path.name.endswith("_all.parquet"):
        reasons.append("excluded_by_builder_all_suffix")
    if provider in {"GFS", "IFS", "AIFS"}:
        if "run_date" not in frame.columns:
            reasons.append("missing_run_date")
        if "run_hour" not in frame.columns:
            reasons.append("missing_run_hour")
        if "step" not in frame.columns:
            reasons.append("missing_step")
        if "zone" not in frame.columns:
            reasons.append("missing_zone")
        required = {"t2m_c", "wind_ms"}
        if provider == "GFS":
            required.add("apcp6_mm" if "apcp6_mm" in frame.columns else "precip_mm")
        else:
            required.add("tp_cum_m" if "tp_cum_m" in frame.columns else "precip_mm")
        missing = sorted(required - set(frame.columns))
        reasons.extend(f"missing_variable:{column}" for column in missing)
        if "zone" in frame:
            unsupported = sorted(set(frame["zone"].dropna().astype(str)) - {
                "kerala_western_ghats", "bay_of_bengal_east_coast", "indo_gangetic_plains"
            })
            reasons.extend(f"unsupported_zone:{zone}" for zone in unsupported)
        if "step" in frame:
            numeric = pd.to_numeric(frame["step"], errors="coerce")
            reasons.extend(["non_numeric_lead"] if numeric.isna().any() else [])
    elif provider == "ERA5":
        required = {"valid_date", "zone", "t2m_c", "wind_kmh"}
        reasons.extend(f"missing_variable:{column}" for column in sorted(required - set(frame.columns)))
    elif provider == "IMERG":
        required = {"valid_date", "zone", "rain_mm"}
        reasons.extend(f"missing_variable:{column}" for column in sorted(required - set(frame.columns)))
    return reasons


def builder_trace(root: Path) -> dict:
    sys.path.insert(0, str(ROOT / "training" / "realdata"))
    import build_training_table as builder

    builder.CACHE_DIR = str(root)
    trace = {}
    for provider, subdir in (("GFS", "gfs_openmeteo"), ("IFS", "ifs_openmeteo"), ("AIFS", "aifs-single"), ("ERA5", "era5"), ("IMERG", "imerg")):
        captured = io.StringIO()
        try:
            with contextlib.redirect_stdout(captured):
                raw = builder.load_model_cache(subdir)
            trace[provider] = {
                "status": "accepted",
                "rows": int(len(raw)),
                "dates": sorted(parse_dates(raw, "")),
                "zones": scalar_values(raw, "zone"),
                "runs": sorted({f"{d} {h}Z" for d, h in zip(raw.get("run_date", []), raw.get("run_hour", []))}),
                "leads": scalar_values(raw, "step"),
                "stdout": captured.getvalue(),
            }
        except SystemExit as exc:
            trace[provider] = {"status": "rejected", "error": str(exc), "stdout": captured.getvalue()}
        except Exception as exc:
            trace[provider] = {"status": "error", "error": f"{type(exc).__name__}: {exc}", "stdout": captured.getvalue()}
    return trace


def coverage_matrix(root: Path) -> list[dict]:
    sys.path.insert(0, str(ROOT / "training" / "realdata"))
    import build_training_table as builder

    builder.CACHE_DIR = str(root)
    tables = {}
    for provider, subdir, model in (("GFS", "gfs_openmeteo", "GFS"), ("IFS", "ifs_openmeteo", "IFS"), ("AIFS", "aifs-single", "AIFS")):
        try:
            raw = builder.load_model_cache(subdir)
            tables[provider] = builder.model_day_table(raw, model, hour=0)
            tables[provider]["valid_date"] = tables[provider]["run_date"] + pd.to_timedelta(tables[provider]["day_k"], unit="D")
        except Exception:
            tables[provider] = pd.DataFrame()
    for provider, subdir in (("ERA5", "era5"), ("IMERG", "imerg")):
        try:
            tables[provider] = builder.load_model_cache(subdir)
            tables[provider]["valid_date"] = pd.to_datetime(tables[provider]["valid_date"])
        except Exception:
            tables[provider] = pd.DataFrame()
    rows = []
    for valid_date in pd.date_range(START, END, freq="D"):
        row = {"date": valid_date.date().isoformat()}
        for provider in PROVIDER_DIRS:
            frame = tables[provider]
            if frame.empty or "valid_date" not in frame:
                count = 0
            else:
                count = int((pd.to_datetime(frame["valid_date"]).dt.normalize() == valid_date).sum())
            key = provider.lower() + "_contexts"
            row[key] = count
            row[provider.lower() + "_status"] = "found" if count else "not_found"
        rows.append(row)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", choices=("both", "12m", "legacy"), default="both")
    args = parser.parse_args()
    roots = []
    if args.root in {"both", "12m"}:
        roots.append(TRAINING_ROOT)
    if args.root in {"both", "legacy"}:
        roots.append(LEGACY_ROOT)
    all_records = []
    root_outputs = {}
    for root in roots:
        records = inventory(root)
        all_records.extend(records)
        accepted = {}
        for provider in PROVIDER_DIRS:
            found = [record for record in records if record["provider"] == provider]
            actual_files = set(immediate_builder_files(root, provider))
            accepted[provider] = {
                "files_found": len(found),
                "files_accepted_by_discovery": len(actual_files),
                "files_rejected_by_discovery": [
                    {"path": record["path"], "reasons": ["not_in_builder_immediate_directory"]}
                    for record in found if Path(record["path"]) not in actual_files
                ],
                "files_accepted": [str(path) for path in sorted(actual_files)],
                "reasons_for_accepted_files": {
                    str(path): accepted_reasons(path, provider, root) for path in sorted(actual_files)
                },
            }
        root_outputs[str(root)] = {
            "resolved_cache_root": str(root),
            "inventory_summary": summarize(records),
            "builder_trace": builder_trace(root),
            "discovery_diff": accepted,
            "date_coverage_matrix": coverage_matrix(root),
        }
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    (OUTPUT_ROOT / "cache_inventory.json").write_text(json.dumps({"roots": root_outputs, "all_files": all_records}, indent=2, default=str), encoding="utf-8")
    selected = root_outputs[str(TRAINING_ROOT)] if str(TRAINING_ROOT) in root_outputs else next(iter(root_outputs.values()))
    (OUTPUT_ROOT / "cache_discovery_diff.json").write_text(json.dumps({"roots": root_outputs}, indent=2, default=str), encoding="utf-8")
    matrix = selected["date_coverage_matrix"]
    pd.DataFrame(matrix).to_csv(OUTPUT_ROOT / "date_coverage_matrix.csv", index=False)
    print(json.dumps({
        "training_cache_root": str(TRAINING_ROOT),
        "legacy_cache_root": str(LEGACY_ROOT),
        "summaries": {root: data["inventory_summary"] for root, data in root_outputs.items()},
        "artifacts": [
            str(OUTPUT_ROOT / "cache_inventory.json"),
            str(OUTPUT_ROOT / "cache_discovery_diff.json"),
            str(OUTPUT_ROOT / "date_coverage_matrix.csv"),
        ],
    }, indent=2, default=str))


if __name__ == "__main__":
    main()
