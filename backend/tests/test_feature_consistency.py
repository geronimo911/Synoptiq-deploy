"""
Tests that training and inference agree on exactly one named feature schema
(the LightGBM feature-name-mismatch fix), and that build_feature_frame /
encode() produce identical columns for identical inputs.
"""
import numpy as np
import pandas as pd
from app.blending.features import encode, FEATURE_NAMES
from app.data_prep import build_feature_frame


def test_encode_returns_dataframe_with_exact_feature_names_in_order():
    row = encode(
        lead_hours=72, regime_probs={"active_monsoon": 0.8, "normal": 0.2},
        region="kerala_western_ghats", season="sw_monsoon", historical_skill=0.6,
        disagreement=20.0, forecast_value=80.0, consensus_deviation=0.1,
        climatological_anomaly=0.05,
    )
    assert isinstance(row, pd.DataFrame)
    assert list(row.columns) == FEATURE_NAMES
    assert len(row) == 1
    assert row.isnull().sum().sum() == 0


def test_build_feature_frame_matches_encode_for_a_single_row():
    df = pd.DataFrame([{
        "lead_hours": 72, "regime_probs": {"active_monsoon": 0.8, "normal": 0.2},
        "region": "kerala_western_ghats", "season": "sw_monsoon",
        "historical_skill_expanding": 0.6, "disagreement": 20.0, "forecast_value": 80.0,
        "consensus_deviation_norm": 0.1, "climatological_anomaly_norm": 0.05,
    }])
    batch_row = build_feature_frame(df, "precipitation")
    single_row = encode(
        lead_hours=72, regime_probs={"active_monsoon": 0.8, "normal": 0.2},
        region="kerala_western_ghats", season="sw_monsoon", historical_skill=0.6,
        disagreement=20.0, forecast_value=80.0, consensus_deviation=0.1,
        climatological_anomaly=0.05, value_scale=100.0,
    )
    assert list(batch_row.columns) == list(single_row.columns)
    np.testing.assert_allclose(batch_row.to_numpy(), single_row.to_numpy(), atol=1e-9)


def test_feature_names_contain_no_lightgbm_unsafe_characters():
    # LightGBM rejects JSON special characters (":" etc.) in feature names.
    unsafe = set(':,"{}[]')
    for name in FEATURE_NAMES:
        assert not (unsafe & set(name)), f"unsafe character in feature name: {name}"


def test_trained_model_predicts_on_named_dataframe_without_column_mismatch(tmp_path):
    """A minimal end-to-end check that a model fit on build_feature_frame's
    output can be queried with encode()'s output with no column mismatch."""
    import lightgbm as lgb
    rng = np.random.default_rng(0)
    rows = []
    for i in range(60):
        rows.append({
            "lead_hours": 72, "regime_probs": {"normal": 1.0}, "region": "kerala_western_ghats",
            "season": "winter", "historical_skill_expanding": float(rng.uniform(0, 1)),
            "disagreement": float(rng.uniform(0, 30)), "forecast_value": float(rng.uniform(0, 100)),
            "consensus_deviation_norm": float(rng.uniform(-1, 1)),
            "climatological_anomaly_norm": float(rng.uniform(-1, 1)),
        })
    df = pd.DataFrame(rows)
    X = build_feature_frame(df, "precipitation")
    y = rng.normal(0, 1, size=len(df))
    model = lgb.LGBMRegressor(n_estimators=5, verbose=-1).fit(X, y)

    query = encode(
        lead_hours=72, regime_probs={"normal": 1.0}, region="kerala_western_ghats",
        season="winter", historical_skill=0.5, disagreement=10.0, forecast_value=50.0,
        consensus_deviation=0.0, climatological_anomaly=0.0,
    )
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # any UserWarning (e.g. feature-name mismatch) fails the test
        pred = model.predict(query)
    assert np.isfinite(pred[0])
