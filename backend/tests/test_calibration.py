import numpy as np
import pytest
from app.blending import calibration

def test_quantile_map_roundtrip_on_identity_distribution(tmp_path, monkeypatch):
    monkeypatch.setattr(calibration, "CAL_DIR", tmp_path)
    rng = np.random.default_rng(0)
    blend_vals = rng.normal(50, 10, 500)
    truth_vals = blend_vals.copy()  # perfect model -> mapping should be ~identity
    calibration.fit_quantile_map(blend_vals, truth_vals, "test_variable_identity")
    mapped = calibration.apply_quantile_map(52.0, "test_variable_identity")
    assert abs(mapped - 52.0) < 3.0  # allow interpolation slack

def test_unseen_variable_does_not_use_fake_calibration_in_real_mode():
    if calibration.RUNTIME_MODE == "real":
        with pytest.raises(RuntimeError, match="REAL calibration artifact is missing"):
            calibration.apply_quantile_map(77.0, "no_such_variable_ever_fit")
    else:
        assert calibration.apply_quantile_map(77.0, "no_such_variable_ever_fit") == 77.0


def test_exceedance_fit_requires_five_examples_in_each_class(tmp_path, monkeypatch):
    monkeypatch.setattr(calibration, "CAL_DIR", tmp_path)
    calibration._load_iso.cache_clear()
    forecasts = np.arange(10, dtype=float)
    truths = np.array([39.0] * 6 + [40.0] * 4)

    result = calibration.fit_exceedance_calibrator(
        forecasts, truths, "temperature", "heatwave",
    )

    assert result == {"fitted": False, "positive_count": 4, "negative_count": 6}
    assert not (tmp_path / "iso_temperature_heatwave.joblib").exists()
    assert calibration.exceedance_probabilities(5.0, "temperature")["heatwave"] is None


def test_exceedance_probabilities_use_regional_then_global_and_stay_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(calibration, "CAL_DIR", tmp_path)
    calibration._load_iso.cache_clear()
    forecasts = np.arange(20, dtype=float)
    rising_truth = np.array([39.0] * 10 + [40.0] * 10)
    falling_truth = np.array([40.0] * 10 + [39.0] * 10)
    calibration.fit_exceedance_calibrator(
        forecasts, rising_truth, "temperature", "heatwave",
    )
    global_probability = calibration.exceedance_probabilities(
        4.0, "temperature", "kerala_western_ghats",
    )["heatwave"]
    assert calibration.exceedance_calibration_source(
        "temperature", "heatwave", "kerala_western_ghats",
    ) == "global"

    calibration.fit_exceedance_calibrator(
        forecasts, falling_truth, "temperature", "heatwave", "kerala_western_ghats",
    )
    regional_probability = calibration.exceedance_probabilities(
        4.0, "temperature", "kerala_western_ghats",
    )["heatwave"]
    assert calibration.exceedance_calibration_source(
        "temperature", "heatwave", "kerala_western_ghats",
    ) == "region"
    assert global_probability is not None and regional_probability is not None
    assert 0.0 <= global_probability <= 1.0
    assert 0.0 <= regional_probability <= 1.0
    assert regional_probability != global_probability


def test_validation_calibration_load_excludes_test_rows(monkeypatch):
    from scripts import train_model

    observed = {}

    def fake_prepare(db, variable, val_start, test_start, before_time=None):
        observed["before_time"] = before_time
        import pandas as pd
        return pd.DataFrame()

    monkeypatch.setattr(train_model, "prepare_variable_frame", fake_prepare)
    train_model.fit_calibration_on_validation(None, "temperature", "train", "test")

    assert observed["before_time"] == "test"


def test_oof_calibration_predictions_are_time_ordered(monkeypatch):
    import pandas as pd
    from scripts import train_model

    training_dates = pd.date_range("2025-01-01", periods=30, freq="D")
    train_frame = pd.DataFrame({"valid_time": training_dates})
    monkeypatch.setattr(
        train_model, "_fit_fold_models", lambda *_args: {name: object() for name in ("GFS", "IFS", "AIFS")},
    )

    def fake_inputs(frame, _variable):
        contexts = pd.DataFrame({
            "region": ["kerala_western_ghats"] * len(frame),
            "valid_time": frame["valid_time"].to_numpy(),
            "historical_skill_expanding": [0.5] * len(frame),
        })
        features = {name: pd.DataFrame({"feature": np.arange(len(frame))}) for name in ("GFS", "IFS", "AIFS")}
        values = {name: np.arange(len(frame), dtype=float) for name in features}
        truth = np.arange(len(frame), dtype=float)
        return features, values, truth, contexts

    monkeypatch.setattr(train_model, "build_batch_blend_inputs", fake_inputs)
    monkeypatch.setattr(
        train_model, "batch_blend",
        lambda _variable, _features, values, *_args, **_kwargs: (values["GFS"], {}, "fold_model"),
    )

    oof = train_model._generate_oof_calibration_data(train_frame, "temperature")

    assert not oof.empty
    assert oof["fold"].min() >= 3
    assert set(pd.to_datetime(oof["valid_time"])).issubset(set(training_dates))
    assert pd.to_datetime(oof["valid_time"]).max() <= training_dates[-1]
