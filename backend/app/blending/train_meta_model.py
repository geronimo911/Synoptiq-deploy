"""
Trains the adaptive meta-model (Section 8): one LightGBM regressor per
forecast source that predicts how good that source's forecast is likely to
be *in this context*. Scores are turned into normalized positive weights via
softmax at inference time (blend.py) — never negative, never unbounded.

Also trains a Forecast Bust classifier (Section 10): P(large blend error)
given context + disagreement.

LEAKAGE + PERFORMANCE FIX (rewritten): all feature construction is now
vectorized through app.data_prep (one DB query per variable, groupby/merge
instead of per-row/per-group DB calls), split assignment is a single
time-based cut shared across every model/region/variable, and both the
meta-model and the bust classifier are fit on split=='train' rows only. The
skill table used for serving fallback + the verification scorecard is
rebuilt separately, from train+validation only, in `freeze_skill_table`.
"""
from __future__ import annotations
import numpy as np
import joblib
import lightgbm as lgb
from sqlalchemy.orm import Session

from app.config import MODELS_DIR, MODELS, VARIABLES
from app.data_prep import (
    prepare_variable_frame, build_feature_frame, compute_split_boundaries,
)
from app.skill_engine import build_skill_table
from app.models_db import ForecastRow

SKILL_MODEL_DIR = MODELS_DIR / "skill"
BUST_MODEL_DIR = MODELS_DIR / "bust"
SKILL_MODEL_DIR.mkdir(exist_ok=True, parents=True)
BUST_MODEL_DIR.mkdir(exist_ok=True, parents=True)


def get_global_split_boundaries(db: Session) -> tuple:
    """One global time cutoff shared by every variable/region, read from the
    full ForecastRow archive so train/val/test are consistent everywhere."""
    import pandas as pd
    vts = pd.read_sql(db.query(ForecastRow.valid_time).distinct().statement, db.bind)["valid_time"]
    return compute_split_boundaries(vts)


def train_all(db: Session) -> dict:
    """Trains per-model skill-score regressors (on TRAIN split only) + bust
    classifiers (on TRAIN split only) for every variable. Returns a report
    dict including per-model sample counts and the split boundaries used."""
    val_start, test_start = get_global_split_boundaries(db)
    report = {"val_start": str(val_start), "test_start": str(test_start), "variables": {}}

    for variable in VARIABLES:
        df = prepare_variable_frame(db, variable, val_start, test_start)
        if df.empty:
            continue
        train_df = df[df["split"] == "train"]

        var_report = {}
        for model_name in MODELS:
            sub = train_df[train_df["model"] == model_name]
            if len(sub) < 50:
                continue
            X = build_feature_frame(sub, variable)  # named DataFrame, not a raw array
            y = -(sub["forecast_value"] - sub["observed_value"]).abs().to_numpy()

            gbm = lgb.LGBMRegressor(
                n_estimators=150, max_depth=4, learning_rate=0.08,
                subsample=0.85, colsample_bytree=0.85, random_state=42, verbose=-1,
            )
            gbm.fit(X, y)  # fit on a named DataFrame -> model remembers feature_names_in_
            joblib.dump(gbm, SKILL_MODEL_DIR / f"skillscore_{variable}_{model_name}.joblib")
            var_report[model_name] = {"n_train": int(len(X))}
        report["variables"][variable] = var_report

        _train_bust_classifier(train_df, variable)

    from app.blending.features import FEATURE_NAMES
    joblib.dump(FEATURE_NAMES, SKILL_MODEL_DIR / "feature_names.joblib")
    return report


def _train_bust_classifier(train_df, variable: str):
    """
    Binary target: did the simple (unweighted) multi-model average miss by
    more than this TRAIN split's own 90th-percentile error? Trained on
    context + disagreement only, and both the threshold and the classifier
    are fit using TRAIN rows exclusively.
    """
    grouped = train_df.groupby(["region", "valid_time", "lead_hours"])
    ctx_rows = []
    errs = []
    for (region, valid_time, lead_hours), g in grouped:
        if g["model"].nunique() < 2:
            continue
        simple_blend = float(g["forecast_value"].mean())
        truth = float(g["observed_value"].iloc[0])
        errs.append(abs(simple_blend - truth))
        row = g.iloc[0].copy()
        row["forecast_value"] = simple_blend
        row["historical_skill_expanding"] = g["historical_skill_expanding"].mean()
        row["disagreement"] = float(g["forecast_value"].std())
        row["consensus_deviation_norm"] = 0.0  # the simple blend IS the consensus
        ctx_rows.append(row)
    if len(errs) < 50:
        return
    import pandas as pd
    ctx_df = pd.DataFrame(ctx_rows)
    X = build_feature_frame(ctx_df, variable)
    thresh90 = float(np.percentile(errs, 90))
    y = (np.array(errs) >= thresh90).astype(int)

    clf = lgb.LGBMClassifier(
        n_estimators=120, max_depth=3, learning_rate=0.08, random_state=42, verbose=-1,
    )
    clf.fit(X, y)
    joblib.dump(clf, BUST_MODEL_DIR / f"bust_{variable}.joblib")
    joblib.dump(thresh90, BUST_MODEL_DIR / f"bust_threshold_{variable}.joblib")


def freeze_skill_table(db: Session, test_start) -> int:
    """Build the diagnostic/fallback skill table from train+validation ONLY
    (valid_time < test_start), i.e. after every learned artifact is frozen
    and before any test-period evaluation runs."""
    return build_skill_table(db, through=test_start)
