"""
Tests that batch_blend (used by both calibration fitting and held-out
evaluation) produces valid, leakage-free outputs, and — where the seeded
archive is available — that the frozen artifacts really were built only
from train+validation data.
"""
import os
import numpy as np
import pandas as pd
import pytest

from app.blending.blend import batch_blend
from app.blending.features import FEATURE_NAMES
from app.config import DB_PATH, RUNTIME_MODE


def _fake_feats(n, seed):
    rng = np.random.default_rng(seed)
    return pd.DataFrame(rng.uniform(-1, 1, size=(n, len(FEATURE_NAMES))), columns=FEATURE_NAMES)


def test_batch_blend_weights_sum_to_one_and_are_finite_without_trained_models():
    """No trained artifacts on disk for this made-up variable name -> must
    exercise the skill-weighted fallback path, not crash."""
    n = 10
    feats = {"GFS": _fake_feats(n, 1), "IFS": _fake_feats(n, 2), "AIFS": _fake_feats(n, 3)}
    values = {"GFS": np.full(n, 40.0), "IFS": np.full(n, 170.0), "AIFS": np.full(n, 95.0)}
    fallback_skill = {"GFS": np.full(n, 0.4), "IFS": np.full(n, 0.7), "AIFS": np.full(n, 0.55)}
    if RUNTIME_MODE == "real":
        with pytest.raises(RuntimeError, match="REAL skill model is missing"):
            batch_blend("no_such_variable_ever_trained", feats, values, fallback_skill)
        return
    blended, weights, source = batch_blend("no_such_variable_ever_trained", feats, values, fallback_skill)
    assert source == "skill_fallback"
    stacked = np.vstack([weights[m] for m in weights])
    assert np.all(np.isfinite(stacked))
    assert np.allclose(stacked.sum(axis=0), 1.0)
    assert blended.shape == (n,)


@pytest.mark.skipif(RUNTIME_MODE != "fake" or not DB_PATH.exists(), reason="requires fake-mode seeded archive")
def test_frozen_skill_table_never_touches_test_period():
    """Integration check against the real, bootstrapped archive: every
    SkillRow's built_through boundary must be at/before the global test
    split start, and no row in the archive with split=='test' should have
    been able to influence it (checked indirectly via the stored boundary)."""
    from app.database import SessionLocal
    from app.models_db import SkillRow, ForecastRow
    db = SessionLocal()
    try:
        skill_rows = db.query(SkillRow).all()
        assert len(skill_rows) > 0
        boundaries = {r.built_through for r in skill_rows if r.built_through is not None}
        assert len(boundaries) == 1, "all frozen skill rows should share the same built_through cutoff"
        test_start = list(boundaries)[0]

        min_test_valid_time = (
            db.query(ForecastRow.valid_time)
              .filter(ForecastRow.split == "test")
              .order_by(ForecastRow.valid_time.asc()).first()
        )
        if min_test_valid_time:
            assert pd.Timestamp(min_test_valid_time[0]) >= pd.Timestamp(test_start)
    finally:
        db.close()


@pytest.mark.skipif(RUNTIME_MODE != "fake" or not DB_PATH.exists(), reason="requires fake-mode seeded archive")
def test_every_forecast_row_has_a_split_and_expanding_features():
    from app.database import SessionLocal
    from app.models_db import ForecastRow
    db = SessionLocal()
    try:
        n_missing_split = db.query(ForecastRow).filter(ForecastRow.split.is_(None)).count()
        n_missing_skill = db.query(ForecastRow).filter(ForecastRow.historical_skill_expanding.is_(None)).count()
        assert n_missing_split == 0
        assert n_missing_skill == 0
    finally:
        db.close()
