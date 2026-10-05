"""Demand-driven live refresh (no cron).

Production runs no scheduled refresh service. The flow is:

    user opens the app
      -> the UI renders immediately from the current cached data
      -> the frontend calls POST /api/v1/system/refresh-if-stale in the
         background
      -> if the cached cycle is fresh, nothing happens
      -> if it is stale, one caller wins a PostgreSQL advisory lock, re-checks
         freshness under the lock, and runs the existing worker
         (training/realdata/live_refresh.py) once; concurrent callers get a
         safe "already in progress" answer instead of starting a second job

The "15 minutes" in the environment is a freshness THRESHOLD, not a schedule.
The refresh work itself is not reimplemented here: it delegates to
``live_scheduler.run_refresh_once`` (which runs the same worker as the CLI).
"""
from __future__ import annotations

import json
import logging
import threading

from app import live_scheduler
from app.config import (
    ACTIVE_MODEL_VERSION,
    REFRESH_MIN_AGE_MINUTES,
    REFRESH_ON_REQUEST,
)
from app.refresh_lock import refresh_lock

log = logging.getLogger("synoptiq.refresh")

_background_lock = threading.Lock()
_background_thread: threading.Thread | None = None


def read_manifest() -> dict:
    """Return the live manifest (written by the worker), or {} if absent."""
    path = live_scheduler.MANIFEST
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def last_refresh_iso() -> str | None:
    return read_manifest().get("last_refresh")


def data_age_minutes() -> float | None:
    """Age of the last successful cycle in minutes, or None if unknown."""
    return live_scheduler.manifest_age_minutes()


def is_stale(min_age_minutes: int | None = None) -> bool:
    """True when a refresh should be attempted.

    Data with no recorded cycle at all counts as stale, so a fresh deployment
    fetches once on the first visit.
    """
    threshold = REFRESH_MIN_AGE_MINUTES if min_age_minutes is None else min_age_minutes
    age = data_age_minutes()
    return age is None or age >= threshold


def _model_version() -> str | None:
    return ACTIVE_MODEL_VERSION or read_manifest().get("model_version") or None


def _age_field() -> float | None:
    age = data_age_minutes()
    return None if age is None else round(age, 1)


def _fresh_response() -> dict:
    return {
        "status": "fresh",
        "refreshed": False,
        "last_refresh": last_refresh_iso(),
        "model_version": _model_version(),
        "data_age_minutes": _age_field(),
    }


def _run_refresh_blocking() -> dict:
    """Acquire the shared lock, re-check freshness, run the worker once."""
    with refresh_lock() as acquired:
        if not acquired:
            log.info("refresh-if-stale: lock unavailable (another refresh in progress)")
            return {
                "status": "refresh_in_progress",
                "refreshed": False,
                "last_refresh": last_refresh_iso(),
                "model_version": _model_version(),
            }
        # Re-check under the lock: another instance may have finished a refresh
        # while this caller was queued behind it.
        if not is_stale():
            log.info("refresh-if-stale: already refreshed by another request")
            return {
                "status": "already_refreshed",
                "refreshed": False,
                "last_refresh": last_refresh_iso(),
                "model_version": _model_version(),
            }
        log.info("refresh-if-stale: lock acquired, running live refresh")
        try:
            result = live_scheduler.run_refresh_once()
        except Exception as exc:  # never leak a stack trace to the client
            log.exception("refresh-if-stale: refresh crashed")
            return {
                "status": "refresh_failed",
                "refreshed": False,
                "error": f"{type(exc).__name__}",
                "last_refresh": last_refresh_iso(),
            }
        status = str(result.get("status", "")).upper()
        if status == "OK":
            log.info("refresh-if-stale: completed (last_refresh=%s)", last_refresh_iso())
            return {
                "status": "refreshed",
                "refreshed": True,
                "last_refresh": last_refresh_iso(),
                "model_version": _model_version(),
                "data_age_minutes": _age_field(),
            }
        if status == "SKIPPED":
            return {
                "status": "refresh_in_progress",
                "refreshed": False,
                "last_refresh": last_refresh_iso(),
                "model_version": _model_version(),
            }
        log.warning("refresh-if-stale: refresh did not complete: %s", result)
        return {
            "status": "refresh_failed",
            "refreshed": False,
            "error": str(result.get("detail") or result.get("status") or "refresh failed")[:300],
            "last_refresh": last_refresh_iso(),
        }


def _start_background_refresh() -> bool:
    """Spawn one background refresh thread per process. Returns True if started."""
    global _background_thread
    with _background_lock:
        if _background_thread is not None and _background_thread.is_alive():
            return False
        _background_thread = threading.Thread(
            target=_run_refresh_blocking,
            name="synoptiq-refresh-on-request",
            daemon=True,
        )
        _background_thread.start()
        return True


def refresh_if_stale(wait: bool = False) -> dict:
    """Refresh the live cycle only when the cached data is stale.

    ``wait=False`` (default) returns as soon as a background refresh has been
    started, so the HTTP call stays fast and never blocks the UI.
    ``wait=True`` runs the refresh synchronously and returns its final status
    (``refreshed`` / ``already_refreshed`` / ``refresh_failed``).
    """
    if not REFRESH_ON_REQUEST:
        return {
            "status": "disabled",
            "refreshed": False,
            "last_refresh": last_refresh_iso(),
            "model_version": _model_version(),
        }
    if not is_stale():
        return _fresh_response()
    if wait:
        return _run_refresh_blocking()
    started = _start_background_refresh()
    return {
        "status": "refresh_started" if started else "refresh_in_progress",
        "refreshed": False,
        "last_refresh": last_refresh_iso(),
        "model_version": _model_version(),
        "note": "poll /api/v1/ops/live-refresh/status for the outcome",
    }
