"""Correct legacy REAL_12M AIFS precipitation values decoded in kg m^-2 as metres."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd


def correct_aifs_precipitation(frame: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    required = {"model", "variable", "forecast_value"}
    if not required.issubset(frame.columns):
        raise ValueError(f"Forecast table is missing columns: {sorted(required - set(frame.columns))}")
    mask = (frame["model"] == "AIFS") & (frame["variable"] == "precipitation")
    if not mask.any():
        raise ValueError("No AIFS precipitation rows found; refusing to emit an unmodified correction.")

    values = pd.to_numeric(frame.loc[mask, "forecast_value"], errors="coerce")
    if not np.isfinite(values.to_numpy(dtype=float)).all() or (values < 0).any():
        raise ValueError("AIFS precipitation has invalid or negative values.")
    before_max = float(values.max())
    if before_max < 1000:
        raise ValueError(
            "AIFS precipitation does not match the known legacy kg m^-2-as-metres signature; "
            "refusing a potentially repeated unit correction."
        )

    corrected = frame.copy()
    corrected.loc[mask, "forecast_value"] = values / 1000.0
    report = {
        "source_unit": "kg m^-2 (equivalent to mm water depth)",
        "legacy_interpretation": "metres",
        "correction_factor": 0.001,
        "corrected_rows": int(mask.sum()),
        "aifs_precipitation_max_before": before_max,
        "aifs_precipitation_max_after": float(corrected.loc[mask, "forecast_value"].max()),
        "other_rows_unchanged": True,
    }
    return corrected, report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    forecasts = pd.read_parquet(args.input)
    corrected, report = correct_aifs_precipitation(forecasts)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    corrected.to_parquet(temporary, index=False, compression="zstd")
    temporary.replace(args.output)
    report_path = args.output.with_suffix(args.output.suffix + ".unit-correction.json")
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(args.output), **report}, indent=2), flush=True)


if __name__ == "__main__":
    main()