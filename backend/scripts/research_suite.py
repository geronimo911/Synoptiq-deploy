"""Research & verification suite for the REAL_12M record.

Computes every "proof" claim the Synoptiq submission makes, directly from
the archived historical blend records (artifacts/real_12m_aifs_corrected/
inference/historical_blends.jsonl), which contain — per (valid_time,
region, variable, lead) — the three raw provider values, the weights the
meta-model issued, the raw and bias-corrected blend, the trust score, the
bust probability, the abstain flag and the observed truth.

Everything is read-only: this script never touches the blending pipeline,
the calibration artifacts or the database. Headline claims are computed on
the untouched TEST split only; descriptive statistics (bias fingerprints,
skill evolution, lead-time crossovers) use the full 12-month record and
are labelled as such in the output JSON.

Sections computed (one output JSON):
  baselines        persistence + climatology + per-model + naive-average,
                   with cluster-bootstrap 95% CIs on the headline improvements
  hard_days        error on easy (models agreed) vs hard (models split) days
  oracle           hindsight-oracle regret analysis
  ai_ablation      GFS+IFS physics-only blend vs the full AI-including blend
  imd_classes      IMD rainfall-class POD / FAR / CSI (15.6 / 64.5 / 115.5 mm)
  trust_audit     observed error by issued trust tier + abstain audit
  crossovers       lead-time crossover detection ("best model changes at h")
  skill_evolution  rolling 30-day RMSE per model + blend (weekly sampled)
  bias_fingerprints per-model systematic bias by region / season
  rpi              Risk Priority Index alerts on the latest archived day

Usage:
  python backend/scripts/research_suite.py \
      --blends artifacts/real_12m_aifs_corrected/inference/historical_blends.jsonl \
      --out     artifacts/real_12m_aifs_corrected/research/research_suite.json
"""
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

MODELS = ["GFS", "IFS", "AIFS"]
VARIABLES = ["precipitation", "temperature", "wind_speed"]

# IMD rainfall classes (mm/day, zone-mean daily rainfall).
IMD_THRESHOLDS = {
    "moderate": 15.6,     # >= 15.6 mm
    "heavy": 64.5,        # >= 64.5 mm
    "very_heavy": 115.5,  # >= 115.5 mm
}

# RPI action levels (impact-based, IMD-style naming).
RPI_LEVELS = [("SEVERE", 85.0), ("WARNING", 65.0), ("ALERT", 45.0), ("WATCH", 25.0)]

SEASONS = {12: "winter", 1: "winter", 2: "winter",
           3: "pre_monsoon", 4: "pre_monsoon", 5: "pre_monsoon",
           6: "monsoon", 7: "monsoon", 8: "monsoon", 9: "monsoon",
           10: "post_monsoon", 11: "post_monsoon"}


def season_of(ts: pd.Timestamp) -> str:
    return SEASONS[ts.month]


def _safe_mae(y, yhat) -> float:
    y, yhat = np.asarray(y, float), np.asarray(yhat, float)
    if len(y) == 0:
        return float("nan")
    return float(np.mean(np.abs(y - yhat)))


def _mae_row(sub: pd.DataFrame, pred_of) -> dict:
    return {name: _safe_mae(sub.observed_value, sub.apply(pred_of, axis=1))
            for name in MODELS} if False else None


def _series_mae(sub: pd.DataFrame, series: pd.Series) -> float:
    y = sub.observed_value.to_numpy(float)
    yhat = series.to_numpy(float)
    if len(y) == 0:
        return float("nan")
    return float(np.mean(np.abs(y - yhat)))


def _pod_far_csi(observed: np.ndarray, forecast: np.ndarray, threshold: float) -> dict:
    hits = int(np.sum((observed >= threshold) & (forecast >= threshold)))
    misses = int(np.sum((observed >= threshold) & (forecast < threshold)))
    false_alarms = int(np.sum((observed < threshold) & (forecast >= threshold)))
    correct_neg = int(np.sum((observed < threshold) & (forecast < threshold)))
    pod = hits / (hits + misses) if hits + misses else None
    far = false_alarms / (hits + false_alarms) if hits + false_alarms else None
    csi = hits / (hits + misses + false_alarms) if hits + misses + false_alarms else None
    return {"hits": hits, "misses": misses, "false_alarms": false_alarms,
            "correct_negatives": correct_neg, "pod": pod, "far": far, "csi": csi,
            "n_events": hits + misses}


def _pctl_ci(samples: np.ndarray) -> list:
    lo, hi = np.percentile(samples, [2.5, 97.5])
    return [float(lo), float(hi)]


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------

def build_baselines(df: pd.DataFrame, test: pd.DataFrame) -> dict:
    """Persistence, climatology and naive baselines, with bootstrap CIs.

    Persistence: forecast for day D = observed value on day D-1 (the standard
    day-ahead persistence benchmark; identical across leads because every
    lead verifies against the same daily zone-mean observation).

    Climatology: month-of-year mean of the observed value computed on the
    TRAIN split only (no leakage into the test period).
    """
    train = df[df.split == "train"]
    obs_lookup = (df[["valid_time", "region", "variable", "observed_value"]]
                  .drop_duplicates(["valid_time", "region", "variable"])
                  .set_index(["region", "variable", "valid_time"])
                  .observed_value)

    rng = np.random.default_rng(42)
    out_rows = []
    for (region, variable), sub in test.groupby(["region", "variable"]):
        sub = sub.copy()
        # --- persistence forecast per row: obs(valid_time - 1 day) ---
        prev_idx = pd.MultiIndex.from_arrays([
            sub.region, sub.variable, sub.valid_time - pd.Timedelta(days=1)])
        sub["_persistence"] = prev_idx.map(obs_lookup).astype(float).values

        # --- climatology forecast: month-of-year mean from TRAIN only ---
        clim = (train[(train.region == region) & (train.variable == variable)]
                .assign(month=lambda t: t.valid_time.dt.month)
                .groupby("month").observed_value.mean())
        sub["_climatology"] = sub.valid_time.dt.month.map(clim).astype(float).values
        sub["_naive"] = np.mean([[s[m] for m in MODELS] for s in sub.sources], axis=1)

        per_model = {m: _series_mae(sub, sub.sources.apply(lambda s: s[m]))
                     for m in MODELS}
        best_single = min(per_model, key=per_model.get)
        col_of = {m: m for m in MODELS}  # per-model columns exist after df[m] explosion

        # day-level sums make the cluster bootstrap a cheap matrix operation:
        # MAE(resample) = sum(sums[picked days]) / sum(n[picked days])
        day_tab = sub.assign(
            _eb=(sub.calibrated - sub.observed_value).abs()).groupby("valid_time").agg(
            _eb_sum=("_eb", "sum"), _n=("calibrated", "size"))

        def _boot_col(col: str) -> dict:
            ec = (sub[col] - sub.observed_value).abs()
            tab = day_tab.assign(_ec_sum=ec.groupby(sub.valid_time).sum())
            s_b, s_c, n = (tab._eb_sum.to_numpy(), tab._ec_sum.to_numpy(),
                           tab._n.to_numpy())
            picks = rng.integers(0, len(n), size=(1000, len(n)))
            mae_b = s_b[picks].sum(axis=1) / n[picks].sum(axis=1)
            mae_c = s_c[picks].sum(axis=1) / n[picks].sum(axis=1)
            imps = 100.0 * (mae_c - mae_b) / mae_c
            return {"mean_mae_reduction": float(np.mean(mae_c - mae_b)),
                    "improvement_pct": float(np.mean(imps)),
                    "ci95_improvement_pct": _pctl_ci(imps)}

        mae = {
            "blend": _series_mae(sub, sub.calibrated),
            "naive_average": _series_mae(sub, sub["_naive"]),
            "persistence": _series_mae(sub, sub["_persistence"]),
            "climatology": _series_mae(sub, sub["_climatology"]),
            **per_model,
        }
        out_rows.append({
            "region": region, "variable": variable,
            "n_test_rows": int(len(sub)),
            "n_test_days": int(sub.valid_time.nunique()),
            "mae": {k: (round(v, 4) if v == v else None) for k, v in mae.items()},
            "best_single_model": best_single,
            "bootstrap": {
                "method": "cluster bootstrap over test days, 1000 resamples, seed 42",
                "blend_vs_best_single": _boot_col(col_of[best_single]),
                "blend_vs_naive_average": _boot_col("_naive"),
            },
        })
    return {"note": "MAE in native units; persistence = previous day's observed value; "
                    "climatology = month-of-year mean from the train split only.",
            "rows": out_rows}


def build_hard_days(test: pd.DataFrame) -> dict:
    """Split test rows into easy (low model spread) / hard (high spread)."""
    rows = []
    for (region, variable), sub in test.groupby(["region", "variable"]):
        spread = sub.sources.apply(lambda s: max(s.values()) - min(s.values()))
        q1, q2 = spread.quantile([1 / 3, 2 / 3])
        easy, hard = sub[spread <= q1], sub[spread >= q2]
        naive = lambda g: np.mean([[s[m] for m in MODELS] for s in g.sources], axis=1)
        res = {}
        for name, g in (("easy", easy), ("hard", hard)):
            res[name] = {
                "n": int(len(g)),
                "mae": {
                    "blend": round(_series_mae(g, g.calibrated), 4),
                    "naive_average": round(_safe_mae(g.observed_value, naive(g)), 4),
                    **{m: round(_series_mae(g, g.sources.apply(lambda s: s[m])), 4)
                       for m in MODELS},
                },
            }

        def gain(group):
            base = min(res[group]["mae"][m] for m in MODELS + ["naive_average"])
            b = res[group]["mae"]["blend"]
            return round(100.0 * (base - b) / base, 2) if base else None
        rows.append({
            "region": region, "variable": variable,
            "definition": "easy = lowest tercile of GFS/IFS/AIFS spread, hard = highest tercile",
            "easy": res["easy"], "hard": res["hard"],
            "blend_gain_pct": {"easy": gain("easy"), "hard": gain("hard")},
        })
    return {"rows": rows,
            "claim_template": "If blend_gain_pct.hard > blend_gain_pct.easy, the blend "
                              "earns its keep precisely on the days that matter."}


def build_oracle(test: pd.DataFrame) -> dict:
    """Hindsight-oracle regret: how much of the oracle's advantage over the
    best fixed single model does the blend recover?"""
    rows = []
    for (region, variable), sub in test.groupby(["region", "variable"]):
        err_mat = np.abs(np.column_stack(
            [sub[m].to_numpy(float) - sub.observed_value.to_numpy(float)
             for m in MODELS]))
        oracle_mae = float(err_mat.min(axis=1).mean())
        blend_mae = float(np.abs(sub.calibrated.to_numpy(float)
                                 - sub.observed_value.to_numpy(float)).mean())
        fixed = {m: float(err_mat[:, i].mean()) for i, m in enumerate(MODELS)}
        best_fixed = min(fixed, key=fixed.get)
        gap = fixed[best_fixed] - oracle_mae
        recovery = 100.0 * (fixed[best_fixed] - blend_mae) / gap if gap > 0 else None
        rows.append({
            "region": region, "variable": variable, "n": int(len(sub)),
            "oracle_mae": round(oracle_mae, 4),
            "best_fixed_model": best_fixed,
            "best_fixed_mae": round(fixed[best_fixed], 4),
            "blend_mae": round(blend_mae, 4),
            "oracle_advantage_recovered_pct": round(recovery, 2) if recovery is not None else None,
            "per_model_mae": {m: round(v, 4) for m, v in fixed.items()},
        })
    overall_oracle = float(np.mean([r["oracle_mae"] for r in rows]))
    overall_blend = float(np.mean([r["blend_mae"] for r in rows]))
    overall_fixed = float(np.mean([r["best_fixed_mae"] for r in rows]))
    gap = overall_fixed - overall_oracle
    return {"definition": "oracle = best single model chosen in hindsight for each day; "
                          "recovery = share of the oracle's MAE advantage over the best "
                          "fixed single model that the blend achieves",
            "rows": rows,
            "overall": {
                "oracle_mae": round(overall_oracle, 4),
                "best_fixed_mae": round(overall_fixed, 4),
                "blend_mae": round(overall_blend, 4),
                "oracle_advantage_recovered_pct":
                    round(100.0 * (overall_fixed - overall_blend) / gap, 2) if gap > 0 else None,
            }}


def build_ai_ablation(test: pd.DataFrame) -> dict:
    """What does adding the AI model (AIFS) to the physics pair buy?"""
    rows = []
    for (region, variable), sub in test.groupby(["region", "variable"]):
        physics_only = sub.sources.apply(
            lambda s: (s["GFS"] + s["IFS"]) / 2.0)
        rows.append({
            "region": region, "variable": variable, "n": int(len(sub)),
            "physics_only_mae": round(_series_mae(sub, physics_only), 4),
            "aifs_only_mae": round(_series_mae(sub, sub.sources.apply(lambda s: s["AIFS"])), 4),
            "full_blend_mae": round(_series_mae(sub, sub.calibrated), 4),
        })
    for r in rows:
        base = r["physics_only_mae"]
        r["ai_value_pct"] = round(100.0 * (base - r["full_blend_mae"]) / base, 2) if base else None
    return {"definition": "physics_only = equal-weight GFS+IFS mean (pre-AI operational "
                          "practice); ai_value_pct > 0 means the AI-including blend beats it",
            "rows": rows}


def build_imd_classes(df: pd.DataFrame, test: pd.DataFrame) -> dict:
    rain = df[df.variable == "precipitation"]
    rain_test = test[test.variable == "precipitation"]

    def block(frame: pd.DataFrame) -> dict:
        out = {}
        obs = frame.observed_value.to_numpy(float)
        for name, thr in IMD_THRESHOLDS.items():
            systems = {"blend": frame.calibrated.to_numpy(float)}
            for m in MODELS:
                systems[m] = frame.sources.apply(lambda s: s[m]).to_numpy(float)
            out[name] = {"threshold_mm": thr,
                         **{k: _pod_far_csi(obs, v, thr) for k, v in systems.items()}}
        return out

    return {
        "thresholds_mm": IMD_THRESHOLDS,
        "note": "zone-mean daily rainfall; test split is the NE-monsoon/winter period "
                "(rain events are rarer there than in the full year)",
        "test": block(rain_test),
        "full_year": block(rain),
    }


def build_trust_audit(test: pd.DataFrame) -> dict:
    tiers = [(0.4, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.01)]
    err = (test.calibrated - test.observed_value).abs()
    rows = []
    for lo, hi in tiers:
        mask = (test.trust >= lo) & (test.trust < hi)
        if mask.sum() == 0:
            continue
        rows.append({
            "tier": f"{lo:.1f}-{min(hi, 1.0):.1f}", "n": int(mask.sum()),
            "mean_trust": round(float(test.trust[mask].mean()), 3),
            "mean_abs_error": round(float(err[mask].mean()), 4),
            "median_abs_error": round(float(err[mask].median()), 4),
        })
    abstain_err = err[test.abstain]
    kept_err = err[~test.abstain]
    # rank correlation between issued trust and realised error (should be <= 0:
    # higher trust, lower error)
    from scipy.stats import spearmanr  # optional; degrade gracefully
    try:
        rho, p = spearmanr(test.trust, err)
        corr = {"spearman_rho": round(float(rho), 4), "p_value": float(p)}
    except Exception:
        corr = None
    return {
        "tiers": rows,
        "abstain_audit": {
            "abstained": {"n": int(test.abstain.sum()),
                          "mean_abs_error": round(float(abstain_err.mean()), 4)},
            "not_abstained": {"n": int((~test.abstain).sum()),
                              "mean_abs_error": round(float(kept_err.mean()), 4)},
        },
        "trust_vs_error_rank_correlation": corr,
        "interpretation": "A negative rank correlation and monotone increasing "
                          "mean error across tiers means the issued trust is "
                          "informative: higher trust forecasts really do err less.",
    }


def build_calibration_sanity(test: pd.DataFrame) -> dict:
    """Did the bias-correction step actually help, per variable?"""
    out = {}
    for variable, sub in test.groupby("variable"):
        raw = _series_mae(sub, sub.blend)
        cal = _series_mae(sub, sub.calibrated)
        out[variable] = {
            "raw_blend_mae": round(raw, 4),
            "bias_corrected_mae": round(cal, 4),
            "improvement_pct": round(100.0 * (raw - cal) / raw, 2) if raw else None,
        }
    return {"note": "positive improvement_pct means the bias-correction step "
                    "reduced test MAE for that variable",
            "rows": out}


def build_crossovers(df: pd.DataFrame) -> list:
    out = []
    for (region, variable), sub in df.groupby(["region", "variable"]):
        by_lead = {}
        for lead, g in sub.groupby("lead_hours"):
            by_lead[lead] = {"blend": _series_mae(g, g.calibrated)}
            for m in MODELS:
                by_lead[lead][m] = _series_mae(g, g.sources.apply(lambda s: s[m]))
        best_by_lead = {str(lead): min(MODELS, key=lambda m: v[m])
                        for lead, v in by_lead.items()}
        leads = sorted(by_lead)
        changes = []
        for a, b in zip(leads, leads[1:]):
            if best_by_lead[str(a)] != best_by_lead[str(b)]:
                changes.append({"from_lead": a, "to_lead": b,
                                "from_model": best_by_lead[str(a)],
                                "to_model": best_by_lead[str(b)]})
        statement = None
        if changes:
            first = changes[0]
            statement = (f"{first['from_model']} leads through +{first['from_lead']}h; "
                         f"{first['to_model']} is best from +{first['to_lead']}h onward "
                         f"({region.replace('_', ' ')}, {variable})")
        else:
            stable = best_by_lead[str(leads[0])] if leads else None
            if stable:
                statement = (f"No lead-time crossover: {stable} is the best single model "
                             f"at every lead from +24h to +120h in the full-year record "
                             f"({region.replace('_', ' ')}, {variable})")
        out.append({
            "region": region, "variable": variable,
            "rmse_by_lead": {str(l): {k: round(v[k], 4) for k in v} for l, v in by_lead.items()},
            "best_single_by_lead": best_by_lead,
            "crossovers": changes,
            "crossover_statement": statement,
            "scope": "descriptive, full 12-month record",
        })
    return out


def build_skill_evolution(df: pd.DataFrame) -> dict:
    rows = []
    for (region, variable), sub in df.groupby(["region", "variable"]):
        sub = sub.sort_values("valid_time")
        roll = sub.set_index("valid_time")
        daily_err = pd.DataFrame({
            "blend": (roll.calibrated - roll.observed_value).abs(),
            **{m: (roll.sources.apply(lambda s: s[m]) - roll.observed_value).abs()
               for m in MODELS},
        })
        rmse_roll = np.sqrt((daily_err ** 2).rolling("30D").mean())
        weekly = rmse_roll.resample("7D").last().dropna()
        for ts, r in weekly.iterrows():
            rows.append({"date": ts.date().isoformat(), "region": region,
                         "variable": variable,
                         "rmse": {k: (round(float(v), 4) if not math.isnan(v) else None)
                                  for k, v in r.items()}})
    return {"window": "rolling 30-day RMSE, sampled weekly",
            "scope": "descriptive, full 12-month record",
            "points": rows}


def build_bias_fingerprints(df: pd.DataFrame, min_days: int = 30) -> dict:
    d = df.copy()
    d["season"] = d.valid_time.map(season_of)
    frames = []
    for m in MODELS:
        frames.append(pd.DataFrame({
            "model": m, "region": d.region, "variable": d.variable,
            "season": d.season, "bias": d[m].to_numpy(float) - d.observed_value.to_numpy(float)}))
    bias = pd.concat(frames, ignore_index=True).groupby(
        ["model", "region", "variable", "season"]).agg(
        mean_bias=("bias", "mean"), n=("bias", "size")).reset_index()
    bias = bias[bias.n >= min_days]
    bias = bias.reindex(bias.mean_bias.abs().sort_values(ascending=False).index)
    top = []
    for _, r in bias.head(12).iterrows():
        direction = "under-forecasts" if r.mean_bias < 0 else "over-forecasts"
        top.append({
            "model": r.model, "region": r.region, "variable": r.variable,
            "season": r.season, "n_days": int(r.n),
            "mean_bias": round(float(r.mean_bias), 3),
            "statement": (f"{r.model} {direction} {r.variable.replace('_', ' ')} in "
                          f"{r.region.replace('_', ' ')} during {r.season.replace('_', ' ')} "
                          f"by {abs(round(float(r.mean_bias), 2))} on average "
                          f"over {int(r.n)} days"),
        })
    return {"scope": "descriptive, full 12-month record",
            "min_days": min_days, "cards": top}


def _rpi_severity(variable: str, value: float) -> tuple[int, str | None]:
    if variable == "precipitation":
        if value >= 115.5: return 100, "very_heavy_rain"
        if value >= 64.5: return 75, "heavy_rain"
        if value >= 15.6: return 50, "moderate_rain"
    elif variable == "temperature":
        if value >= 40.0: return 75, "heat_alert"
        if value <= 5.0: return 50, "cold_alert"
    elif variable == "wind_speed":
        if value >= 50.0: return 75, "damaging_wind"
        if value >= 40.0: return 50, "strong_wind"
    return 0, None


def build_rpi(df: pd.DataFrame, date: str | None = None) -> dict:
    """Risk Priority Index over the most recent archived day of the record.

    severity points follow IMD-recognised classes: rain 15.6 / 64.5 / 115.5 mm,
    heat 40 degC, wind 50 km/h (damaging winds). The score multiplies severity
    by issued trust and a lead-time decay, so a distant low-trust signal can
    never outrank a near-term high-trust one at equal severity.
    """
    latest_day = pd.Timestamp(date) if date else df.valid_time.max()
    today = df[df.valid_time == latest_day]
    if today.empty:
        raise ValueError(f"no archived contexts for {date}")

    alerts = []
    for _, r in today.iterrows():
        points, label = _rpi_severity(r.variable, float(r.calibrated))
        if points == 0:
            continue
        lead_decay = 1.0 - 0.10 * (int(r.lead_hours) / 24.0 - 1.0)
        score = points * (0.5 + 0.5 * float(r.trust)) * lead_decay
        level = "INFO"
        for name, thr in RPI_LEVELS:
            if score >= thr:
                level = name
                break
        alerts.append({
            "region": r.region, "variable": r.variable,
            "lead_hours": int(r.lead_hours),
            "value": round(float(r.calibrated), 2),
            "trust": round(float(r.trust), 3),
            "bust_probability": round(float(r.bust_probability), 3),
            "event": label, "severity_points": points,
            "rpi_score": round(score, 1), "level": level,
            "model_spread": round(max(r.sources.values()) - min(r.sources.values()), 2),
        })
    alerts.sort(key=lambda a: -a["rpi_score"])
    return {
        "as_of": latest_day.date().isoformat(),
        "note": "computed on the latest archived cycle of the 12-month record; "
                "refreshes automatically as new cycles are ingested",
        "levels": {name: thr for name, thr in RPI_LEVELS},
        "alerts": alerts,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def compute_all(blends_path: Path) -> dict:
    df = pd.read_json(blends_path, lines=True)
    for m in MODELS:
        df[m] = df.sources.apply(lambda s: s[m])
    test = df[df.split == "test"].copy()

    suite = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "mode": "REAL_12M",
            "blends_file": str(blends_path.name),
            "contexts": int(len(df)),
            "period": {"start": str(df.valid_time.min().date()),
                       "end": str(df.valid_time.max().date())},
            "splits": {k: int(v) for k, v in df.split.value_counts().items()},
            "test_start": str(test.valid_time.min().date()),
            "test_end": str(test.valid_time.max().date()),
        },
        "baselines": build_baselines(df, test),
        "hard_days": build_hard_days(test),
        "oracle": build_oracle(test),
        "ai_ablation": build_ai_ablation(test),
        "imd_classes": build_imd_classes(df, test),
        "trust_audit": build_trust_audit(test),
        "calibration_sanity": build_calibration_sanity(test),
        "crossovers": build_crossovers(df),
        "skill_evolution": build_skill_evolution(df),
        "bias_fingerprints": build_bias_fingerprints(df),
        "rpi": build_rpi(df),
    }
    return suite


def _sanitize(obj):
    """Replace NaN/Inf with None so the artifact is strict-JSON valid
    (JavaScript's JSON.parse rejects NaN even though Python emits it)."""
    import math
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize(v) for v in obj]
    return obj


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--blends", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    suite = compute_all(Path(args.blends))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(_sanitize(suite), indent=1, allow_nan=False))
    print(f"wrote {out} ({out.stat().st_size/1024:.0f} KB)")
    # headline summary for the console
    for v, r in suite["calibration_sanity"]["rows"].items():
        print(f"calibration {v}: raw {r['raw_blend_mae']} -> corrected {r['bias_corrected_mae']} ({r['improvement_pct']}%)")
    ov = suite["oracle"]["overall"]
    print(f"oracle recovery overall: {ov['oracle_advantage_recovered_pct']}%")
    for r in suite["baselines"]["rows"]:
        print(f"  {r['region']}/{r['variable']}: blend {r['mae']['blend']} vs "
              f"best single {r['best_single_model']} {r['mae'][r['best_single_model']]}")


if __name__ == "__main__":
    main()
