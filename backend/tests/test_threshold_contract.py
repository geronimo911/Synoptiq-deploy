from app.config import THRESHOLDS


def test_precipitation_threshold_uses_20mm_primary_metric():
    assert THRESHOLDS["precipitation"]["heavy"] == 20.0
    assert THRESHOLDS["precipitation"]["unit"] == "mm/24h"
