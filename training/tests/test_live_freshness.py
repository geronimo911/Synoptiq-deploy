import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "realdata"))

import live_refresh


def test_live_rows_must_be_future_dated():
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    rows = [{
        "model": "GFS", "region": live_refresh.ZONES[0], "variable": "temperature",
        "lead_hours": 24, "valid_time": (now - timedelta(hours=1)).replace(tzinfo=None),
        "forecast_value": 25.0,
    }]
    try:
        live_refresh.validate_rows("GFS", rows, now)
    except RuntimeError as exc:
        assert "future=False" in str(exc)
    else:
        raise AssertionError("past live rows must be rejected")
