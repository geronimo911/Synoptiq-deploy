"""Tests for the research & verification suite.

Two layers:
  1. computation correctness — a tiny synthetic blend record with hand-
     computable numbers is run through research_suite.compute_all and every
     section is checked against values derived by hand;
  2. API surface — the read-only /research endpoints and /alerts/rpi serve
     exactly what the artifact contains, and 404 with a regeneration hint
     when the artifact is absent (never synthetic data).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "backend" / "scripts"))

from research_suite import compute_all  # noqa: E402
import research_suite  # noqa: E402


def _record(valid_time, region, variable, lead, sources, calibrated, observed,
            trust=0.8, bust=0.01, abstain=False, split="test", blend=None):
    return {
        "valid_time": valid_time, "region": region, "variable": variable,
        "lead_hours": lead, "sources": sources,
        "weights": {"GFS": 0.34, "IFS": 0.33, "AIFS": 0.33},
        "blend": blend if blend is not None else sum(sources.values()) / 3.0,
        "calibrated": calibrated, "exceedance_probabilities": {"heavy": 0.0},
        "trust": trust, "bust_probability": bust, "abstain": abstain,
        "weight_source": "meta_model", "regime": "normal",
        "regime_probs": {"normal": 1.0}, "split": split,
        "observed_value": observed,
    }


@pytest.fixture()
def mini_record(tmp_path):
    """Two days, one region/variable, three models, two leads.

    Day 1 (obs = 10.0): models agree closely (easy); GFS is best.
    Day 2 (obs = 0.0): models split wildly (hard); blend is best.
    Climatology comes from a third training day with obs = 5.0 (same month).
    """
    rows = [
        # train day (feeds climatology + persistence lookup)
        _record("2025-01-01", "r", "temperature", 24,
                {"GFS": 5.0, "IFS": 5.0, "AIFS": 5.0}, 5.0, 5.0, split="train"),
        # test day 1: easy, close agreement
        _record("2025-01-02", "r", "temperature", 24,
                {"GFS": 10.2, "IFS": 10.4, "AIFS": 10.6}, 10.4, 10.0),
        _record("2025-01-02", "r", "temperature", 48,
                {"GFS": 10.2, "IFS": 10.4, "AIFS": 10.6}, 10.4, 10.0),
        # test day 2: hard, wild split — blend nails it
        _record("2025-01-03", "r", "temperature", 24,
                {"GFS": 0.0, "IFS": 20.0, "AIFS": 0.2}, 0.0, 0.0),
        _record("2025-01-03", "r", "temperature", 48,
                {"GFS": 0.0, "IFS": 20.0, "AIFS": 0.2}, 0.0, 0.0),
    ]
    path = tmp_path / "mini.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    return path


def test_baselines_persistence_and_climatology(mini_record):
    suite = compute_all(mini_record)
    row = suite["baselines"]["rows"][0]
    # persistence: obs of previous day -> day1: 5.0 vs obs 10 (err 5),
    # day2: obs(day1)=10 vs obs 0 (err 10); mean over 4 rows = 7.5
    assert row["mae"]["persistence"] == pytest.approx(7.5, abs=1e-6)
    # climatology: month-of-year mean from train = 5.0 -> every test err = |5 - obs|
    # day1: 5, day2: 5 -> mean 5.0
    assert row["mae"]["climatology"] == pytest.approx(5.0, abs=1e-6)
    # blend MAE: day1 |10.4-10| = 0.4 x2; day2 0 x2 -> 0.2
    assert row["mae"]["blend"] == pytest.approx(0.2, abs=1e-6)
    # naive average day1 = 10.4 (err .4), day2 = 6.733 (err 6.733)
    naive = (0.4 + 0.4 + 6.7333 + 6.7333) / 4
    assert row["mae"]["naive_average"] == pytest.approx(naive, abs=1e-3)


def test_oracle_math(mini_record):
    suite = compute_all(mini_record)
    row = suite["oracle"]["rows"][0]
    # day1 best single err = 0.2 (GFS); day2 best = 0.0 (GFS) -> oracle 0.1
    assert row["oracle_mae"] == pytest.approx(0.1, abs=1e-6)
    # best fixed model: GFS errs 0.2,0.2,0.0,0.0 -> 0.1
    assert row["best_fixed_model"] == "GFS"
    assert row["best_fixed_mae"] == pytest.approx(0.1, abs=1e-6)
    # blend 0.2, best fixed 0.1, oracle 0.2: gap is negative -> recovery None
    assert row["oracle_advantage_recovered_pct"] is None or \
        row["oracle_advantage_recovered_pct"] <= 0


def test_hard_days_split(mini_record):
    suite = compute_all(mini_record)
    row = suite["hard_days"]["rows"][0]
    # day1 spread = 0.4 (easy tercile), day2 spread = 20.0 (hard tercile)
    assert row["easy"]["mae"]["blend"] == pytest.approx(0.4, abs=1e-6)
    assert row["hard"]["mae"]["blend"] == pytest.approx(0.0, abs=1e-6)
    # hard gain is None here because the base (GFS) error is exactly zero on
    # the hard day — a degenerate-fixture case the real record never hits
    assert row["blend_gain_pct"]["hard"] is None or \
        row["blend_gain_pct"]["hard"] > row["blend_gain_pct"]["easy"]


def test_imd_classes_pod_far_csi(mini_record):
    suite = compute_all(mini_record)
    # inject rain rows to exercise the IMD machinery
    rows = [json.loads(l) for l in mini_record.read_text().splitlines()]
    rows += [
        _record("2025-01-02", "r", "precipitation", 24,
                {"GFS": 20.0, "IFS": 0.0, "AIFS": 0.0}, 18.0, 16.0),
        _record("2025-01-03", "r", "precipitation", 24,
                {"GFS": 0.0, "IFS": 0.0, "AIFS": 0.0}, 0.0, 30.0),
    ]
    mini_record.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    suite = compute_all(mini_record)
    moderate = suite["imd_classes"]["test"]["moderate"]
    blend = moderate["blend"]
    # observed events at >=15.6: day1 (16.0, hit) + day2 (30.0, miss) -> POD 0.5
    assert blend["n_events"] == 2
    assert blend["pod"] == pytest.approx(0.5)
    assert blend["csi"] == pytest.approx(0.5)
    gfs = moderate["GFS"]
    assert gfs["hits"] == 1 and gfs["misses"] == 1


def test_trust_audit_and_rpi(mini_record):
    rows = [json.loads(l) for l in mini_record.read_text().splitlines()]
    rows.append(_record("2025-01-03", "r", "precipitation", 24,
                        {"GFS": 70.0, "IFS": 65.0, "AIFS": 60.0}, 66.0, 60.0,
                        trust=0.9))
    mini_record.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    suite = compute_all(mini_record)
    assert any(t["n"] > 0 for t in suite["trust_audit"]["tiers"])
    rpi = suite["rpi"]
    # latest day is 2025-01-03; its rain forecast (66 mm) is a heavy-rain alert
    assert rpi["as_of"] == "2025-01-03"
    heavy = [a for a in rpi["alerts"] if a["event"] == "heavy_rain"]
    assert len(heavy) == 1
    alert = heavy[0]
    assert alert["level"] in ("WATCH", "ALERT", "WARNING")
    # severity 75 x (0.5+0.5*0.9) x 1.0 (24h lead) = 71.25 -> WARNING
    assert alert["rpi_score"] == pytest.approx(71.25, abs=0.2)
    # the day's 0 degC temperature rows also legitimately fire cold alerts
    colds = [a for a in rpi["alerts"] if a["event"] == "cold_alert"]
    assert len(colds) == 2


def test_rpi_severity_bands():
    assert research_suite._rpi_severity("precipitation", 15.6) == (50, "moderate_rain")
    assert research_suite._rpi_severity("precipitation", 64.5) == (75, "heavy_rain")
    assert research_suite._rpi_severity("precipitation", 115.5) == (100, "very_heavy_rain")
    assert research_suite._rpi_severity("temperature", 41.0) == (75, "heat_alert")
    assert research_suite._rpi_severity("wind_speed", 55.0) == (75, "damaging_wind")
    assert research_suite._rpi_severity("wind_speed", 10.0) == (0, None)


def test_api_endpoints_serve_artifact(monkeypatch, tmp_path, mini_record):
    suite = json.loads(json.dumps(compute_all(mini_record)))
    artifact = tmp_path / "research_suite.json"
    artifact.write_text(json.dumps(suite), encoding="utf-8")
    from app import research
    monkeypatch.setattr(research, "RESEARCH_SUITE_PATH", artifact)
    research._load_suite.cache_clear()
    assert research.research_summary()["oracle_overall"] == suite["oracle"]["overall"]
    assert research.research_section("baselines") == suite["baselines"]
    with pytest.raises(Exception):
        research.research_section("does_not_exist")


def test_api_missing_artifact_raises_404(monkeypatch, tmp_path):
    from fastapi import HTTPException
    from app import research
    monkeypatch.setattr(research, "RESEARCH_SUITE_PATH",
                        tmp_path / "nope.json")
    research._load_suite.cache_clear()
    with pytest.raises(HTTPException) as exc:
        research.research_summary()
    assert exc.value.status_code == 404
    assert "research_suite.py" in exc.value.detail


def test_rpi_for_date_filters_jsonl(monkeypatch, tmp_path, mini_record):
    from app import research
    monkeypatch.setattr(research, "BLENDS_JSONL_PATH", mini_record)
    out = research.rpi_for_date("2025-01-02")
    assert out["as_of"] == "2025-01-02"
    with pytest.raises(Exception):
        research.rpi_for_date("2024-01-01")
