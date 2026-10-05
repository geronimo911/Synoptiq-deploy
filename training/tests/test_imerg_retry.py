import sys
from pathlib import Path

import pytest

REALDATA = Path(__file__).resolve().parents[1] / "realdata"
sys.path.insert(0, str(REALDATA))

import fetch_imerg


def test_opendap_zone_means_retries_transient_connection_errors(monkeypatch):
    attempts = {"count": 0}

    class DummyResponse:
        text = "precipitation.precipitation[0], 1.0, 2.0, 3.0"

        def raise_for_status(self):
            return None

    class DummySession:
        def get(self, url, timeout=90):
            attempts["count"] += 1
            if attempts["count"] < 3:
                raise ConnectionError("temporary disconnect")
            return DummyResponse()

    class DummyLogin:
        def get_session(self):
            return DummySession()

    class DummyEarthAccess:
        def login(self, strategy="environment", persist=False):
            return DummyLogin()

    granule = {"umm": {"GranuleUR": "GPM_3IMERGDL.07:3B-DAY.MS.MRG.3IMERGDL.20250101-S000000-E235959.07.V07.nc4"}}
    monkeypatch.setattr(fetch_imerg, "PILOT_ZONES", {"zone_a": {"lat_range": (10.0, 10.5), "lon_range": (70.0, 70.5)}})
    monkeypatch.setattr(fetch_imerg, "REGION_NAMES", {"Z1": "zone_a"})
    monkeypatch.setattr(fetch_imerg, "_earthaccess", lambda: DummyEarthAccess())
    monkeypatch.setattr(fetch_imerg, "_parse_opendap_values", lambda text: [1.0, 2.0, 3.0])

    rows = fetch_imerg._opendap_zone_means("2025-01-01", granule)

    assert rows == {"zone_a": 2.0}
    assert attempts["count"] == 3
