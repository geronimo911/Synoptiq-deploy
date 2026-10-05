"""Cross-instance single-flight lock for the on-demand live refresh.

Render can run more than one web instance, so a process-local
``threading.Lock`` cannot guarantee that only one refresh runs at a time.
PostgreSQL session-level advisory locks are shared across every process
connected to the same database, which is exactly the primitive needed here.

On SQLite (local development and the test suite) advisory locks do not exist,
so this falls back to a process-local lock. That is correct for the
single-process case and keeps the endpoint testable without a PostgreSQL
server. The lock is always released on exit, even if the guarded body raises,
so a failed refresh can never leave the lock permanently held.
"""
from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import text

from app.database import engine

log = logging.getLogger("synoptiq.refresh")

# Fixed lock namespace. Deliberately distinct from the history-seed advisory
# lock (831742609) used by training/realdata/seed_corrected_history.py so the
# two operations can never block each other.
REFRESH_LOCK_KEY = 831742610

# Fallback for non-PostgreSQL backends (SQLite): protects a single process.
_process_lock = threading.Lock()


def backend_is_postgres() -> bool:
    """True when the configured database supports PostgreSQL advisory locks."""
    return engine.dialect.name == "postgresql"


@contextmanager
def refresh_lock() -> Iterator[bool]:
    """Try to acquire the single-flight refresh lock.

    Yields ``True`` when the caller now owns the lock and ``False`` when
    another refresh is already in progress (or the lock backend is
    unavailable). The lock is released on exit in every case.
    """
    if backend_is_postgres():
        connection = None
        acquired = False
        try:
            connection = engine.connect()
        except Exception:  # database unreachable — never crash the request
            log.warning("refresh lock: database connection failed", exc_info=True)
            yield False
            return
        try:
            acquired = bool(
                connection.execute(
                    text("SELECT pg_try_advisory_lock(:key)"), {"key": REFRESH_LOCK_KEY}
                ).scalar()
            )
            yield acquired
        finally:
            if acquired:
                try:
                    connection.execute(
                        text("SELECT pg_advisory_unlock(:key)"), {"key": REFRESH_LOCK_KEY}
                    )
                except Exception:  # pragma: no cover - defensive
                    # If the unlock fails the pooled connection would return to
                    # the pool still holding the lock; discard it instead.
                    log.warning("refresh lock: failed to release advisory lock", exc_info=True)
                    try:
                        connection.invalidate()
                    except Exception:
                        pass
            try:
                connection.close()
            except Exception:  # pragma: no cover - defensive
                pass
    else:
        acquired = _process_lock.acquire(blocking=False)
        try:
            yield acquired
        finally:
            if acquired:
                _process_lock.release()
