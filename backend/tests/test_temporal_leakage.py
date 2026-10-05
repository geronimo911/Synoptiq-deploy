"""
Tests for the leakage fixes: strict split boundaries, and that the expanding
skill/climatology features truly cannot see forward in time.
"""
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from app.data_prep import (
    compute_split_boundaries, assign_split_column, compute_expanding_features,
)


def _toy_frame(n=40):
    """One model/region/lead/season, so groupby collapses to a single group —
    makes the expanding computation trivial to reason about by hand."""
    base = datetime(2024, 1, 1)
    rows = []
    rng = np.random.default_rng(0)
    for i in range(n):
        vt = base + timedelta(hours=12 * i)
        rows.append(dict(
            model="GFS", region="kerala_western_ghats", lead_hours=72, season="winter",
            variable="precipitation", valid_time=vt,
            forecast_value=float(rng.uniform(0, 100)),
            observed_value=float(rng.uniform(0, 100)),
            regime_probs={"normal": 1.0},
        ))
    return pd.DataFrame(rows)


def test_split_boundaries_are_chronological_and_non_overlapping():
    df = _toy_frame(100)
    val_start, test_start = compute_split_boundaries(df["valid_time"])
    assert val_start < test_start
    tagged = assign_split_column(df, val_start, test_start)
    train_max = pd.to_datetime(tagged[tagged["split"] == "train"]["valid_time"]).max()
    val_min = pd.to_datetime(tagged[tagged["split"] == "val"]["valid_time"]).min()
    val_max = pd.to_datetime(tagged[tagged["split"] == "val"]["valid_time"]).max()
    test_min = pd.to_datetime(tagged[tagged["split"] == "test"]["valid_time"]).min()
    assert train_max < val_min
    assert val_max < test_min
    # every row lands in exactly one split
    assert set(tagged["split"].unique()) <= {"train", "val", "test"}


def test_expanding_skill_is_unaffected_by_future_rows():
    """The defining leakage-free property: computing the expanding feature on
    a frame truncated at row i must give the SAME value for row i as
    computing it on the full frame (i.e. row i's feature cannot depend on
    anything at or after its own timestamp being visible)."""
    df = _toy_frame(30)
    full = compute_expanding_features(df.copy(), "precipitation")
    cut_index = 20
    truncated_input = df.iloc[: cut_index + 1].copy()
    truncated = compute_expanding_features(truncated_input, "precipitation")

    full_sorted = full.sort_values("valid_time").reset_index(drop=True)
    trunc_sorted = truncated.sort_values("valid_time").reset_index(drop=True)
    # the row at cut_index (last row of the truncated frame) must match
    assert np.isclose(
        full_sorted.loc[cut_index, "historical_skill_expanding"],
        trunc_sorted.loc[cut_index, "historical_skill_expanding"],
        atol=1e-9,
    )


def test_climatological_anomaly_uses_only_prior_truth():
    df = _toy_frame(15)
    out = compute_expanding_features(df.copy(), "precipitation")
    out = out.sort_values("valid_time").reset_index(drop=True)
    # first row in the archive has no prior history -> anomaly must be neutral (0)
    assert out.loc[0, "climatological_anomaly_norm"] == 0.0


def test_first_row_of_a_group_gets_neutral_skill_prior():
    df = _toy_frame(5)
    out = compute_expanding_features(df.copy(), "precipitation")
    out = out.sort_values("valid_time").reset_index(drop=True)
    assert 0.0 <= out.loc[0, "historical_skill_expanding"] <= 1.0
