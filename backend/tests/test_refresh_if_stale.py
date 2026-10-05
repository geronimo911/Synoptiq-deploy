"""Tests for the demand-driven refresh architecture and production config.

These cover the deployment contract:

  * DATABASE_URL is read from the environment and never reaches the browser;
  * a fresh cycle does NOT trigger a live refresh;
  * a stale cycle DOES trigger exactly one live refresh;
  * concurrent callers are serialised by the shared lock;
  * a failed refresh returns a safe response, releases the lock, and leaves the
    cached data in place;
  * /health stays lightweight and never refreshes;
  * CORS is configurable and never a wildcard;
  * the Render blueprint has no cron service and carries the refresh policy;
  * the history seed is idempotent and skips the archive when already seeded.

The live refresh itself is always mocked — no external downloads run here.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))


def _write_manifest(path: Path, when: datetime) -> None:
    path.write_text(
        json.dumps({"last_refresh": when.isoformat(), "model_version": "test-version"}),
        encoding="utf-8",
    )


@pytest.fixture()
def stale_manifest(tmp_path, monkeypatch):
    """A live manifest that is well past the freshness threshold."""
    from app import live_scheduler

    manifest = tmp_path / "live_manifest.json"
    _write_manifest(manifest, datetime.now(timezone.utc) - timedelta(hours=2))
    monkeypatch.setattr(live_scheduler, "MANIFEST", manifest)
    return manifest


@pytest.fixture()
def fresh_manifest(tmp_path, monkeypatch):
    from app import live_scheduler

    manifest = tmp_path / "live_manifest.json"
    _write_manifest(manifest, datetime.now(timezone.utc))
    monkeypatch.setattr(live_scheduler, "MANIFEST", manifest)
    return manifest


# --------------------------------------------------------------------------
# 1. DATABASE_URL configuration
# --------------------------------------------------------------------------

def test_database_url_comes_from_environment():
    from app import config

    assert config.DATABASE_URL
    assert config.DATABASE_URL == os.environ["DATABASE_URL"].strip()


def test_frontend_source_never_references_database_credentials():
    """No DATABASE_URL / VITE_DATABASE_URL may appear anywhere in the frontend."""
    frontend = PROJECT_ROOT / "frontend"
    offenders = []
    for path in frontend.rglob("*"):
        if not path.is_file():
            continue
        if "node_modules" in path.parts or path.name == "package-lock.json":
            continue
        if path.suffix not in {".ts", ".tsx", ".js", ".jsx", ".json", ".example"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "VITE_DATABASE_URL" in text or "DATABASE_URL" in text:
            offenders.append(str(path.relative_to(PROJECT_ROOT)))
    assert offenders == [], f"database credentials leaked to the frontend: {offenders}"


# --------------------------------------------------------------------------
# 2 & 3. fresh vs stale behaviour
# --------------------------------------------------------------------------

def test_fresh_data_does_not_trigger_refresh(fresh_manifest, monkeypatch):
    from app import live_scheduler, on_demand_refresh as odr

    calls: list[int] = []
    monkeypatch.setattr(
        live_scheduler, "run_refresh_once", lambda *a, **k: calls.append(1) or {"status": "OK"}
    )

    result = odr.refresh_if_stale(wait=True)

    assert result["status"] == "fresh"
    assert result["refreshed"] is False
    assert calls == []


def test_stale_data_runs_the_refresh_once(stale_manifest, monkeypatch):
    from app import live_scheduler, on_demand_refresh as odr

    calls: list[int] = []

    def fake_refresh():
        calls.append(1)
        _write_manifest(stale_manifest, datetime.now(timezone.utc))
        return {"status": "OK", "detail": "cycle complete"}

    monkeypatch.setattr(live_scheduler, "run_refresh_once", fake_refresh)

    result = odr.refresh_if_stale(wait=True)

    assert result["status"] == "refreshed"
    assert result["refreshed"] is True
    assert len(calls) == 1
    assert result["last_refresh"] is not None


def test_refresh_disabled_short_circuits(stale_manifest, monkeypatch):
    from app import on_demand_refresh as odr

    monkeypatch.setattr(odr, "REFRESH_ON_REQUEST", False)
    result = odr.refresh_if_stale(wait=True)
    assert result["status"] == "disabled"
    assert result["refreshed"] is False


# --------------------------------------------------------------------------
# 4 & 5. concurrency: only one refresh, others get a safe answer
# --------------------------------------------------------------------------

def test_refresh_lock_is_single_flight():
    from app.refresh_lock import refresh_lock

    with refresh_lock() as first:
        assert first is True
        with refresh_lock() as second:
            assert second is False
    # released, so it can be taken again
    with refresh_lock() as third:
        assert third is True


def test_concurrent_callers_run_only_one_refresh(stale_manifest, monkeypatch):
    from app import live_scheduler, on_demand_refresh as odr

    started = threading.Event()
    release = threading.Event()
    calls: list[int] = []

    def slow_refresh():
        calls.append(1)
        started.set()
        release.wait(timeout=10)
        _write_manifest(stale_manifest, datetime.now(timezone.utc))
        return {"status": "OK"}

    monkeypatch.setattr(live_scheduler, "run_refresh_once", slow_refresh)

    results: dict[str, dict] = {}

    def worker():
        results["first"] = odr.refresh_if_stale(wait=True)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    assert started.wait(timeout=10), "the first refresh never started"

    # Second caller arrives while the first holds the lock.
    results["second"] = odr.refresh_if_stale(wait=True)

    release.set()
    thread.join(timeout=10)

    assert results["first"]["status"] == "refreshed"
    assert results["second"]["status"] == "refresh_in_progress"
    assert results["second"]["refreshed"] is False
    assert len(calls) == 1


def test_already_refreshed_when_another_caller_won(stale_manifest, monkeypatch):
    from app import live_scheduler, on_demand_refresh as odr

    calls: list[int] = []
    monkeypatch.setattr(
        live_scheduler, "run_refresh_once", lambda *a, **k: calls.append(1) or {"status": "OK"}
    )
    # stale on the first check, fresh by the time we hold the lock (as if
    # another instance finished in between)
    states = iter([True, False])
    monkeypatch.setattr(odr, "is_stale", lambda *a, **k: next(states))

    result = odr.refresh_if_stale(wait=True)

    assert result["status"] == "already_refreshed"
    assert result["refreshed"] is False
    assert calls == []


# --------------------------------------------------------------------------
# 6 & 7. failure safety and data preservation
# --------------------------------------------------------------------------

def test_refresh_failure_is_safe_and_releases_lock(stale_manifest, monkeypatch):
    from app import live_scheduler, on_demand_refresh as odr
    from app.refresh_lock import refresh_lock

    monkeypatch.setattr(
        live_scheduler,
        "run_refresh_once",
        lambda *a, **k: {"status": "FAILED", "detail": "provider unreachable"},
    )

    result = odr.refresh_if_stale(wait=True)

    assert result["status"] == "refresh_failed"
    assert result["refreshed"] is False
    assert "error" in result
    # the lock was released even though the refresh failed
    with refresh_lock() as acquired:
        assert acquired is True
    # cached data is untouched — the manifest still exists
    assert stale_manifest.is_file()


def test_refresh_exception_does_not_leak_and_releases_lock(stale_manifest, monkeypatch):
    from app import live_scheduler, on_demand_refresh as odr
    from app.refresh_lock import refresh_lock

    def explode():
        raise RuntimeError("boom: /secret/path")

    monkeypatch.setattr(live_scheduler, "run_refresh_once", explode)

    result = odr.refresh_if_stale(wait=True)

    assert result["status"] == "refresh_failed"
    assert "boom" not in json.dumps(result)
    assert "/secret/path" not in json.dumps(result)
    with refresh_lock() as acquired:
        assert acquired is True


def test_endpoint_returns_safe_failure_on_exception(monkeypatch):
    import app.on_demand_refresh as odr
    from app import api_v1

    def explode(**kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(odr, "refresh_if_stale", explode)

    payload = api_v1.refresh_if_stale_endpoint(wait=False)

    assert payload["status"] == "refresh_failed"
    assert payload["refreshed"] is False
    assert "boom" not in json.dumps(payload)


# --------------------------------------------------------------------------
# endpoint registration & non-blocking default
# --------------------------------------------------------------------------

def test_refresh_route_is_registered_as_post():
    from app.api_v1 import router

    route = next(
        (r for r in router.routes if getattr(r, "path", "") == "/api/v1/system/refresh-if-stale"),
        None,
    )
    assert route is not None
    assert "POST" in route.methods


def test_non_blocking_call_returns_quickly(stale_manifest, monkeypatch):
    from app import live_scheduler, on_demand_refresh as odr

    release = threading.Event()

    def slow_refresh():
        release.wait(timeout=10)
        return {"status": "OK"}

    monkeypatch.setattr(live_scheduler, "run_refresh_once", slow_refresh)

    result = odr.refresh_if_stale(wait=False)

    assert result["status"] in {"refresh_started", "refresh_in_progress"}
    assert result["refreshed"] is False
    release.set()


# --------------------------------------------------------------------------
# 8 & 9. health and CORS
# --------------------------------------------------------------------------

def test_health_is_lightweight_and_does_not_refresh(monkeypatch):
    from app import live_scheduler
    from app.main import health

    monkeypatch.setattr(
        live_scheduler,
        "run_refresh_once",
        lambda *a, **k: pytest.fail("health must never trigger a refresh"),
    )

    payload = health()

    assert payload["status"] == "ok"
    assert payload["service"] == "synoptiq-api"


def test_health_tolerates_a_broken_database(monkeypatch):
    from app import main as main_module

    class Boom:
        def __init__(self, *a, **k):
            raise RuntimeError("database down")

    monkeypatch.setattr(main_module, "SessionLocal", Boom)

    payload = main_module.health()

    assert payload["status"] == "ok"
    assert payload["database"] == "unavailable"


def test_cors_is_configurable_and_not_wildcard():
    from starlette.middleware.cors import CORSMiddleware

    from app.main import app

    entries = [m for m in app.user_middleware if getattr(m, "cls", None) is CORSMiddleware]
    assert entries, "CORS middleware must be installed"
    origins = entries[0].kwargs.get("allow_origins") if entries[0].kwargs else None
    assert origins, "CORS origins must be configured"
    assert "*" not in origins, "wildcard CORS is not allowed in production"


# --------------------------------------------------------------------------
# deployment blueprint
# --------------------------------------------------------------------------

def test_cors_origin_trailing_slash_is_normalised():
    """A configured origin with a trailing slash must still match the browser.

    Browsers send `Origin: https://app.vercel.app` with no trailing slash and
    CORSMiddleware matches exactly, so an unnormalised value silently blocks
    every cross-origin response (200s in the server logs, empty pages in the UI).
    """
    from app.main import _normalise_origin

    assert _normalise_origin("https://app.vercel.app/") == "https://app.vercel.app"
    assert _normalise_origin("  https://app.vercel.app//  ") == "https://app.vercel.app"
    assert _normalise_origin("http://localhost:5173") == "http://localhost:5173"


def test_configured_cors_origins_are_normalised():
    from starlette.middleware.cors import CORSMiddleware

    from app.main import app

    entries = [m for m in app.user_middleware if getattr(m, "cls", None) is CORSMiddleware]
    origins = entries[0].kwargs["allow_origins"]
    assert origins
    assert all(not o.endswith("/") for o in origins), origins
    assert all(o == o.strip() for o in origins), origins


def test_head_requests_are_supported():
    """Render probes HEAD / on startup; FastAPI does not auto-add HEAD to GET."""
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        assert client.head("/").status_code == 200
        assert client.head("/health").status_code == 200
        assert client.get("/health").status_code == 200


def test_render_blueprint_has_no_cron_and_carries_refresh_policy():
    text = (PROJECT_ROOT / "render.yaml").read_text(encoding="utf-8")

    assert "type: cron" not in text, "the 15-minute Render cron must be removed"
    assert "synoptiq-live-refresh" not in text
    assert "SYNOPTIQ_AUTO_REFRESH" in text
    assert "SYNOPTIQ_REFRESH_ON_REQUEST" in text
    assert "SYNOPTIQ_REFRESH_MIN_AGE_MINUTES" in text
    assert "fromDatabase" in text, "DATABASE_URL must come from the Render database"
    assert "name: synoptiq-postgres" in text


def test_python_version_is_pinned():
    assert (PROJECT_ROOT / ".python-version").read_text(encoding="utf-8").strip() == "3.12"


# --------------------------------------------------------------------------
# 10. history seeding is idempotent and skips the archive when already seeded
# --------------------------------------------------------------------------

def _load_seed_module():
    path = PROJECT_ROOT / "training" / "realdata" / "seed_corrected_history.py"
    spec = importlib.util.spec_from_file_location("seed_corrected_history", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_history_seed_skips_archive_when_already_seeded(monkeypatch):
    module = _load_seed_module()
    monkeypatch.setattr(module, "RUNTIME_MODE", "real")
    monkeypatch.setattr(module, "DATABASE_ROLE", "PRODUCTION")
    monkeypatch.setattr(module, "ACTIVE_MODEL_VERSION", "test-version")
    monkeypatch.setattr(module, "_already_seeded", lambda: True)

    def must_not_run(*args, **kwargs):
        raise AssertionError("the seed archive must not be touched when already seeded")

    monkeypatch.setattr(module, "_extract_source_database", must_not_run)

    result = module.seed_history(force=False)

    assert result == {"status": "already_seeded", "model_version": "test-version"}


def test_history_seed_fast_path_reports_ledger_state():
    """_already_seeded reads only the ledger and never raises."""
    module = _load_seed_module()
    assert module._already_seeded() in {True, False}
