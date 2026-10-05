"""
Shared feature encoding used identically at training time and inference
time, so there is exactly one definition of "context" (Section 7) anywhere
in the codebase. `encode()` returns a single-row pandas DataFrame with named
columns (not a raw numpy array) so a LightGBM model fit on a named
DataFrame and later queried via `encode()` never hits the
"X does not have valid feature names" mismatch warning — both sides always
agree on the same schema, defined once, here.
"""
from __future__ import annotations
import numpy as np
import pandas as pd
from app.config import PILOT_ZONES, SEASONS, REGIMES

REGION_KEYS = list(PILOT_ZONES.keys())

FEATURE_NAMES = (
    ["lead_hours_norm"]
    + [f"regime_prob__{r}" for r in REGIMES]
    + [f"region__{r}" for r in REGION_KEYS]
    + [f"season__{s}" for s in SEASONS]
    + [
        "historical_skill",          # now an EXPANDING, leakage-free score (see app/data_prep.py)
        "disagreement_norm",
        "forecast_value_norm",
        "consensus_deviation_norm",   # model-vs-consensus deviation (meteorologically meaningful)
        "climatological_anomaly_norm",  # forecast vs. expanding climatological mean of truth
    ]
)


def encode(
    lead_hours: int,
    regime_probs: dict,
    region: str,
    season: str,
    historical_skill: float,
    disagreement: float,
    forecast_value: float,
    consensus_deviation: float = 0.0,
    climatological_anomaly: float = 0.0,
    value_scale: float = 100.0,
) -> pd.DataFrame:
    """Returns a single-row DataFrame with columns == FEATURE_NAMES, ready to
    pass straight into a LightGBM model's .predict()/.predict_proba() or a
    SHAP TreeExplainer without any array-vs-DataFrame mismatch."""
    row = {"lead_hours_norm": lead_hours / 120.0}
    for r in REGIMES:
        row[f"regime_prob__{r}"] = regime_probs.get(r, 0.0)
    for r in REGION_KEYS:
        row[f"region__{r}"] = 1.0 if region == r else 0.0
    for s in SEASONS:
        row[f"season__{s}"] = 1.0 if season == s else 0.0
    row["historical_skill"] = historical_skill
    row["disagreement_norm"] = disagreement / value_scale
    row["forecast_value_norm"] = forecast_value / value_scale
    row["consensus_deviation_norm"] = consensus_deviation
    row["climatological_anomaly_norm"] = climatological_anomaly
    return pd.DataFrame([row], columns=FEATURE_NAMES)
