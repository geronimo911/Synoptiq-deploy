"""
Central, vectorized data preparation: strict time-based splits, leakage-free
expanding skill/climatology features, and a named feature-frame builder used
identically by training, calibration fitting, and held-out evaluation.

This module exists specifically to fix two problems the earlier version of
this codebase had:

1. LEAKAGE. `skill_engine.build_skill_table()` used to be rebuilt on the
   FULL archive (including the test window) right after training, and every
   downstream lookup — including the held-out evaluation script — read from
   that contaminated table. A model's "historical skill" therefore quietly
   included its own performance on the very period being scored.

   Fix: two complementary, both-leakage-free mechanisms —
     a) EXPANDING features (`compute_expanding_features`): for every row,
        computed via `groupby(...).expanding().shift(1)`, so a row can only
        ever be influenced by strictly EARLIER valid_times. This is safe to
        compute over the *entire* archive in one vectorized pass — train,
        validation and test rows alike — because by construction a test-row
        feature never touches test-period (or later) outcomes. This is also
        the "rolling/expanding historical skill for operational-style
        evaluation" the fix calls for: skill estimates firm up over time,
        exactly as they would operationally.
     b) The FROZEN skill table (`skill_engine.build_skill_table`) is now
        built ONLY from rows with valid_time before the test split boundary,
        and is used only (i) as a serving-time fallback when a context has
        no archived row to read an expanding feature from, and (ii) as the
        diagnostic per-model scorecard in `/verification/scorecard` — it is
        never touched by, or built from, test-period data.

2. PERFORMANCE. Feature construction used to call `lookup_historical_skill`
   (a DB round trip) inside nested Python loops, once per (model, context)
   pair. Everything here is vectorized pandas/numpy: one query per variable,
   then groupby/expanding/merge operations — no per-row DB calls, no
   per-row Python loops in the hot path.
"""
from __future__ import annotations
import bisect
import numpy as np
import pandas as pd
from datetime import datetime
from sqlalchemy.orm import Session

from app.models_db import ForecastRow, GroundTruthRow
from app.config import TRAIN_FRACTION, VAL_FRACTION, THRESHOLDS
from app.blending.features import FEATURE_NAMES, REGION_KEYS
from app.config import SEASONS, REGIMES

VALUE_SCALE = {"precipitation": 100.0, "temperature": 8.0, "wind_speed": 40.0}


# --------------------------------------------------------------------------
# 1. Strict time-based split
# --------------------------------------------------------------------------
def compute_split_boundaries(valid_times: pd.Series | list, train_frac: float = TRAIN_FRACTION,
                              val_frac: float = VAL_FRACTION) -> tuple[datetime, datetime]:
    """Given ALL unique valid_times in the archive, return (val_start, test_start)
    cutoffs so that every row sharing a valid_time lands in the same split,
    regardless of which region/model/variable it belongs to."""
    uniq = sorted(pd.Series(valid_times).unique())
    n = len(uniq)
    val_start = uniq[int(n * train_frac)]
    test_start = uniq[int(n * (train_frac + val_frac))]
    return pd.Timestamp(val_start), pd.Timestamp(test_start)


def assign_split_column(df: pd.DataFrame, val_start, test_start, time_col: str = "valid_time") -> pd.DataFrame:
    vt = pd.to_datetime(df[time_col])
    split = np.where(vt < val_start, "train", np.where(vt < test_start, "val", "test"))
    df = df.copy()
    df["split"] = split
    return df


# --------------------------------------------------------------------------
# 2. Loading
# --------------------------------------------------------------------------
def load_merged_frame(db: Session, variable: str, before_time=None) -> pd.DataFrame:
    """One query for forecasts, one for ground truth, one merge. No per-row
    DB access anywhere downstream of this function."""
    forecast_query = db.query(ForecastRow).filter(ForecastRow.variable == variable)
    truth_query = db.query(GroundTruthRow).filter(GroundTruthRow.variable == variable)
    if before_time is not None:
        forecast_query = forecast_query.filter(ForecastRow.valid_time < before_time)
        truth_query = truth_query.filter(GroundTruthRow.valid_time < before_time)
    fdf = pd.read_sql(forecast_query.statement, db.bind)
    gdf = pd.read_sql(truth_query.statement, db.bind)
    merged = fdf.merge(
        gdf[["region", "valid_time", "variable", "observed_value"]],
        on=["region", "valid_time", "variable"], how="inner",
    )
    merged["valid_time"] = pd.to_datetime(merged["valid_time"])
    return merged.sort_values("valid_time").reset_index(drop=True)


# --------------------------------------------------------------------------
# 3. Expanding, leakage-free features (vectorized)
# --------------------------------------------------------------------------
def _expanding_csi_precip(g: pd.DataFrame, thresh: float) -> pd.Series:
    """Expanding CSI computed from cumulative hit/miss/false-alarm counts,
    shifted by one so the current row's own outcome is excluded."""
    pred_yes = (g["forecast_value"] >= thresh)
    obs_yes = (g["observed_value"] >= thresh)
    hit = (pred_yes & obs_yes).astype(float)
    miss = (~pred_yes & obs_yes).astype(float)
    fa = (pred_yes & ~obs_yes).astype(float)
    cum_hit = hit.cumsum().shift(1)
    cum_miss = miss.cumsum().shift(1)
    cum_fa = fa.cumsum().shift(1)
    denom = cum_hit + cum_miss + cum_fa
    csi = cum_hit / denom
    return csi


def _expanding_rmse_skill(g: pd.DataFrame) -> pd.Series:
    """Expanding RMSE, shifted by one, converted to a bounded [0,1] skill score."""
    sq_err = (g["forecast_value"] - g["observed_value"]) ** 2
    expanding_mse = sq_err.expanding().mean().shift(1)
    rmse = np.sqrt(expanding_mse)
    return 1.0 / (1.0 + rmse / 10.0)


def compute_expanding_features(df: pd.DataFrame, variable: str) -> pd.DataFrame:
    """
    Adds, per row, strictly leakage-free (expanding, shift(1)) columns:
      - historical_skill_expanding: this model's expanding skill in this
        exact (region, lead_hours, season) context, as of just before this
        valid_time. Neutral prior (0.5) when there is no prior history yet.
      - climatological_anomaly_norm: how far this forecast sits from the
        expanding climatological mean of OBSERVED truth for this
        (region, season) as of just before this valid_time — a
        meteorologically meaningful anomaly feature, built only from past
        ground truth, never from the current or any future observation.
    Both are computed once per full archive pass (train+val+test together);
    this is safe because expanding+shift(1) can never look forward.
    """
    df = df.sort_values("valid_time").reset_index(drop=True)
    scale = VALUE_SCALE.get(variable, 50.0)

    # --- historical_skill_expanding ---
    skill_parts = []
    group_cols = ["model", "region", "lead_hours", "season"]
    for _, g in df.groupby(group_cols, sort=False):
        g = g.sort_values("valid_time")
        if variable == "precipitation":
            thresh = THRESHOLDS["precipitation"]["heavy"]
            skill = _expanding_csi_precip(g, thresh)
            # Not enough prior events to form a CSI denominator yet -> fall
            # back to expanding RMSE-skill for that stretch, then neutral.
            rmse_skill = _expanding_rmse_skill(g)
            skill = skill.fillna(rmse_skill)
        else:
            skill = _expanding_rmse_skill(g)
        skill = skill.fillna(0.5).clip(0.0, 1.0)
        skill_parts.append(pd.Series(skill.values, index=g.index))
    df["historical_skill_expanding"] = pd.concat(skill_parts).reindex(df.index)

    # --- climatological_anomaly_norm (built from ground truth only) ---
    truth_only = df[["region", "season", "valid_time", "observed_value"]].drop_duplicates(
        subset=["region", "season", "valid_time"]
    ).sort_values("valid_time")
    clim_parts = []
    for _, g in truth_only.groupby(["region", "season"], sort=False):
        g = g.sort_values("valid_time")
        clim_mean_prior = g["observed_value"].expanding().mean().shift(1)
        clim_parts.append(pd.DataFrame({
            "region": g["region"], "season": g["season"], "valid_time": g["valid_time"],
            "clim_mean_prior": clim_mean_prior.values,
        }))
    clim_df = pd.concat(clim_parts, ignore_index=True)
    df = df.merge(clim_df, on=["region", "season", "valid_time"], how="left")
    # No prior climatology yet (start of archive) -> neutral anomaly of 0.
    df["clim_mean_prior"] = df["clim_mean_prior"].fillna(df["forecast_value"])
    df["climatological_anomaly_norm"] = (df["forecast_value"] - df["clim_mean_prior"]) / scale
    df = df.drop(columns=["clim_mean_prior"])
    return df


def add_consensus_deviation(df: pd.DataFrame, variable: str) -> pd.DataFrame:
    """Model-vs-consensus deviation: how far THIS model's forecast sits from
    the multi-model mean at the same context. Fully vectorized via
    groupby().transform — no leakage concern since it only uses forecasts
    issued for the same context, never any observation."""
    scale = VALUE_SCALE.get(variable, 50.0)
    consensus_mean = df.groupby(["region", "valid_time", "lead_hours"])["forecast_value"].transform("mean")
    df = df.copy()
    df["consensus_deviation_norm"] = (df["forecast_value"] - consensus_mean) / scale
    return df


def add_disagreement(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["disagreement"] = df.groupby(["region", "valid_time", "lead_hours"])["forecast_value"].transform("std").fillna(0.0)
    return df


# --------------------------------------------------------------------------
# 4. Vectorized, named feature-frame builder (fixes the LightGBM feature-name
#    mismatch: training and inference both build a DataFrame with these exact
#    columns, in this exact order).
# --------------------------------------------------------------------------
def build_feature_frame(df: pd.DataFrame, variable: str) -> pd.DataFrame:
    """
    df must already have: lead_hours, regime_probs (dict), region, season,
    historical_skill_expanding, disagreement, consensus_deviation_norm,
    climatological_anomaly_norm, forecast_value.
    Returns a DataFrame with columns == FEATURE_NAMES (app/blending/features.py),
    same schema used at inference time by features.encode().
    """
    scale = VALUE_SCALE.get(variable, 50.0)
    n = len(df)
    out = pd.DataFrame(index=df.index)
    out["lead_hours_norm"] = df["lead_hours"].astype(float) / 120.0

    regime_expanded = pd.json_normalize(df["regime_probs"].apply(lambda d: d if isinstance(d, dict) else {}))
    for r in REGIMES:
        out[f"regime_prob__{r}"] = regime_expanded[r].values if r in regime_expanded.columns else 0.0

    for r in REGION_KEYS:
        out[f"region__{r}"] = (df["region"] == r).astype(float)
    for s in SEASONS:
        out[f"season__{s}"] = (df["season"] == s).astype(float)

    out["historical_skill"] = df["historical_skill_expanding"].astype(float)
    out["disagreement_norm"] = df["disagreement"].astype(float) / scale
    out["forecast_value_norm"] = df["forecast_value"].astype(float) / scale
    out["consensus_deviation_norm"] = df["consensus_deviation_norm"].astype(float)
    out["climatological_anomaly_norm"] = df["climatological_anomaly_norm"].astype(float)

    return out[FEATURE_NAMES]


def prepare_variable_frame(db: Session, variable: str, val_start, test_start,
                           before_time=None) -> pd.DataFrame:
    """One-stop, fully vectorized prep for a variable: load -> split -> disagreement
    -> consensus deviation -> expanding skill/climatology -> ready for feature-frame
    construction. Returns the enriched row-level DataFrame (not yet the named
    feature matrix — call build_feature_frame(...) per model subset for that)."""
    df = load_merged_frame(db, variable, before_time=before_time)
    if df.empty:
        return df
    df = assign_split_column(df, val_start, test_start)
    df = add_disagreement(df)
    df = add_consensus_deviation(df, variable)
    df = compute_expanding_features(df, variable)
    return df


# --------------------------------------------------------------------------
# 5. Shared batch-blend input builder — used by BOTH calibration fitting
#    (validation split) and held-out evaluation (test split), so the
#    per-model recomputation of consensus-deviation / climatological-anomaly
#    (which genuinely differ per model, since they depend on that model's own
#    forecast_value) is defined exactly once.
# --------------------------------------------------------------------------
def build_batch_blend_inputs(df_split: pd.DataFrame, variable: str):
    """
    df_split: rows from ONE split (val or test) for ONE variable, as produced
    by prepare_variable_frame. Returns:
      feats_by_model: {model: DataFrame[FEATURE_NAMES]} row-aligned across models
      values_by_model: {model: np.ndarray} raw forecast values, same row order
      truth: np.ndarray of observed values, same row order
      context_meta: the deduped per-context frame the above are aligned to
    Every context (region, valid_time, lead_hours) must have ALL models
    present to be included (a context missing a source is dropped from
    calibration/evaluation, consistent with "keep a skill-weighted fallback"
    only applying at live-serving time, not at metric time).
    """
    scale = VALUE_SCALE.get(variable, 50.0)
    pivot_val = df_split.pivot_table(index=["region", "valid_time", "lead_hours"],
                                      columns="model", values="forecast_value", aggfunc="first")
    pivot_skill = df_split.pivot_table(index=["region", "valid_time", "lead_hours"],
                                        columns="model", values="historical_skill_expanding", aggfunc="first")
    pivot_val = pivot_val.dropna()
    if pivot_val.empty:
        return {}, {}, np.array([]), pivot_val

    context_meta = df_split.drop_duplicates(subset=["region", "valid_time", "lead_hours"]).set_index(
        ["region", "valid_time", "lead_hours"]
    ).loc[pivot_val.index].reset_index()

    # Recover each context's climatological mean-prior from the representative
    # row's own (forecast_value, climatological_anomaly_norm) pair, then
    # reapply it per model — anomaly genuinely differs per model's own value.
    rep_model = context_meta["model"].to_numpy()
    rep_forecast = context_meta["forecast_value"].to_numpy()
    rep_anomaly = context_meta["climatological_anomaly_norm"].to_numpy()
    clim_mean_prior = rep_forecast - rep_anomaly * scale

    mean_across_models = pivot_val.mean(axis=1).to_numpy()

    feats_by_model, values_by_model = {}, {}
    for m in pivot_val.columns:
        sub = context_meta.copy()
        sub["forecast_value"] = pivot_val[m].to_numpy()
        sub["consensus_deviation_norm"] = (pivot_val[m].to_numpy() - mean_across_models) / scale
        sub["climatological_anomaly_norm"] = (pivot_val[m].to_numpy() - clim_mean_prior) / scale
        if m in pivot_skill.columns:
            sub["historical_skill_expanding"] = pivot_skill[m].fillna(0.5).to_numpy()
        feats_by_model[m] = build_feature_frame(sub, variable)
        values_by_model[m] = pivot_val[m].to_numpy()

    truth = context_meta["observed_value"].to_numpy()
    return feats_by_model, values_by_model, truth, context_meta
