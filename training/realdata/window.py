"""Fixed prototype training window and coverage requirements."""
from __future__ import annotations

from datetime import date, timedelta

TRAINING_START = date(2025, 2, 26)
TRAINING_END = date(2026, 2, 25)
PROTOTYPE_START = date(2025, 2, 26)
PROTOTYPE_END = date(2025, 3, 10)
REQUIRED_MODELS = ("GFS", "IFS", "AIFS")
REQUIRED_REGIONS = (
    "kerala_western_ghats",
    "bay_of_bengal_east_coast",
    "indo_gangetic_plains",
)
REQUIRED_VARIABLES = ("precipitation", "temperature", "wind_speed")
REQUIRED_LEADS = (24, 48, 72, 96, 120)


def requested_dates() -> list[date]:
    return [
        TRAINING_START + timedelta(days=offset)
        for offset in range((TRAINING_END - TRAINING_START).days + 1)
    ]


def validate_window(start: str, end: str) -> None:
    if start != TRAINING_START.isoformat() or end != TRAINING_END.isoformat():
        raise SystemExit(
            "The prototype training window is fixed at "
            f"{TRAINING_START.isoformat()} through {TRAINING_END.isoformat()}; "
            f"requested {start} through {end}."
        )


def validate_prototype_window(start: str, end: str) -> None:
    if start != PROTOTYPE_START.isoformat() or end != PROTOTYPE_END.isoformat():
        raise SystemExit(
            "--real-prototype is fixed at "
            f"{PROTOTYPE_START.isoformat()} through {PROTOTYPE_END.isoformat()}; "
            f"requested {start} through {end}."
        )