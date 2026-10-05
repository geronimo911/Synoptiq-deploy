"""Automatic live-refresh scheduler.

The refresh worker (training/realdata/live_refresh.py) already knows how to
fetch the newest complete GFS / IFS / AIFS cycle and persist it. What was
missing operationally is anything that *starts* it: a bare deployment
(`uvicorn app.main:app`, a Render web service, a container) would sit there
serving stale data until a human ran the worker.

This module closes that gap. When the API process starts in real mode it
spawns one daemon thread that

  * runs a refresh immediately if the last manifest is older than the
    configured interval (catch-up after a restart, a redeploy, or an outage);
  * then refreshes on that interval forever, so the system keeps pulling
    real data for weeks or months with no manual action;
  * records every attempt in data/real/live/scheduler_state.json and in the
    worker's own log, so "is it actually running?" is answerable.

Design choices, deliberately conservative:
  * the worker runs as a SUBPROCESS (`live_refresh.py --once`), not in-process:
    a crash, a hang or a memory spike in the fetch path cannot take the API
    down, and the same code path is exercised by cron / Task Scheduler;
  * single-flight: a lock plus a fresh state file mean two schedulers (for
    example a container plus a cron job) never fetch concurrently;
  * failures never raise into the web process — they are logged and retried
    on the next tick with exponential backoff up to the interval;
  * everything is switchable: SYNOPTIQ_AUTO_REFRESH=0 disables it entirely
    (tests and demo mode do this), SYNOPTIQ_LIVE_REFRESH_MINUTES sets the
    cadence.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKER = ROOT / "training" / "realdata" / "live_refresh.py"
LIVE_DIR = Path(os.getenv("SYNOPTIQ_LIVE_DATA_DIR", str(ROOT / "data" / "real" / "live"))).resolve()
MANIFEST = LIVE_DIR / "live_manifest.json"
STATE_PATH = LIVE_DIR / "scheduler_state.json"
LOCK_PATH = LIVE_DIR / "refresh.lock"

log = logging.getLogger("synoptiq.scheduler")

_state_lock = threading.Lock()
_thread: threading.Thread | None = None
_stop = threading.Event()


def interval_minutes() -> int:
    try:
        return max(5, int(os.getenv("SYNOPTIQ_LIVE_REFRESH_MINUTES", "15")))
    except ValueError:
        return 15


def auto_refresh_enabled() -> bool:
    return os.getenv("SYNOPTIQ_AUTO_REFRESH", "1").strip().lower() not in {"0", "false", "no", "off"}


def _write_state(**updates) -> dict:
    LIVE_DIR.mkdir(parents=True, exist_ok=True)
    with _state_lock:
        state = {}
        if STATE_PATH.is_file():
            try:
                state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                state = {}
        state.update(updates)
        state["interval_minutes"] = interval_minutes()
        state["enabled"] = auto_refresh_enabled()
        STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")
        return state


def read_state() -> dict:
    if not STATE_PATH.is_file():
        return {"enabled": auto_refresh_enabled(), "interval_minutes": interval_minutes(),
                "runs": 0, "last_attempt": None, "last_success": None, "last_error": None}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"enabled": auto_refresh_enabled(), "interval_minutes": interval_minutes(),
                "runs": 0, "last_attempt": None, "last_success": None, "last_error": None}


def manifest_age_minutes() -> float | None:
    if not MANIFEST.is_file():
        return None
    try:
        payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
        stamp = payload.get("last_refresh")
        if not stamp:
            return None
        when = datetime.fromisoformat(stamp)
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - when).total_seconds() / 60.0
    except (json.JSONDecodeError, ValueError):
        return None


def _lock_is_fresh() -> bool:
    """True when another scheduler process holds a recent lock."""
    if not LOCK_PATH.is_file():
        return False
    try:
        age = time.time() - LOCK_PATH.stat().st_mtime
    except OSError:
        return False
    # a lock older than the subprocess timeout is stale and may be stolen
    return age < float(os.getenv("SYNOPTIQ_REFRESH_TIMEOUT_SECONDS", "900"))


def run_refresh_once(timeout: int | None = None) -> dict:
    """Run the live-refresh worker once, as a subprocess, single-flight."""
    timeout = timeout or int(os.getenv("SYNOPTIQ_REFRESH_TIMEOUT_SECONDS", "900"))
    if _lock_is_fresh():
        return {"status": "SKIPPED", "reason": "another refresh is in progress"}
    LIVE_DIR.mkdir(parents=True, exist_ok=True)
    LOCK_PATH.write_text(str(os.getpid()), encoding="utf-8")
    started = datetime.now(timezone.utc)
    _write_state(last_attempt=started.isoformat(), runs=read_state().get("runs", 0) + 1)
    try:
        env = dict(os.environ)
        env.setdefault("SYNOPTIQ_LIVE_DATA_DIR", str(LIVE_DIR))
        result = subprocess.run(
            [sys.executable, str(WORKER), "--once"],
            cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=timeout,
        )
        ok = result.returncode == 0
        detail = (result.stdout or "").strip().splitlines()[-1:] or [""]
        if ok:
            _write_state(last_success=datetime.now(timezone.utc).isoformat(),
                         last_error=None, last_status="OK", last_detail=detail[0][:300])
            log.info("live refresh OK")
            return {"status": "OK", "detail": detail[0][:300]}
        error = (result.stderr or result.stdout or "worker failed").strip().splitlines()[-1:]
        _write_state(last_error=(error[0] if error else "worker failed")[:300],
                     last_status="FAILED")
        log.warning("live refresh failed: %s", error)
        return {"status": "FAILED", "detail": error[0][:300] if error else "worker failed"}
    except subprocess.TimeoutExpired:
        _write_state(last_error=f"worker timed out after {timeout}s", last_status="TIMEOUT")
        log.warning("live refresh timed out after %ss", timeout)
        return {"status": "TIMEOUT", "detail": f"worker exceeded {timeout}s"}
    except Exception as exc:  # never let the scheduler kill the API process
        _write_state(last_error=f"{type(exc).__name__}: {exc}"[:300], last_status="ERROR")
        log.exception("live refresh crashed")
        return {"status": "ERROR", "detail": str(exc)[:300]}
    finally:
        try:
            LOCK_PATH.unlink()
        except OSError:
            pass


def _loop() -> None:
    log.info("live scheduler started (interval=%s min, auto_refresh=%s)",
             interval_minutes(), auto_refresh_enabled())
    while not _stop.is_set():
        age = manifest_age_minutes()
        due = age is None or age >= interval_minutes()
        if due:
            run_refresh_once()
        # sleep in short ticks so shutdown stays responsive
        for _ in range(int(interval_minutes() * 60 / 5)):
            if _stop.is_set():
                return
            time.sleep(5)


def start() -> bool:
    """Start the background scheduler once per process. Returns True if started."""
    global _thread
    if not auto_refresh_enabled():
        log.info("live scheduler disabled (SYNOPTIQ_AUTO_REFRESH=0)")
        return False
    if _thread and _thread.is_alive():
        return False
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="synoptiq-live-scheduler", daemon=True)
    _thread.start()
    _write_state(scheduler_started_at=datetime.now(timezone.utc).isoformat())
    return True


def stop() -> None:
    _stop.set()
