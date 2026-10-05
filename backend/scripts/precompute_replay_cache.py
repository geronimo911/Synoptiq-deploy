"""Precompute the offline Historical Replay cache used as the default demo path."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.database import SessionLocal
from app.replay import precompute_replay_cache
from app.config import PILOT_ZONES


def main():
    db = SessionLocal()
    total = 0
    for region in PILOT_ZONES:
        ids = precompute_replay_cache(db, region, variable="precipitation", lead_hours=72)
        print(f"{region}: cached {len(ids)} replay events -> {ids}")
        total += len(ids)
    db.close()
    print(f"Total replay events cached: {total}")


if __name__ == "__main__":
    main()
