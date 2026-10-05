import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "realdata"))

import build_training_table as b


def test_load_model_cache_includes_aifs_openmeteo_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(b, "CACHE_DIR", str(tmp_path))

    archive_dir = tmp_path / "aifs-single"
    fallback_dir = tmp_path / "aifs-single_openmeteo"
    archive_dir.mkdir()
    fallback_dir.mkdir()

    archive_df = pd.DataFrame([
        {
            "run_date": "2025-03-03",
            "run_hour": 0,
            "step": 24,
            "zone": "kerala_western_ghats",
            "t2m_c": 20.0,
            "wind_ms": 3.0,
            "tp_cum_m": 0.01,
        }
    ])
    fallback_df = pd.DataFrame([
        {
            "run_date": "2025-02-25",
            "run_hour": 0,
            "step": 24,
            "zone": "kerala_western_ghats",
            "t2m_c": 20.0,
            "wind_ms": 3.0,
            "precip_mm": 0.02,
            "selected_valid_timestamp": "2025-02-26T00:00:00Z",
        },
        {
            "run_date": "2025-02-24",
            "run_hour": 0,
            "step": 48,
            "zone": "kerala_western_ghats",
            "t2m_c": 21.0,
            "wind_ms": 4.0,
            "precip_mm": 0.03,
            "selected_valid_timestamp": "2025-02-26T00:00:00Z",
        },
    ])

    archive_df.to_parquet(archive_dir / "aifs_2025-03-03.parquet", index=False)
    fallback_df.to_parquet(fallback_dir / "openmeteo_aifs-single_2025-02-26.parquet", index=False)

    loaded = b.load_model_cache("aifs-single")
    assert len(loaded) == 3
    assert set(pd.to_datetime(loaded["run_date"]).dt.strftime("%Y-%m-%d")) == {"2025-02-26", "2025-03-03"}
    assert set(loaded.loc[pd.to_datetime(loaded["run_date"]) == pd.Timestamp("2025-02-26"), "step"]) == {24, 48}
