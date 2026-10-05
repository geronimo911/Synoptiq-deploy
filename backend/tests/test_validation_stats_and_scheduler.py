"""Tests for the production-database validation statistics and the scheduler.

The point of these tests is that the conformal band and the EVT tail in the
API are *derived*, not hard-coded: they must be recomputed from the database
rows the system actually has, and the code must say UNAVAILABLE rather than
invent numbers when those rows are absent.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))


@pytest.fixture()
def seeded_db(tmp_path, monkeypatch):
    """A tiny production-like database: 3 models x 40 validation contexts."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models_db import Base, ForecastRow, GroundTruthRow

    url = f"sqlite:///{tmp_path/'prod.sqlite3'}"
    engine = create_engine(url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    base = datetime(2025, 11, 1)
    rng_values = {"GFS": 12.0, "IFS": 12.4, "AIFS": 12.2}
    for day in range(40):
        valid = base + timedelta(days=day)
        observed = 12.0 + (day % 5) * 0.5
        # deterministic but well-spread errors so the tail fit has real data
        wobble = ((day * 37) % 11) / 10.0
        for model, value in rng_values.items():
            db.add(ForecastRow(
                model=model, model_generation="real_12m", region="kerala_western_ghats",
                run_time=valid - timedelta(days=1), valid_time=valid, lead_hours=24,
                variable="temperature", lat=10.2, lon=76.4,
                forecast_value=value + wobble + (day % 3) * 0.1, season="post_monsoon",
                regime="normal", regime_probs={"normal": 1.0}, split="val",
                historical_skill_expanding=0.6, climatological_anomaly_norm=0.1,
            ))
        db.add(GroundTruthRow(region="kerala_western_ghats", valid_time=valid,
                              variable="temperature", lat=10.2, lon=76.4,
                              observed_value=observed, source="NASA_POWER_MERRA2"))
    db.commit()
    yield db
    db.close()


def test_validation_stats_computed_from_db(seeded_db, tmp_path, monkeypatch):
    from app import validation_stats as vs

    monkeypatch.setattr(vs, "STATS_DIR", tmp_path / "stats")
    stats = vs.compute_from_db(seeded_db, "temperature", "test-version")
    assert stats["status"] == "OK"
    assert stats["n_validation_contexts"] == 40
    assert stats["conformal"]["q90"] > 0
    assert "production database" in stats["source"]
    # the residual tail is fitted on |blend - observed| exceedances
    assert stats["residual_tail"]["n"] >= 1
    # cached to disk on the second call
    again = vs.compute_from_db(seeded_db, "temperature", "test-version")
    assert again["cache"] == "disk"
    assert again["conformal"]["q90"] == stats["conformal"]["q90"]


def test_validation_stats_unavailable_without_rows(seeded_db, tmp_path, monkeypatch):
    from app import validation_stats as vs
    monkeypatch.setattr(vs, "STATS_DIR", tmp_path / "stats")
    stats = vs.compute_from_db(seeded_db, "wind_speed", "test-version")
    assert stats["status"] == "UNAVAILABLE"
    assert "no validation-split rows" in stats["reason"]


def test_exceedance_probability_reports_context(seeded_db, tmp_path, monkeypatch):
    from app import validation_stats as vs
    monkeypatch.setattr(vs, "STATS_DIR", tmp_path / "stats")
    stats = vs.compute_from_db(seeded_db, "temperature", "test-version")
    if stats["observed_tail"].get("xi") is None:
        pytest.skip("observed tail not estimable on this fixture")
    out = vs.exceedance_probability(stats, value=stats["observed_tail"]["threshold_u95"] + 1.0)
    assert out["status"] == "OK"
    assert 0.0 <= out["exceedance_probability_given_above_threshold"] <= 1.0


def test_confidence_block_prefers_database(monkeypatch, seeded_db, tmp_path):
    """_confidence_block must label the database as the source when it can."""
    from app import validation_stats as vs
    from app import api_v1

    monkeypatch.setattr(vs, "STATS_DIR", tmp_path / "stats")
    vs._cached.cache_clear()
    fake = {
        "status": "OK", "variable": "temperature", "n_validation_contexts": 40,
        "source": "production database", "conformal": {"q90": 2.0, "nominal_coverage": 0.9},
        "observed_tail": {"xi": 0.1, "sigma": 1.0, "threshold_u95": 13.0, "n": 20},
        "residual_tail": {"xi": 0.0, "sigma": 1.0, "threshold_u95": 1.0, "n": 20},
    }
    monkeypatch.setattr("app.validation_stats.validation_stats", lambda *a, **k: fake)
    block = api_v1._confidence_block("temperature", 20.0)
    assert block["source"] == "production_database"
    assert block["interval_90"]["low"] == 18.0 and block["interval_90"]["high"] == 22.0
    assert block["interval_90"]["method"].endswith("production_db_validation_residuals")


def test_weight_policy_applies_measured_optimum(monkeypatch):
    """The measured optimum (lambda 0 on this record) must be what is applied."""
    from app import api_v1
    from app import research as research_mod

    fake_router = {
        "profiles": [{"regime": "depression", "mean_weights": {"GFS": 0.1, "IFS": 0.6, "AIFS": 0.3}}],
        "weight_policy_measurement": {
            "recommended_lambda": 0.0,
            "rows": [{"variable": "temperature",
                      "mae_by_lambda": {"0.00": 0.99, "0.25": 1.11, "1.00": 1.48},
                      "meta_model_lambda_0": 0.99, "router_only_lambda_1": 1.48}],
        },
    }
    monkeypatch.setattr(api_v1, "regime_router", lambda: fake_router)
    monkeypatch.delenv("SYNOPTIQ_REGIME_WEIGHT_LAMBDA", raising=False)
    policy = api_v1._weight_policy("temperature", "depression",
                                   {"GFS": 0.3, "IFS": 0.4, "AIFS": 0.3})
    assert policy["applied_lambda"] == 0.0
    assert policy["source"] == "meta_model"
    assert policy["router_regime_weights"] == {"GFS": 0.1, "IFS": 0.6, "AIFS": 0.3}
    assert policy["measured_optimum_lambda"] == 0.0
    # an explicit override is honoured (for experiments)
    monkeypatch.setenv("SYNOPTIQ_REGIME_WEIGHT_LAMBDA", "0.25")
    overridden = api_v1._weight_policy("temperature", "depression",
                                       {"GFS": 0.3, "IFS": 0.4, "AIFS": 0.3})
    assert overridden["applied_lambda"] == 0.25
    assert overridden["source"] == "router_blend"


# ---------------------------------------------------------------------------
# Live-refresh scheduler
# ---------------------------------------------------------------------------

def test_scheduler_disabled_by_env(monkeypatch):
    from app import live_scheduler
    monkeypatch.setenv("SYNOPTIQ_AUTO_REFRESH", "0")
    assert live_scheduler.auto_refresh_enabled() is False
    assert live_scheduler.start() is False  # no thread when disabled


def test_scheduler_state_and_manifest_age(tmp_path, monkeypatch):
    from app import live_scheduler
    monkeypatch.setattr(live_scheduler, "LIVE_DIR", tmp_path)
    monkeypatch.setattr(live_scheduler, "STATE_PATH", tmp_path / "scheduler_state.json")
    monkeypatch.setattr(live_scheduler, "MANIFEST", tmp_path / "live_manifest.json")
    monkeypatch.setenv("SYNOPTIQ_AUTO_REFRESH", "1")
    monkeypatch.setenv("SYNOPTIQ_LIVE_REFRESH_MINUTES", "20")
    # no manifest yet -> age is None -> a refresh is due
    assert live_scheduler.manifest_age_minutes() is None
    state = live_scheduler._write_state(last_success="2026-01-01T00:00:00+00:00", runs=3)
    assert state["runs"] == 3 and state["interval_minutes"] == 20
    # a fresh manifest reports a small age; an old one reports a large age
    now = datetime.now(timezone.utc)
    (tmp_path / "live_manifest.json").write_text(
        json.dumps({"last_refresh": (now - timedelta(minutes=5)).isoformat()}))
    age = live_scheduler.manifest_age_minutes()
    assert 4.0 <= age <= 6.0
    (tmp_path / "live_manifest.json").write_text(
        json.dumps({"last_refresh": (now - timedelta(minutes=90)).isoformat()}))
    assert live_scheduler.manifest_age_minutes() > 60


def test_scheduler_single_flight_skips_when_locked(tmp_path, monkeypatch):
    from app import live_scheduler
    monkeypatch.setattr(live_scheduler, "LIVE_DIR", tmp_path)
    monkeypatch.setattr(live_scheduler, "STATE_PATH", tmp_path / "scheduler_state.json")
    monkeypatch.setattr(live_scheduler, "LOCK_PATH", tmp_path / "refresh.lock")
    (tmp_path / "refresh.lock").write_text("1234")  # fresh lock from another process
    out = live_scheduler.run_refresh_once(timeout=1)
    assert out["status"] == "SKIPPED"
    assert "in progress" in out["reason"]


def test_scheduler_records_failure_without_raising(tmp_path, monkeypatch):
    """A worker that cannot run must be recorded, never crash the API."""
    from app import live_scheduler
    monkeypatch.setattr(live_scheduler, "LIVE_DIR", tmp_path)
    monkeypatch.setattr(live_scheduler, "STATE_PATH", tmp_path / "scheduler_state.json")
    monkeypatch.setattr(live_scheduler, "LOCK_PATH", tmp_path / "refresh.lock")
    monkeypatch.setattr(live_scheduler, "WORKER", tmp_path / "does_not_exist.py")
    out = live_scheduler.run_refresh_once(timeout=20)
    assert out["status"] in {"FAILED", "ERROR"}
    state = live_scheduler.read_state()
    assert state["last_status"] in {"FAILED", "ERROR"}
    assert state["last_error"]
