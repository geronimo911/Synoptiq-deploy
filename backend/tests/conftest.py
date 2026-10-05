import os
import sys
import pathlib

# Resolve relative to this file, not the caller's cwd — running `pytest` from
# backend/ (the natural place, since these tests live in backend/tests/)
# previously resolved "./data/real/training.sqlite3" against backend/, not
# the project root where data/real actually lives, tripping the real-mode
# path guard in app/database.py for the wrong reason.
_PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
_TRAINING_DB = (_PROJECT_ROOT / "data" / "real" / "training.sqlite3").as_posix()

os.environ.setdefault("SYNOPTIQ_MODE", "real")
# never let the test suite start the background live-refresh scheduler or
# touch the network: the scheduler is exercised explicitly in its own test
os.environ.setdefault("SYNOPTIQ_AUTO_REFRESH", "0")
os.environ.setdefault("SYNOPTIQ_DATABASE_ROLE", "TRAINING")
os.environ.setdefault("TRAINING_DATABASE_URL", f"sqlite:///{_TRAINING_DB}")
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://user:password@host:5432/synoptiq")

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
