import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "realdata"))

import live_refresh


def _rows(now):
    rows = []
    for zone in live_refresh.ZONES:
        for lead in live_refresh.LEADS:
            for variable, value in (("temperature", 25.0), ("precipitation", 1.0), ("wind_speed", 12.0)):
                rows.append({
                    "model": "GFS", "region": zone, "variable": variable,
                    "lead_hours": lead, "valid_time": (now + timedelta(hours=lead)).replace(tzinfo=None),
                    "forecast_value": value,
                })
    return rows


def test_live_cycle_requires_three_regions_three_variables_and_five_leads():
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    coverage = live_refresh.validate_rows("GFS", _rows(now), now)
    assert coverage["rows"] == 45
    assert coverage["regions"] == 3
    assert coverage["variables"] == 3
    assert coverage["leads"] == 5
    assert coverage["future"] is True
    assert coverage["finite"] is True
