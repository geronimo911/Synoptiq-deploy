import sys
from pathlib import Path

import pytest

REALDATA = Path(__file__).resolve().parents[1] / "realdata"
sys.path.insert(0, str(REALDATA))

from build_training_table import validate_requested_coverage
from window import TRAINING_END, TRAINING_START, validate_window


def test_prototype_window_is_fixed():
    validate_window(TRAINING_START.isoformat(), TRAINING_END.isoformat())

    with pytest.raises(SystemExit, match="fixed"):
        validate_window("2025-02-25", TRAINING_END.isoformat())


def test_incomplete_window_writes_report_and_fails(tmp_path, monkeypatch):
    import build_training_table
    import pandas as pd

    monkeypatch.setattr(build_training_table, "CACHE_DIR", str(tmp_path))
    with pytest.raises(SystemExit, match="incomplete"):
        validate_requested_coverage(
            pd.DataFrame(columns=["model", "region", "valid_time", "lead_hours", "variable"]),
            pd.DataFrame(columns=["region", "valid_time", "variable"]),
        )

    report = tmp_path / "completeness_report.json"
    assert report.exists()
    assert len(report.read_text(encoding="utf-8")) > 0