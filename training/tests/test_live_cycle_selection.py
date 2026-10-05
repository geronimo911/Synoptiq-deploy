from datetime import datetime, timezone
from pathlib import Path
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "realdata"))
from ingest_latest_cycle import candidate_cycles, valid_provider_cycle, valid_time_for_run


def test_provider_publish_delay_selects_latest_eligible_six_hour_cycle():
    now = datetime(2026, 9, 28, 16, 30, tzinfo=timezone.utc)

    assert candidate_cycles(now, 4)[0] == datetime(2026, 9, 28, 12, tzinfo=timezone.utc)
    assert candidate_cycles(now, 8)[0] == datetime(2026, 9, 28, 6, tzinfo=timezone.utc)


def test_cycle_candidates_roll_back_to_previous_utc_day():
    now = datetime(2026, 9, 28, 2, 0, tzinfo=timezone.utc)

    assert candidate_cycles(now, 8)[0] == datetime(2026, 9, 27, 18, tzinfo=timezone.utc)


def test_valid_time_tracks_initialization_and_exact_lead():
    assert valid_time_for_run("2026-09-28", 6, 24) == datetime(2026, 9, 29, 6)


def test_provider_cycle_rejects_non_finite_values():
    frame = pd.DataFrame(
        [
            {
                "step": 24,
                "zone": "kerala_western_ghats",
                "t2m_c": 25.0,
                "wind_ms": 2.0,
                "apcp6_mm": np.inf,
            }
        ]
    )

    assert not valid_provider_cycle(frame, "GFS", [24])