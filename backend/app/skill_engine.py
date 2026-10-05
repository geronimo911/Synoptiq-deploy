"""
Historical Skill Engine (Section 6). For every model x region x lead_hours x
season it computes RMSE/MAE/Bias for continuous variables, and CSI/POD/FAR
for rainfall event skill at the 50mm pitch threshold.

LEAKAGE FIX: `build_skill_table` now takes an explicit `through` cutoff and
only ever aggregates rows with valid_time < through. It is called with the
test-split boundary (train+validation only, never test) and is used for two
purposes only: (1) a serving-time fallback in `lookup_historical_skill` for
contexts with no archived row to read a per-row expanding feature from, and
(2) the diagnostic per-model scorecard shown at `/verification/scorecard`.
The actual "historical_skill" feature fed to the meta-model is the
per-row EXPANDING version computed in app/data_prep.py, which is safe to
compute over the whole archive (including test rows) because it can only
look backward in time by construction. This table is a coarser, frozen
snapshot — never the live ML feature.

FSS-PROXY note: a textbook Fractions Skill Score needs a gridded
neighbourhood. Our synthetic archive is point-sampled per pilot zone, so
`fss_50mm` here is an explicit FSS-PROXY: exceedance agreement rate pooled
over a rolling window of nearby cases in the same region/season. It is
reported alongside, and must never be presented as, true spatial FSS.
"""
from __future__ import annotations
import numpy as np
import pandas as pd
from datetime import datetime
from sqlalchemy.orm import Session
from app.models_db import ForecastRow, GroundTruthRow, SkillRow
from app.config import THRESHOLDS


def _contingency(pred: np.ndarray, obs: np.ndarray, thresh: float):
    pred_yes = pred >= thresh
    obs_yes = obs >= thresh
    hits = int(np.sum(pred_yes & obs_yes))
    misses = int(np.sum(~pred_yes & obs_yes))
    false_alarms = int(np.sum(pred_yes & ~obs_yes))
    correct_neg = int(np.sum(~pred_yes & ~obs_yes))
    return hits, misses, false_alarms, correct_neg


def _csi_pod_far(pred: np.ndarray, obs: np.ndarray, thresh: float):
    h, m, fa, cn = _contingency(pred, obs, thresh)
    denom_csi = h + m + fa
    csi = h / denom_csi if denom_csi > 0 else np.nan
    pod = h / (h + m) if (h + m) > 0 else np.nan
    far = fa / (h + fa) if (h + fa) > 0 else np.nan
    return csi, pod, far


def _fss_proxy(pred: np.ndarray, obs: np.ndarray, thresh: float, window: int = 5):
    """FSS-PROXY, not true gridded FSS — see module docstring."""
    pred_yes = (pred >= thresh).astype(float)
    obs_yes = (obs >= thresh).astype(float)
    if len(pred_yes) < window:
        window = max(1, len(pred_yes))
    pred_frac = pd.Series(pred_yes).rolling(window, min_periods=1, center=True).mean().values
    obs_frac = pd.Series(obs_yes).rolling(window, min_periods=1, center=True).mean().values
    num = np.mean((pred_frac - obs_frac) ** 2)
    denom = np.mean(pred_frac ** 2) + np.mean(obs_frac ** 2)
    if denom == 0:
        return 1.0
    return float(1 - num / denom)


def build_skill_table(db: Session, through: datetime | None = None) -> int:
    """
    Recompute the FROZEN skill table from forecasts+ground_truth with
    valid_time < through (train+validation only when `through` is the test
    split boundary). Pass through=None only for ad-hoc/manual inspection —
    every caller in scripts/ passes an explicit pre-test cutoff.
    Returns row count written.
    """
    fdf = pd.read_sql(db.query(ForecastRow).statement, db.bind)
    gdf = pd.read_sql(db.query(GroundTruthRow).statement, db.bind)
    if fdf.empty or gdf.empty:
        return 0

    if through is not None:
        through = pd.Timestamp(through)
        fdf = fdf[pd.to_datetime(fdf["valid_time"]) < through]
        gdf = gdf[pd.to_datetime(gdf["valid_time"]) < through]

    merged = fdf.merge(
        gdf[["region", "valid_time", "variable", "observed_value"]],
        on=["region", "valid_time", "variable"], how="inner",
    )

    db.query(SkillRow).delete()
    written = 0
    group_cols = ["model", "region", "lead_hours", "season", "variable"]
    for keys, g in merged.groupby(group_cols):
        model, region, lead_hours, season, variable = keys
        pred = g["forecast_value"].to_numpy()
        obs = g["observed_value"].to_numpy()
        err = pred - obs
        rmse = float(np.sqrt(np.mean(err ** 2)))
        mae = float(np.mean(np.abs(err)))
        bias = float(np.mean(err))

        csi = pod = far = fss = None
        if variable == "precipitation":
            thresh = THRESHOLDS["precipitation"]["heavy"]
            csi, pod, far = _csi_pod_far(pred, obs, thresh)
            fss = _fss_proxy(pred, obs, thresh)
            csi = None if np.isnan(csi) else float(csi)
            pod = None if np.isnan(pod) else float(pod)
            far = None if np.isnan(far) else float(far)

        db.add(SkillRow(
            model=model, region=region, lead_hours=int(lead_hours), season=season,
            variable=variable, rmse=rmse, mae=mae, bias=bias,
            csi_50mm=csi, pod_50mm=pod, far_50mm=far, fss_50mm=fss,
            n_samples=int(len(g)), built_through=through,
        ))
        written += 1
    db.commit()
    return written


def lookup_historical_skill(db: Session, model: str, region: str, lead_hours: int,
                             season: str, variable: str) -> float:
    """
    FALLBACK ONLY: reads the frozen (train+val-only) skill table. Used when
    no per-row expanding feature exists to read (e.g. a genuinely new
    region/context not present in the archive). Live serving and evaluation
    should prefer the per-row `historical_skill_expanding` column populated
    by app/data_prep.py wherever it is available.
    """
    q = (db.query(SkillRow)
           .filter_by(model=model, region=region, season=season, variable=variable)
           .all())
    if not q:
        return 0.5  # neutral prior when nothing is known yet
    row = min(q, key=lambda r: abs(r.lead_hours - lead_hours))
    if variable == "precipitation" and row.csi_50mm is not None:
        return float(np.clip(row.csi_50mm, 0.0, 1.0))
    return float(np.clip(1.0 / (1.0 + row.rmse / 10.0), 0.0, 1.0))
