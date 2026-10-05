"""Test harness for the training suite.

The training tests exercise modules that reach into the serving app
(``app.config``, ``app.database``, ``app.models_db``), so the backend package
must be importable and the runtime config must be populated before those
modules load. This mirrors ``backend/tests/conftest.py``.
"""
import os
import pathlib
import sys

_PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]

# backend/ on the path so `import app.*` works
sys.path.insert(0, str(_PROJECT_ROOT / "backend"))
# realdata/ on the path so `import live_refresh` / `fetch_ecmwf` work
sys.path.insert(0, str(_PROJECT_ROOT / "training" / "realdata"))

# Deterministic, offline runtime config: never start the background refresh
# scheduler and never touch the network during tests.
os.environ.setdefault("SYNOPTIQ_MODE", "real")
os.environ.setdefault("SYNOPTIQ_AUTO_REFRESH", "0")
os.environ.setdefault("SYNOPTIQ_DATABASE_ROLE", "TRAINING")
os.environ.setdefault(
    "TRAINING_DATABASE_URL",
    f"sqlite:///{(_PROJECT_ROOT / 'data' / 'real' / 'training.sqlite3').as_posix()}",
)
os.environ.setdefault(
    "DATABASE_URL", "postgresql+psycopg://user:password@host:5432/synoptiq"
)
