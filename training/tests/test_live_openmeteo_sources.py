import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "realdata"))

import live_refresh


def test_live_sources_are_model_specific():
    assert live_refresh.MODELS == {
        "GFS": ("gfs", "NCEP GFS Global 0.11/0.25"),
        "IFS": ("ifs", "ECMWF IFS 0.25"),
        "AIFS": ("aifs-single", "ECMWF AIFS 0.25 Single"),
    }


def test_live_lead_contract_is_production_contract():
    assert live_refresh.LEADS == [24, 48, 72, 96, 120]
    assert live_refresh.ZONES == [
        "kerala_western_ghats",
        "bay_of_bengal_east_coast",
        "indo_gangetic_plains",
    ]
