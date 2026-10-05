from __future__ import annotations

import sys
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "realdata"))
from common import PILOT_ZONES, STEPS, forecast_cache_is_complete
from build_training_table import model_day_table
from fetch_ecmwf import OPEN_DATA_START, AIFS_V2_START, model_generation, _tp_to_metres


def _rows(model: str) -> pd.DataFrame:
    rows = []
    for step in (24, 30, 36, 42, 48):
        row = {
            "model": model,
            "run_date": "2026-01-01",
            "run_hour": 12,
            "step": step,
            "zone": "kerala_western_ghats",
            "t2m_c": 20.0,
            "wind_ms": 5.0,
        }
        if model == "GFS":
            row["apcp6_mm"] = 1.0
        else:
            row["tp_cum_m"] = step / 1000.0
        rows.append(row)
    return pd.DataFrame(rows)


def test_gfs_precipitation_uses_window_30_to_48_for_day_one():
    result = model_day_table(_rows("GFS"), "GFS", hour=12)
    day_one = result[result["day_k"] == 1].iloc[0]
    assert day_one["run_date"] + pd.to_timedelta(day_one["day_k"], unit="D") == pd.Timestamp("2026-01-02")
    assert day_one["lead_hours"] == 24
    assert day_one["run_hour"] == 12
    assert day_one["precipitation"] == 4.0


def test_ecmwf_cumulative_precipitation_is_differenced_at_window_edges():
    result = model_day_table(_rows("ifs"), "IFS", hour=12)
    day_one = result[result["day_k"] == 1].iloc[0]
    assert day_one["run_date"] + pd.to_timedelta(day_one["day_k"], unit="D") == pd.Timestamp("2026-01-02")
    assert day_one["lead_hours"] == 24
    assert day_one["precipitation"] == 24.0


def test_ecmwf_precipitation_grib_units_are_normalized_before_aggregation():
    assert np.isclose(_tp_to_metres(2.38, "kg m**-2"), 0.00238)
    assert _tp_to_metres(0.00238, "m") == 0.00238


def test_ecmwf_common_start_and_aifs_generation_boundary():
    assert OPEN_DATA_START.isoformat() == "2025-02-25"
    assert model_generation("aifs-single", "2026-05-11") == "AIFS Single v1"
    assert model_generation("aifs-single", "2026-05-12") == "AIFS Single v2"


def test_forecast_cache_requires_every_lead_and_region():
    rows = []
    for step, zone in product(STEPS, PILOT_ZONES):
        rows.append({
            "step": step,
            "zone": zone,
            "t2m_c": 20.0,
            "wind_ms": 5.0,
            "apcp6_mm": 1.0,
        })
    complete = pd.DataFrame(rows)
    assert forecast_cache_is_complete(complete, "GFS")
    assert not forecast_cache_is_complete(complete.iloc[:-1], "GFS")