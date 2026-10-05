"""Tests for live-cycle freshness and the GFS fallback cycle walk.

Two production defects motivated these:

1. Freshness used a fixed 45-minute INGESTION age. In a demand-driven (no-cron)
   deployment that age just measures "time since the last visit", so the UI
   declared itself DEGRADED on any quiet afternoon while the forecast cycle it
   was serving was still perfectly current. Freshness is now derived from each
   provider's real cycle cadence.

2. The GFS fallback tried only the newest 6-hourly cycle, so whenever that cycle
   was not yet published the provider failed outright, while IFS recovered via
   its own multi-cycle fallback. GFS now walks newest-first like ECMWF.
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))
sys.path.insert(0, str(PROJECT_ROOT / "training" / "realdata"))

# live_refresh creates its log/live dir at import time; point it somewhere safe.
os.environ.setdefault(
    "SYNOPTIQ_LIVE_DATA_DIR", tempfile.mkdtemp(prefix="synoptiq-live-test-")
)


# --------------------------------------------------------------------------
# 1. cycle-aware freshness
# --------------------------------------------------------------------------

def test_freshness_windows_follow_provider_cadence():
    from app.api_v1 import (MODEL_CYCLE_HOURS, MODEL_PUBLISH_GRACE_HOURS,
                            _live_freshness_window_minutes)

    for model, cycle_hours in MODEL_CYCLE_HOURS.items():
        expected = (cycle_hours + MODEL_PUBLISH_GRACE_HOURS[model]) * 60
        assert _live_freshness_window_minutes(model) == expected

    # GFS 6+4, IFS 6+8, AIFS 12+9
    assert _live_freshness_window_minutes("GFS") == 600
    assert _live_freshness_window_minutes("IFS") == 840
    assert _live_freshness_window_minutes("AIFS") == 1260


def test_unknown_model_falls_back_to_configured_window():
    from app.api_v1 import _live_freshness_window_minutes
    from app.config import LIVE_FRESHNESS_MINUTES

    assert _live_freshness_window_minutes("SOMETHING_NEW") == LIVE_FRESHNESS_MINUTES


def test_current_cycle_is_fresh_beyond_the_old_45_minute_gate():
    """The regression: 87 minutes old was reported STALE even though current."""
    from app.api_v1 import live_cycle_is_fresh

    assert live_cycle_is_fresh("GFS", "LIVE", True, 87) is True
    assert live_cycle_is_fresh("IFS", "LIVE", True, 576) is True
    assert live_cycle_is_fresh("AIFS", "LIVE", True, 600) is True


def test_superseded_cycle_is_not_fresh():
    from app.api_v1 import live_cycle_is_fresh

    # older than the model's own cycle window -> the cycle has been replaced
    assert live_cycle_is_fresh("GFS", "LIVE", True, 601) is False
    assert live_cycle_is_fresh("AIFS", "LIVE", True, 1261) is False


def test_freshness_requires_a_live_status_and_a_current_source():
    from app.api_v1 import live_cycle_is_fresh

    assert live_cycle_is_fresh("GFS", "STALE", True, 10) is False
    assert live_cycle_is_fresh("GFS", "UNAVAILABLE", True, 10) is False
    assert live_cycle_is_fresh("GFS", "LIVE", False, 10) is False
    assert live_cycle_is_fresh("GFS", "LIVE", True, None) is False


# --------------------------------------------------------------------------
# 2. GFS fallback walks back through cycles
# --------------------------------------------------------------------------

def test_gfs_fallback_walks_back_through_cycles(monkeypatch):
    import fetch_gfs
    import live_refresh

    tried: list[int] = []

    def fake_fetch_cycle(date_str, hour, steps):
        tried.append(hour)
        # newest cycle not published yet; an older one is available
        if len(tried) == 1:
            return []
        return [{"run_date": date_str, "run_hour": hour, "zone": "kerala_western_ghats",
                 "step": 24, "t2m_c": 28.0, "wind_ms": 3.0, "tp_cum_m": 0.01,
                 "model_generation": "GFS"}]

    monkeypatch.setattr(fetch_gfs, "fetch_cycle", fake_fetch_cycle)
    monkeypatch.setattr(
        live_refresh, "_aggregate_archive_rows",
        lambda model, raw, now, provider, transport, forecast_reference_time=None: raw,
    )
    monkeypatch.setattr(live_refresh, "validate_rows", lambda *_args: {})

    now = datetime(2026, 10, 5, 15, 37, tzinfo=timezone.utc)
    rows = live_refresh._fallback_rows("GFS", now)

    assert rows, "fallback must recover from an older cycle"
    assert len(tried) >= 2, f"must try more than one cycle, tried hours={tried}"
    assert tried[0] == 12 and tried[1] == 6, f"must walk newest-first, got {tried}"


def test_gfs_fallback_skips_incomplete_cycle_before_accepting(monkeypatch):
    import fetch_gfs
    import live_refresh

    now = datetime(2026, 10, 5, 15, 37, tzinfo=timezone.utc)
    tried: list[int] = []

    def fake_fetch_cycle(date_str, hour, steps):
        tried.append(hour)
        return [{"run_date": date_str, "run_hour": hour}]

    def forecast_rows(
        _model, _raw, retrieved_at, _provider, _transport, forecast_reference_time=None
    ):
        reference_time = forecast_reference_time or retrieved_at
        rows = []
        for zone in live_refresh.ZONES:
            for variable in ("temperature", "precipitation", "wind_speed"):
                for lead in live_refresh.LEADS:
                    if len(tried) == 1 and zone == "bay_of_bengal_east_coast" and lead == 48:
                        continue
                    rows.append({
                        "model": "GFS",
                        "model_generation": "live:GFS",
                        "region": zone,
                        "variable": variable,
                        "lead_hours": lead,
                        "forecast_value": 1.0,
                        "valid_time": (reference_time.replace(tzinfo=None)
                                       + live_refresh.timedelta(hours=lead)),
                    })
        return rows

    monkeypatch.setattr(fetch_gfs, "fetch_cycle", fake_fetch_cycle)
    monkeypatch.setattr(live_refresh, "_aggregate_archive_rows", forecast_rows)

    rows = live_refresh._fallback_rows("GFS", now)

    assert len(rows) == len(live_refresh.ZONES) * 3 * len(live_refresh.LEADS)
    assert tried[:2] == [12, 6]


def test_gfs_fallback_raises_when_no_cycle_is_usable(monkeypatch):
    import fetch_gfs
    import live_refresh

    monkeypatch.setattr(fetch_gfs, "fetch_cycle", lambda *a, **k: [])

    now = datetime(2026, 10, 5, 15, 37, tzinfo=timezone.utc)
    with pytest.raises(RuntimeError, match="GFS"):
        live_refresh._fallback_rows("GFS", now)


def test_gfs_provider_records_unavailable_reason_instead_of_raising(monkeypatch):
    import live_refresh

    now = datetime(2026, 10, 5, 15, 37, tzinfo=timezone.utc)

    def no_primary(_model, _now, _reference_time=None):
        raise RuntimeError("primary unavailable")

    def no_fallback(_model, _now, _reference_time=None):
        raise RuntimeError("no valid NOAA cycle: fallback missing bay_of_bengal_east_coast lead 48")

    monkeypatch.setattr(live_refresh, "_live_rows", no_primary)
    monkeypatch.setattr(live_refresh, "_fallback_rows", no_fallback)
    monkeypatch.setattr(live_refresh.time, "sleep", lambda _seconds: None)

    rows, state = live_refresh.fetch_provider("GFS", now)

    assert rows == []
    assert state["status"] == "UNAVAILABLE"
    assert "bay_of_bengal_east_coast lead 48" in state["reason"]


def test_provider_validation_rejects_rows_that_cannot_join_the_shared_lead():
    import live_refresh

    now = datetime(2026, 10, 5, 15, 37, tzinfo=timezone.utc)
    rows = [
        {
            "model": "GFS",
            "region": zone,
            "variable": variable,
            "lead_hours": lead,
            "forecast_value": 1.0,
            "valid_time": (now.replace(tzinfo=None) + live_refresh.timedelta(hours=lead)),
        }
        for zone in live_refresh.ZONES
        for variable in ("temperature", "precipitation", "wind_speed")
        for lead in live_refresh.LEADS
    ]

    coverage = live_refresh.validate_rows("GFS", rows, now)
    assert coverage["aligned"] is True

    rows[0]["valid_time"] -= live_refresh.timedelta(hours=6)
    with pytest.raises(RuntimeError, match="aligned=False"):
        live_refresh.validate_rows("GFS", rows, now)


def test_shared_forecast_reference_uses_the_live_provider_utc_day():
    import live_refresh

    earlier = datetime(2026, 10, 5, 12, 5, tzinfo=timezone.utc)
    later = datetime(2026, 10, 5, 17, 55, tzinfo=timezone.utc)

    assert live_refresh._forecast_reference_time(earlier) == datetime(
        2026, 10, 5, 0, tzinfo=timezone.utc
    )
    assert live_refresh._forecast_reference_time(later) == datetime(
        2026, 10, 5, 0, tzinfo=timezone.utc
    )


@pytest.mark.parametrize(
    ("model", "precipitation_field", "expected_precipitation"),
    [
        ("GFS", "apcp6_mm", 4.0),
        ("IFS", "tp_cum_m", 24.0),
    ],
)
def test_fallback_provider_rows_align_to_common_target_valid_time(
    monkeypatch, model, precipitation_field, expected_precipitation
):
    import fetch_openmeteo
    import live_refresh

    monkeypatch.setattr(
        fetch_openmeteo,
        "REPRESENTATIVE_POINTS",
        {"kerala_western_ghats": [(10.0, 76.0)]},
    )
    retrieved_at = datetime(2026, 10, 5, 15, tzinfo=timezone.utc)
    raw_rows = []
    for step in range(6, 181, 6):
        row = {
            "run_date": "2026-10-05",
            "run_hour": 12,
            "zone": "kerala_western_ghats",
            "step": step,
            "t2m_c": float(step),
            "wind_ms": float(step),
        }
        row[precipitation_field] = 1.0 if model == "GFS" else step * 0.001
        raw_rows.append(row)

    rows = live_refresh._aggregate_archive_rows(
        model, raw_rows, retrieved_at, "provider", "transport"
    )
    target = retrieved_at.replace(tzinfo=None) + live_refresh.timedelta(hours=24)
    lead_rows = [row for row in rows if row["lead_hours"] == 24]

    assert lead_rows
    assert {row["valid_time"] for row in lead_rows} == {target}
    assert {row["run_time"] for row in lead_rows} == {datetime(2026, 10, 5, 12)}
    assert next(row["forecast_value"] for row in lead_rows if row["variable"] == "temperature") == pytest.approx(36.0)
    assert next(row["forecast_value"] for row in lead_rows if row["variable"] == "wind_speed") == pytest.approx(162.0)
    assert next(row["forecast_value"] for row in lead_rows if row["variable"] == "precipitation") == pytest.approx(expected_precipitation)


# --------------------------------------------------------------------------
# 3. deterministic live-ingestion read
# --------------------------------------------------------------------------

def test_status_is_live_when_the_cycle_is_current_but_the_visit_is_old(tmp_path):
    """The exact production symptom: 87 minutes since the last visit.

    Before the fix this reported REAL INPUTS UNAVAILABLE / DEGRADED, because the
    ingestion age (87) exceeded a fixed 45-minute window even though the cycle
    being served was still the current one.
    """
    from datetime import timedelta

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app import api_v1
    from app.models_db import Base, ForecastRow, LiveForecast
    import live_refresh

    engine = create_engine(f"sqlite:///{tmp_path/'status.sqlite3'}")
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine)()

    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    ingested = now - timedelta(minutes=87)
    cycle = now - timedelta(hours=5)  # still the current 6-hourly cycle
    states = {
        model: {
            "status": "LIVE", "complete": True, "finite": True,
            "source": "Open-Meteo", "retrieved_at": ingested.isoformat(),
            "source_run_time": cycle.isoformat(), "run_time_basis": "model_run_time",
            "coverage": {}, "fallback_used": False,
            "reason": "complete fresh real source cycle",
        }
        for model in ("GFS", "IFS", "AIFS")
    }
    forecast_reference_time = live_refresh._forecast_reference_time(ingested)
    for model in ("GFS", "IFS", "AIFS"):
        for zone in live_refresh.ZONES:
            for variable in ("temperature", "precipitation", "wind_speed"):
                for lead in live_refresh.LEADS:
                    db.add(ForecastRow(
                        model=model,
                        model_generation=f"live:{model}",
                        region=zone,
                        run_time=cycle.replace(tzinfo=None),
                        valid_time=(forecast_reference_time + timedelta(hours=lead)).replace(tzinfo=None),
                        lead_hours=lead,
                        variable=variable,
                        lat=0.0,
                        lon=0.0,
                        forecast_value=1.0,
                        season="post_monsoon",
                        regime="normal",
                        regime_probs=None,
                    ))
    db.add(LiveForecast(
        region="__cycle__", variable="__cycle__", valid_time=ingested, lead_hours=0,
        run_time=ingested, ingestion_time=ingested, model_version="test",
        source_runs=states, payload={"providers": states},
    ))
    db.commit()

    payload = api_v1.system_status(db)
    db.close()

    assert payload["live_state"] == "REAL LIVE", payload["providers"]
    assert all(p["fresh"] for p in payload["providers"].values())
    assert all(p["coverage"]["aligned"] for p in payload["providers"].values())

    for row in db.query(ForecastRow).filter(ForecastRow.model == "IFS").all():
        row.valid_time += timedelta(hours=6)
    db.commit()
    degraded = api_v1.system_status(db)
    db.close()

    assert degraded["live_state"] == "DEGRADED REAL"
    assert degraded["providers"]["IFS"]["status"] == "INVALID"
    assert degraded["providers"]["IFS"]["coverage"]["aligned"] is False
    assert "shared live valid-time ladder" in degraded["providers"]["IFS"]["reason"]


def test_status_goes_stale_once_the_cycle_is_superseded(tmp_path):
    """The safety net still works: a superseded cycle must not read as live."""
    from datetime import timedelta

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app import api_v1
    from app.models_db import Base, LiveForecast

    engine = create_engine(f"sqlite:///{tmp_path/'status2.sqlite3'}")
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine)()

    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    ingested = now - timedelta(minutes=2000)  # way past every cycle window
    states = {
        model: {
            "status": "LIVE", "complete": True, "finite": True,
            "source": "Open-Meteo", "retrieved_at": ingested.isoformat(),
            "source_run_time": ingested.isoformat(), "run_time_basis": "model_run_time",
            "coverage": {}, "fallback_used": False, "reason": "stale cycle",
        }
        for model in ("GFS", "IFS", "AIFS")
    }
    db.add(LiveForecast(
        region="__cycle__", variable="__cycle__", valid_time=ingested, lead_hours=0,
        run_time=ingested, ingestion_time=ingested, model_version="test",
        source_runs=states, payload={"providers": states},
    ))
    db.commit()

    payload = api_v1.system_status(db)
    db.close()

    assert payload["live_state"] != "REAL LIVE"
    assert not any(p["fresh"] for p in payload["providers"].values())


def test_latest_ingestion_prefers_the_complete_row(tmp_path):
    """The complete state is written last (higher id) and must win."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.api_v1 import _latest_ingestion
    from app.models_db import Base, LiveForecast

    engine = create_engine(f"sqlite:///{tmp_path/'ordering.sqlite3'}")
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine)()

    stamp = datetime(2026, 10, 5, 15, 29)  # identical ingestion_time for both rows

    def add(payload):
        db.add(LiveForecast(
            region="__cycle__", variable="__cycle__", valid_time=stamp, lead_hours=0,
            run_time=stamp, ingestion_time=stamp, model_version="test",
            source_runs={}, payload=payload,
        ))
        db.commit()

    add({"providers": {"AIFS": {"status": "UNAVAILABLE",
                                "reason": "AIFS provider refresh is in progress"}}})
    add({"providers": {"AIFS": {"status": "LIVE", "reason": "complete future real forecast cycle"}}})

    row = _latest_ingestion(db)
    assert row is not None
    assert row.payload["providers"]["AIFS"]["status"] == "LIVE"
    db.close()


def test_primary_provider_is_attempted_once_before_real_fallback(monkeypatch):
    import live_refresh

    calls = []

    def unavailable_primary(model, retrieved_at, forecast_reference_time=None):
        calls.append(model)
        raise TimeoutError("Open-Meteo request timed out")

    monkeypatch.setattr(live_refresh, "_live_rows", unavailable_primary)
    monkeypatch.setattr(
        live_refresh, "_fallback_rows",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("no complete archive cycle")),
    )
    monkeypatch.setattr(live_refresh.time, "sleep", lambda _seconds: None)

    rows, state = live_refresh.fetch_provider(
        "IFS", datetime(2026, 10, 5, 18, 0, tzinfo=timezone.utc)
    )

    assert rows == []
    assert calls == ["IFS"]
    assert state["status"] == "UNAVAILABLE"
    assert state["reason"] == "RuntimeError: no complete archive cycle"
