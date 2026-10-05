import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "realdata"))

import live_refresh
import fetch_ecmwf


def test_provider_fallback_is_used_after_primary_failure(monkeypatch):
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    calls = []
    monkeypatch.setattr(live_refresh.time, "sleep", lambda _: None)

    def primary(model, retrieved_at, forecast_reference_time=None):
        calls.append("primary")
        raise RuntimeError("primary unavailable")

    def fallback(model, retrieved_at, forecast_reference_time=None):
        calls.append("fallback")
        rows = []
        reference_time = forecast_reference_time or retrieved_at
        for zone in live_refresh.ZONES:
            for lead in live_refresh.LEADS:
                for variable, value in (("temperature", 25.0), ("precipitation", 1.0), ("wind_speed", 12.0)):
                    rows.append({
                        "model": model, "region": zone, "variable": variable,
                        "lead_hours": lead,
                        "valid_time": (reference_time + timedelta(hours=lead)).replace(tzinfo=None),
                        "forecast_value": value,
                        "source_provider": "fallback", "source_model": "fallback", "source_transport": "https",
                    })
        return rows

    monkeypatch.setattr(live_refresh, "_live_rows", primary)
    monkeypatch.setattr(live_refresh, "_fallback_rows", fallback)
    rows, state = live_refresh.fetch_provider("GFS", now)
    assert len(rows) == 45
    assert state["status"] == "LIVE"
    assert state["fallback_used"] is True
    assert calls[-1] == "fallback"
    assert calls[:-1] == ["primary"] * 2


def test_ecmwf_candidate_missing_first_step_is_skipped_without_fetching_rest(
    monkeypatch, tmp_path
):
    import fetch_ecmwf

    calls = []

    class MissingCycleClient:
        def retrieve(self, **kwargs):
            calls.append(kwargs["step"])
            raise RuntimeError("404 not found")

    monkeypatch.setattr(fetch_ecmwf, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(
        fetch_ecmwf, "open_data_client", lambda *args, **kwargs: MissingCycleClient()
    )

    rows = fetch_ecmwf.fetch_cycle(
        "ifs", "2026-10-05", 18, [6, 12, 18], source="aws", maximum_retries=1
    )

    assert rows == []
    assert calls == ["6"]


def test_ecmwf_incomplete_required_lead_stops_cycle_immediately(
    monkeypatch, tmp_path
):
    calls = []

    class PartialCycleClient:
        def retrieve(self, **kwargs):
            calls.append(kwargs["step"])
            if kwargs["step"] == "12":
                raise RuntimeError("404 not found")
            Path(kwargs["target"]).write_bytes(b"GRIB")

    monkeypatch.setattr(fetch_ecmwf, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(
        fetch_ecmwf, "open_data_client", lambda *args, **kwargs: PartialCycleClient()
    )
    monkeypatch.setattr(
        fetch_ecmwf, "validate_grib_fields",
        lambda path: (Path(path).exists(), set()),
    )
    monkeypatch.setattr(
        fetch_ecmwf, "decode_zone_means",
        lambda *_args: {
            zone: {"t2m_c": 20.0, "wind_ms": 3.0, "tp_cum_m": 0.01}
            for zone in live_refresh.ZONES
        },
    )

    rows = fetch_ecmwf.fetch_cycle(
        "ifs", "2026-10-05", 18, [6, 12, 18], source="aws", maximum_retries=1
    )

    assert rows == []
    assert calls == ["6", "12"]


def test_fallback_requests_only_steps_needed_for_shared_forecast_horizon():
    cycle = datetime(2026, 10, 5, 18, tzinfo=timezone.utc)
    reference = datetime(2026, 10, 5, 0, tzinfo=timezone.utc)

    assert live_refresh._required_archive_steps(cycle, reference) == list(
        range(6, 127, 6)
    )


def test_gfs_incomplete_required_lead_stops_cycle_immediately(monkeypatch):
    import fetch_gfs

    calls = []

    def get_file(_date, _hour, step):
        calls.append(step)
        if step == 12:
            raise FileNotFoundError("required forecast lead unavailable")
        return b"GRIB"

    monkeypatch.setattr(fetch_gfs, "fetch_file_fields", get_file)
    monkeypatch.setattr(
        fetch_gfs, "decode_zone_means",
        lambda _blob: {
            zone: {"t2m_c": 20.0, "wind_ms": 3.0, "apcp6_mm": 1.0}
            for zone in live_refresh.ZONES
        },
    )

    rows = fetch_gfs.fetch_cycle("2026-10-05", 18, [6, 12, 18])

    assert rows == []
    assert calls == [6, 12]


def test_ecmwf_client_uses_requested_open_data_mirror(monkeypatch):
    import ecmwf.opendata

    monkeypatch.setattr(ecmwf.opendata, "Client", lambda **kwargs: kwargs)

    client_options = fetch_ecmwf.open_data_client(
        "aifs-single", maximum_retries=1, source="azure"
    )

    assert client_options["source"] == "azure"
    assert client_options["model"] == "aifs-single"


def test_aifs_fallback_uses_latest_published_cycle_and_source_timestamps(monkeypatch):
    now = datetime(2026, 9, 29, 16, 0, tzinfo=timezone.utc)
    requested = []

    def fetch_cycle(model, run_date, hour, steps, source="aws", maximum_retries=4):
        requested.append((run_date, hour, source))
        return [
            {
                "run_date": run_date,
                "run_hour": hour,
                "model_generation": "AIFS Single v2",
                "step": step,
                "zone": zone,
                "t2m_c": 25.0,
                "wind_ms": 3.0,
                "tp_cum_m": step / 1000,
            }
            for zone in live_refresh.ZONES
            for step in steps
        ]

    monkeypatch.setitem(sys.modules, "fetch_ecmwf", SimpleNamespace(fetch_cycle=fetch_cycle))
    rows = live_refresh._fallback_rows("AIFS", now)

    # Newest-cycle selection: at 16:00 UTC on 29 Sep the newest AIFS cycle is
    # 12Z, and that is attempted FIRST (it is often already published); older
    # cycles are only walked back to when the newest is genuinely absent.
    assert requested == [("2026-09-29", 12, "azure")]
    assert len(rows) == 45
    assert rows[0]["run_time"] == datetime(2026, 9, 29, 12)
    assert rows[0]["valid_time"] == now.replace(tzinfo=None) + timedelta(hours=24)
    assert rows[0]["source_run_time"] == "2026-09-29T12:00:00+00:00"
    assert rows[0]["run_time_basis"] == "model_run_time"
    assert rows[0]["model_generation"] == "live:AIFS Single v2"
    assert rows[0]["source_model"] == "AIFS Single"
    assert rows[0]["source_transport"] == "ECMWF Open Data (azure)"
    assert rows[0]["valid_time"] > now.replace(tzinfo=None)


def test_aifs_falls_back_to_google_when_azure_mirror_is_throttled(monkeypatch):
    now = datetime(2026, 9, 29, 16, 0, tzinfo=timezone.utc)
    requested = []

    def fetch_cycle(model, run_date, hour, steps, source="aws", maximum_retries=4):
        requested.append((run_date, hour, source))
        if source == "azure":
            raise RuntimeError("HTTP 503 Slow Down")
        return [
            {
                "run_date": run_date,
                "run_hour": hour,
                "model_generation": "AIFS Single v2",
                "step": step,
                "zone": zone,
                "t2m_c": 25.0,
                "wind_ms": 3.0,
                "tp_cum_m": step / 1000,
            }
            for zone in live_refresh.ZONES
            for step in steps
        ]

    monkeypatch.setitem(sys.modules, "fetch_ecmwf", SimpleNamespace(fetch_cycle=fetch_cycle))
    rows = live_refresh._fallback_rows("AIFS", now)

    # Newest cycle (12Z) attempted first; azure throttles, so google serves
    # that same cycle.
    assert requested == [
        ("2026-09-29", 12, "azure"),
        ("2026-09-29", 12, "google"),
    ]
    assert len(rows) == 45
    assert rows[0]["source_run_time"] == "2026-09-29T12:00:00+00:00"
    assert rows[0]["source_transport"] == "ECMWF Open Data (google)"


def test_refresh_persists_gfs_and_ifs_before_attempting_aifs(monkeypatch):
    persisted = []
    monkeypatch.setattr(live_refresh, "_previous_live_provider", lambda model, now: ([], None))

    def persist_cycle(provider_rows, states, refresh_time):
        persisted.append((set(provider_rows), dict(states)))

    def fetch_provider(model, refresh_time):
        if model == "AIFS":
            assert persisted
            assert persisted[0][0] == {"GFS", "IFS"}
            assert persisted[0][1]["GFS"]["status"] == "LIVE"
            assert persisted[0][1]["IFS"]["status"] == "LIVE"
            return [], {"status": "UNAVAILABLE", "fresh": False, "fallback_used": True}
        return [{"model": model, "valid_time": refresh_time}], {
            "status": "LIVE",
            "fresh": True,
            "fallback_used": False,
            "coverage": {},
            "source": "test",
        }

    monkeypatch.setattr(live_refresh, "persist_cycle", persist_cycle)
    monkeypatch.setattr(live_refresh, "fetch_provider", fetch_provider)

    states = live_refresh.refresh_once()

    assert states["AIFS"]["status"] == "UNAVAILABLE"
    assert len(persisted) == 2
    assert persisted[1][0] == {"GFS", "IFS", "AIFS"}


def test_previous_aifs_cycle_is_found_by_provider_rows_not_ingestion_run_time(monkeypatch):
    import app.config
    import app.database
    from app.models_db import ForecastRow, LiveForecast

    now = datetime(2026, 9, 30, 9, 15, tzinfo=timezone.utc)
    state = {
        "status": "LIVE",
        "source": "ECMWF Open Data",
        "source_transport": "ECMWF Open Data (azure)",
        "source_run_time": "2026-09-30T00:00:00+00:00",
        "retrieved_at": "2026-09-30T09:02:00+00:00",
    }
    latest = SimpleNamespace(
        model_version="test-version",
        ingestion_time=datetime(2026, 9, 30, 9, 2),
        payload={"providers": {"AIFS": state}},
    )
    rows = [
        SimpleNamespace(**{
            "model": "AIFS", "model_generation": "live:AIFS Single v2",
            "region": zone, "run_time": datetime(2026, 9, 30),
            "valid_time": live_refresh._forecast_reference_time(now).replace(tzinfo=None)
            + timedelta(hours=lead),
            "lead_hours": lead, "variable": variable, "lat": 0.0, "lon": 0.0,
            "forecast_value": 1.0, "season": "post_monsoon", "regime": "normal",
            "regime_probs": None,
        })
        for zone in live_refresh.ZONES
        for lead in live_refresh.LEADS
        for variable in ("temperature", "precipitation", "wind_speed")
    ]

    class FakeQuery:
        def order_by(self, *_):
            return self

        def filter(self, *_):
            return self

        def first(self):
            return latest

        def all(self):
            return rows

    class FakeSession:
        def query(self, model):
            assert model in (LiveForecast, ForecastRow)
            return FakeQuery()

        def close(self):
            pass

    monkeypatch.setattr(app.config, "ACTIVE_MODEL_VERSION", "test-version")
    monkeypatch.setattr(app.database, "SessionLocal", FakeSession)

    preserved_rows, preserved_state = live_refresh._previous_live_provider("AIFS", now)

    assert len(preserved_rows) == 45
    assert preserved_state["source_run_time"] == "2026-09-30T00:00:00+00:00"


def test_refresh_retains_last_validated_aifs_when_replacement_fails(monkeypatch):
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    forecast_reference_time = live_refresh._forecast_reference_time(now).replace(tzinfo=None)
    previous_rows = [
        {
            "model": "AIFS", "region": zone, "variable": variable,
            "lead_hours": lead, "run_time": datetime(2026, 9, 30),
            "valid_time": forecast_reference_time + timedelta(hours=lead),
            "forecast_value": 1.0,
        }
        for zone in live_refresh.ZONES
        for lead in live_refresh.LEADS
        for variable in ("temperature", "precipitation", "wind_speed")
    ]
    previous_state = {
        "status": "LIVE", "fresh": True, "complete": True, "finite": True,
        "source": "ECMWF Open Data", "source_model": "AIFS Single",
        "source_transport": "ECMWF Open Data (azure)",
        "source_run_time": (now - timedelta(hours=9)).isoformat(),
        "retrieved_at": (now - timedelta(minutes=10)).isoformat(),
        "run_time_basis": "model_run_time", "coverage": {"rows": 45},
        "fallback_used": True, "reason": "previous fresh source cycle",
    }
    persisted = []

    def persist_cycle(provider_rows, states, refresh_time):
        persisted.append((provider_rows.copy(), states.copy()))

    def fetch_provider(model, refresh_time):
        if model == "AIFS":
            return [], {"status": "UNAVAILABLE", "reason": "mirror timeout", "fresh": False}
        rows = [
            {
                "model": model, "region": zone, "variable": variable,
                "lead_hours": lead,                 "valid_time": live_refresh._forecast_reference_time(refresh_time).replace(tzinfo=None)
                + timedelta(hours=lead),
            }
            for zone in live_refresh.ZONES
            for lead in live_refresh.LEADS
            for variable in ("temperature", "precipitation", "wind_speed")
        ]
        return rows, {
            "status": "LIVE", "fresh": True, "fallback_used": False,
            "coverage": {}, "source": "Open-Meteo",
        }

    monkeypatch.setattr(live_refresh, "_previous_live_provider", lambda *_: (previous_rows, previous_state))
    monkeypatch.setattr(live_refresh, "persist_cycle", persist_cycle)
    monkeypatch.setattr(live_refresh, "fetch_provider", fetch_provider)

    states = live_refresh.refresh_once()

    assert states["AIFS"]["status"] == "LIVE"
    assert "retaining the aligned last validated cycle" in states["AIFS"]["reason"]
    assert len(persisted[0][0]["AIFS"]) == 45
    assert len(persisted[1][0]["AIFS"]) == 45
    assert persisted[0][0]["AIFS"][0]["valid_time"] == forecast_reference_time + timedelta(hours=24)
    assert persisted[0][0]["AIFS"][0]["run_time"] == datetime(2026, 9, 30)
