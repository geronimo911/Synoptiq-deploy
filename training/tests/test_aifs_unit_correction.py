import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "realdata"))

from correct_aifs_precipitation import correct_aifs_precipitation


def test_only_legacy_aifs_precipitation_is_converted_from_mm_to_metres():
    source = pd.DataFrame([
        {"model": "AIFS", "variable": "precipitation", "forecast_value": 9729.0},
        {"model": "AIFS", "variable": "temperature", "forecast_value": 28.0},
        {"model": "IFS", "variable": "precipitation", "forecast_value": 22.0},
    ])

    corrected, report = correct_aifs_precipitation(source)

    assert corrected.loc[0, "forecast_value"] == pytest.approx(9.729)
    assert corrected.loc[1, "forecast_value"] == 28.0
    assert corrected.loc[2, "forecast_value"] == 22.0
    assert report["corrected_rows"] == 1
    assert report["other_rows_unchanged"] is True


def test_already_plausible_aifs_precipitation_is_not_corrected_twice():
    source = pd.DataFrame([
        {"model": "AIFS", "variable": "precipitation", "forecast_value": 9.729},
    ])

    with pytest.raises(ValueError, match="repeated unit correction"):
        correct_aifs_precipitation(source)