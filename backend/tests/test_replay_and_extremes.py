import json
from datetime import datetime
from types import SimpleNamespace

import numpy as np

from app import api_v1
from app.blending import calibration
from app.replay import build_real_replay_case, real_test_replay_cases


def _inference_row(split="test", calibrated=21.0):
    return {
        "split": split,
        "region": "bay_of_bengal_east_coast",
        "variable": "temperature",
        "valid_time": "2026-01-01T00:00:00",
        "lead_hours": 72,
        "sources": {"GFS": 10.0, "IFS": 30.0, "AIFS": 20.0},
        "weights": {"GFS": 0.1, "IFS": 0.6, "AIFS": 0.3},
        "calibrated": calibrated,
        "observed_value": 22.0,
    }


def test_real_replay_uses_held_out_observation_and_all_baselines():
    summary, detail = build_real_replay_case(_inference_row())

    assert summary["outcome"] == "WIN"
    assert summary["naive_average_error"] == 2.0
    assert summary["single_model_error"] == 8.0
    assert summary["synoptiq_error"] == 1.0
    assert detail["single_model_choice"] == {"model": "IFS", "forecast_value": 30.0}
    assert detail["reference_value"] == 22.0
    assert detail["split"] == "test"
    assert len(detail["raw_sources"]) == 3


def test_real_replay_excludes_training_and_incomplete_source_rows():
    assert build_real_replay_case(_inference_row(split="train")) is None
    incomplete = _inference_row()
    incomplete["sources"].pop("AIFS")
    assert build_real_replay_case(incomplete) is None


def test_replay_catalog_loads_only_test_split_artifact_rows(tmp_path, monkeypatch):
    inference = tmp_path / "inference"
    inference.mkdir()
    rows = [_inference_row(split="train"), _inference_row(split="test")]
    (inference / "historical_blends.jsonl").write_text(
        "\n".join(json.dumps(row) for row in rows), encoding="utf-8"
    )
    monkeypatch.setattr("app.replay.ARTIFACTS_DIR", tmp_path)
    real_test_replay_cases.cache_clear()
    try:
        cases = real_test_replay_cases()
        assert len(cases) == 1
        assert cases[0][0]["outcome"] == "WIN"
    finally:
        real_test_replay_cases.cache_clear()


def test_extreme_guidance_explains_missing_validation_calibrator(tmp_path, monkeypatch):
    monkeypatch.setattr(api_v1, "CALIBRATION_DIR", tmp_path)
    monkeypatch.setattr(api_v1, "_internal_region", lambda _: "bay_of_bengal_east_coast")
    monkeypatch.setattr(api_v1, "_latest_context", lambda *args: datetime(2026, 1, 1))
    monkeypatch.setattr(api_v1, "_blend_public", lambda *args: {
        "sources": [
            {"model": "GFS", "forecast_value": 26.0},
            {"model": "IFS", "forecast_value": 28.0},
            {"model": "AIFS", "forecast_value": 30.0},
        ],
        "final_value": 28.0,
    })
    monkeypatch.setattr(api_v1, "run_blend_pipeline", lambda *args: SimpleNamespace(
        exceedance_probabilities={"heavy": 0.4, "heatwave": 0.3, "gale": 0.2},
        trust_score=0.72,
        bust_probability=0.18,
        disagreement=1.4,
    ))

    response = api_v1.extreme_guidance("BOB", 72, object())

    for variable in ("temperature", "wind_speed"):
        item = next(guidance for guidance in response["guidance"] if guidance["variable"] == variable)
        assert item["calibrated"] is False
        assert item["probability"] is None
        assert item["calibration_status"] == "WITHHELD"
        assert item["probability_reason"] == (
            "Insufficient real calibration events in the non-test calibration set."
        )
        assert item["trust_score"] == 0.72
        assert item["bust_probability"] == 0.18
        assert item["bust_flag"] is False
        assert item["disagreement"] == 1.4
        assert item["source_range"] == {"minimum": 26.0, "maximum": 30.0}


def test_extreme_guidance_applies_temperature_and_wind_artifacts(tmp_path, monkeypatch):
    monkeypatch.setattr(api_v1, "CALIBRATION_DIR", tmp_path)
    monkeypatch.setattr(calibration, "CAL_DIR", tmp_path)
    calibration._load_iso.cache_clear()
    calibration.fit_exceedance_calibrator(
        np.arange(20, dtype=float), np.array([39.0] * 10 + [40.0] * 10),
        "temperature", "heatwave",
    )
    calibration.fit_exceedance_calibrator(
        np.arange(20, dtype=float), np.array([61.0] * 10 + [62.0] * 10),
        "wind_speed", "gale",
    )
    monkeypatch.setattr(api_v1, "_internal_region", lambda _: "bay_of_bengal_east_coast")
    monkeypatch.setattr(api_v1, "_latest_context", lambda *args: datetime(2026, 1, 1))
    monkeypatch.setattr(api_v1, "_blend_public", lambda *args: {
        "sources": [{"model": "GFS"}, {"model": "IFS"}, {"model": "AIFS"}],
        "final_value": 28.0,
    })

    def run_pipeline(_db, region, variable, *_args):
        return SimpleNamespace(
            exceedance_probabilities=calibration.exceedance_probabilities(10.0, variable, region),
        )

    monkeypatch.setattr(api_v1, "run_blend_pipeline", run_pipeline)
    response = api_v1.extreme_guidance("BOB", 72, object())

    for variable in ("temperature", "wind_speed"):
        item = next(guidance for guidance in response["guidance"] if guidance["variable"] == variable)
        assert item["calibrated"] is True
        assert item["calibration_status"] == "CALIBRATED"
        assert item["calibration_source"] == "global"
        assert item["probability"] is not None
        assert 0.0 <= item["probability"] <= 1.0