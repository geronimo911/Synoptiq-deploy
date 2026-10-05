"""Train model artifacts and fit calibration without using TEST observations."""
import json
import sys
import pathlib
import time
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd
import lightgbm as lgb
from sklearn.model_selection import TimeSeriesSplit

from app.database import SessionLocal
from app.blending.train_meta_model import train_all, get_global_split_boundaries, freeze_skill_table
from app.blending.blend import batch_blend
from app.blending.calibration import (
    apply_quantile_curve, apply_quantile_map, build_quantile_curve,
    fit_quantile_map, fit_exceedance_calibrator, load_quantile_map,
    MIN_SAMPLES_FOR_REGIONAL_FIT,
)
from app.data_prep import prepare_variable_frame, build_feature_frame, build_batch_blend_inputs
from app.config import (
    ACTIVE_MODEL_VERSION, CALIBRATION_DIR, MANIFEST_PATH, MODELS,
    PILOT_ZONES, THRESHOLDS, VARIABLES,
)


def _fit_fold_models(train_df: pd.DataFrame, variable: str) -> dict[str, object]:
    fitted = {}
    for model_name in MODELS:
        subset = train_df[train_df["model"] == model_name]
        if len(subset) < 50:
            return {}
        estimator = lgb.LGBMRegressor(
            n_estimators=150, max_depth=4, learning_rate=0.08,
            subsample=0.85, colsample_bytree=0.85, random_state=42, verbose=-1,
        )
        estimator.fit(
            build_feature_frame(subset, variable),
            -(subset["forecast_value"] - subset["observed_value"]).abs().to_numpy(),
        )
        fitted[model_name] = estimator
    return fitted


def _generate_oof_calibration_data(train_df: pd.DataFrame, variable: str) -> pd.DataFrame:
    columns = ["region", "valid_time", "fold", "raw_forecast", "calibrated_forecast", "truth"]
    valid_times = np.array(sorted(pd.to_datetime(train_df["valid_time"]).unique()))
    if len(valid_times) < 7:
        return pd.DataFrame(columns=columns)

    split_count = min(5, len(valid_times) - 1)
    splitter = TimeSeriesSplit(n_splits=split_count)
    prior_oof: list[dict] = []
    calibration_rows: list[dict] = []
    for fold, (train_indices, heldout_indices) in enumerate(splitter.split(valid_times), start=1):
        earlier_times = valid_times[train_indices]
        heldout_times = valid_times[heldout_indices]
        fold_train = train_df[pd.to_datetime(train_df["valid_time"]).isin(earlier_times)]
        fold_heldout = train_df[pd.to_datetime(train_df["valid_time"]).isin(heldout_times)]
        fold_models = _fit_fold_models(fold_train, variable)
        if not fold_models:
            continue

        features, values, truth, context = build_batch_blend_inputs(fold_heldout, variable)
        if not features or any(name not in fold_models for name in features):
            continue
        fallback_skill = {
            name: context["historical_skill_expanding"].to_numpy()
            for name in features
        }
        raw, _, _ = batch_blend(
            variable, features, values, fallback_skill, score_models=fold_models,
        )
        current_rows = [
            {"region": region, "valid_time": valid_time, "fold": fold,
             "raw_forecast": float(prediction), "truth": float(observed)}
            for region, valid_time, prediction, observed
            in zip(context["region"], context["valid_time"], raw, truth)
        ]

        global_curve = build_quantile_curve(
            np.asarray([row["raw_forecast"] for row in prior_oof]),
            np.asarray([row["truth"] for row in prior_oof]),
        ) if prior_oof else None
        region_curves = {}
        if global_curve is not None:
            prior_frame = pd.DataFrame(prior_oof)
            for region, group in prior_frame.groupby("region"):
                if len(group) >= MIN_SAMPLES_FOR_REGIONAL_FIT:
                    curve = build_quantile_curve(
                        group["raw_forecast"].to_numpy(), group["truth"].to_numpy(),
                    )
                    if curve is not None:
                        region_curves[region] = curve
            for row in current_rows:
                curve = region_curves.get(row["region"], global_curve)
                row["calibrated_forecast"] = float(
                    apply_quantile_curve(np.asarray([row["raw_forecast"]]), curve)[0]
                )
                calibration_rows.append(row)

        prior_oof.extend(current_rows)

    return pd.DataFrame(calibration_rows, columns=columns)


def _period(frame: pd.DataFrame) -> dict[str, str | None]:
    if frame.empty:
        return {"start": None, "end": None}
    times = pd.to_datetime(frame["valid_time"])
    return {"start": times.min().isoformat(), "end": times.max().isoformat()}


def _event_report(variable: str, threshold_key: str, threshold: float,
                  region: str | None, result: dict, source: str,
                  period: dict[str, str | None], out_of_fold: bool,
                  validation_counts: dict | None = None) -> dict:
    return {
        "variable": variable,
        "event": threshold_key,
        "threshold": threshold,
        "positive_count": result["positive_count"],
        "negative_count": result["negative_count"],
        "calibration_source": source,
        "calibration_period": period,
        "out_of_fold": out_of_fold,
        "validation_counts": validation_counts,
        "region": region or "global",
        "model_version": ACTIVE_MODEL_VERSION or None,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "fitted": result["fitted"],
        "status": "fitted" if result["fitted"] else
            "Insufficient real calibration events in the non-test calibration set.",
    }


def _calibrate_predictions(raw: np.ndarray, variable: str,
                           regions: np.ndarray) -> np.ndarray:
    return np.asarray([
        apply_quantile_map(float(value), variable, region=region)
        for value, region in zip(raw, regions)
    ])


def _manifest_validation_counts(db, manifest: dict) -> dict | None:
    validation_period = manifest.get("validation_period") or {}
    test_period = manifest.get("test_period") or {}
    if not validation_period.get("start") or not test_period.get("start"):
        return None
    start = pd.Timestamp(validation_period["start"])
    test_start = pd.Timestamp(test_period["start"])
    counts = {}
    for variable, event in (("temperature", "heatwave"), ("wind_speed", "gale")):
        frame = prepare_variable_frame(db, variable, start, test_start, before_time=test_start)
        validation = frame[frame["split"] == "val"]
        _, _, truth, context = build_batch_blend_inputs(validation, variable)
        labels = truth >= THRESHOLDS[variable][event]
        counts[variable] = {
            "positive": int(labels.sum()),
            "negative": int(len(labels) - labels.sum()),
            "regions": {},
        }
        for region in PILOT_ZONES:
            mask = context["region"].to_numpy() == region if len(context) else []
            region_labels = labels[mask] if len(labels) else []
            counts[variable]["regions"][region] = {
                "positive": int(region_labels.sum()) if len(region_labels) else 0,
                "negative": int(len(region_labels) - region_labels.sum()) if len(region_labels) else 0,
            }
    return counts


def fit_calibration_on_validation(db, variable: str, val_start, test_start):
    df = prepare_variable_frame(db, variable, val_start, test_start, before_time=test_start)
    if df.empty:
        print(f"  [{variable}] no data, skipping calibration.")
        return []
    val_df = df[df["split"] == "val"]
    train_df = df[df["split"] == "train"]
    records = []

    feats_by_model, values_by_model, truth, context_meta = build_batch_blend_inputs(val_df, variable)
    if feats_by_model and len(truth) >= 20:
        fallback_skill = {m: context_meta["historical_skill_expanding"].to_numpy() for m in feats_by_model}
        blended, _, source = batch_blend(variable, feats_by_model, values_by_model, fallback_skill)
        fit_quantile_map(blended, truth, variable, region=None)
        global_curve = load_quantile_map(variable, region=None)
        for region in val_df["region"].unique():
            region_val_df = val_df[val_df["region"] == region]
            r_feats, r_values, r_truth, r_ctx = build_batch_blend_inputs(region_val_df, variable)
            if not r_feats or len(r_truth) < MIN_SAMPLES_FOR_REGIONAL_FIT:
                continue
            r_fallback_skill = {m: r_ctx["historical_skill_expanding"].to_numpy() for m in r_feats}
            r_blended, _, _ = batch_blend(variable, r_feats, r_values, r_fallback_skill)
            fit_quantile_map(r_blended, r_truth, variable, region=region, shrink_toward=global_curve)
        calibrated = _calibrate_predictions(blended, variable, context_meta["region"].to_numpy())
        print(f"  [{variable}] validation quantile map fit on {len(blended)} contexts (weight source: {source}).")

        oof_frame = None
        for thresh_key, threshold in THRESHOLDS.get(variable, {}).items():
            if thresh_key == "unit":
                continue
            global_result = fit_exceedance_calibrator(
                calibrated, truth, variable, thresh_key, region=None,
            )
            validation_labels = truth >= threshold
            validation_counts = {
                "global": {
                    "positive": int(validation_labels.sum()),
                    "negative": int(len(validation_labels) - validation_labels.sum()),
                },
                "regions": {},
            }
            for region in PILOT_ZONES:
                region_mask = context_meta["region"].to_numpy() == region
                region_labels = validation_labels[region_mask]
                validation_counts["regions"][region] = {
                    "positive": int(region_labels.sum()),
                    "negative": int(len(region_labels) - region_labels.sum()),
                }
            if global_result["fitted"]:
                records.append(_event_report(
                    variable, thresh_key, threshold, None, global_result,
                    "real_validation", _period(context_meta), False, validation_counts,
                ))
                for region in val_df["region"].unique():
                    mask = context_meta["region"].to_numpy() == region
                    regional_result = fit_exceedance_calibrator(
                        calibrated[mask], truth[mask], variable, thresh_key, region=region,
                    )
                    records.append(_event_report(
                        variable, thresh_key, threshold, region, regional_result,
                        "real_validation", _period(context_meta.loc[mask]), False,
                        validation_counts,
                    ))
                continue

            if variable not in {"temperature", "wind_speed"}:
                records.append(_event_report(
                    variable, thresh_key, threshold, None, global_result,
                    "real_validation", _period(context_meta), False, validation_counts,
                ))
                continue

            if oof_frame is None:
                oof_frame = _generate_oof_calibration_data(train_df, variable)
            oof_result = fit_exceedance_calibrator(
                oof_frame["calibrated_forecast"].to_numpy(),
                oof_frame["truth"].to_numpy(), variable, thresh_key,
            )
            records.append(_event_report(
                variable, thresh_key, threshold, None, oof_result,
                "time_ordered_oof_train", _period(oof_frame), True, validation_counts,
            ))
            for region in PILOT_ZONES:
                regional_oof = oof_frame[oof_frame["region"] == region]
                regional_result = fit_exceedance_calibrator(
                    regional_oof["calibrated_forecast"].to_numpy(),
                    regional_oof["truth"].to_numpy(), variable, thresh_key, region=region,
                )
                records.append(_event_report(
                    variable, thresh_key, threshold, region, regional_result,
                    "time_ordered_oof_train", _period(regional_oof), True, validation_counts,
                ))
            print(f"  [{variable}/{thresh_key}] validation class safeguard failed; "
                  f"OOF non-test contexts={len(oof_frame)}.")

    return records


def write_calibration_report(records: list[dict], split_boundaries: dict | None = None,
                             manifest_validation_counts: dict | None = None) -> None:
    CALIBRATION_DIR.mkdir(exist_ok=True, parents=True)
    manifest = {}
    if MANIFEST_PATH.exists():
        try:
            manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            manifest = {}
    payload = {
        "model_version": ACTIVE_MODEL_VERSION or None,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "test_used_for_fitting": False,
        "database_split_boundaries": split_boundaries,
        "manifest_validation_period": manifest.get("validation_period"),
        "manifest_test_period": manifest.get("test_period"),
        "manifest_validation_event_counts": manifest_validation_counts,
        "calibrators": records,
        "test_evaluation": {"status": "pending post-freeze evaluation"},
    }
    json_path = CALIBRATION_DIR / "calibration_report.json"
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    lines = [
        "# Event Calibration Report", "",
        f"Model version: {payload['model_version'] or 'not set'}", "",
        f"Manifest validation period: {payload['manifest_validation_period'] or 'not available'}",
        f"Manifest TEST period: {payload['manifest_test_period'] or 'not available'}", "",
        f"Database split boundaries: {split_boundaries or 'not available'}", "",
        "TEST observations are excluded from fitting. Isotonic fitting requires at least "
        "5 positive and 5 negative real events.", "",
        "Manifest-window validation event counts:", "",
        "| Variable | Global + | Global - | KWG + / - | BOB + / - | IGP + / - |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for variable, counts in (manifest_validation_counts or {}).items():
        regions = counts["regions"]
        lines.append(
            f"| {variable} | {counts['positive']} | {counts['negative']} | "
            f"{regions['kerala_western_ghats']['positive']} / {regions['kerala_western_ghats']['negative']} | "
            f"{regions['bay_of_bengal_east_coast']['positive']} / {regions['bay_of_bengal_east_coast']['negative']} | "
            f"{regions['indo_gangetic_plains']['positive']} / {regions['indo_gangetic_plains']['negative']} |"
        )
    lines.extend([
        "", "| Variable | Event | Threshold | Scope | Fit + | Fit - | Validation + | Validation - | Source | OOF | Status |",
        "|---|---|---:|---|---:|---:|---:|---:|---|---|---|",
    ])
    for record in records:
        counts = record.get("validation_counts") or {}
        if record["region"] == "global":
            validation_counts = counts.get("global", {})
        else:
            validation_counts = counts.get("regions", {}).get(record["region"], {})
        lines.append(
            f"| {record['variable']} | {record['event']} | {record['threshold']:g} | "
            f"{record['region']} | {record['positive_count']} | {record['negative_count']} | "
            f"{validation_counts.get('positive', 'n/a')} | {validation_counts.get('negative', 'n/a')} | "
            f"{record['calibration_source']} | {str(record['out_of_fold']).lower()} | "
            f"{'fitted' if record['fitted'] else 'withheld'} |"
        )
    lines.extend(["", "No fit-set Brier/reliability metrics are reported because the same "
                  "observations are used to fit the calibrator.", ""])
    (CALIBRATION_DIR / "calibration_report.md").write_text("\n".join(lines), encoding="utf-8")


def main():
    t0 = time.time()
    db = SessionLocal()

    print("Training per-source skill-score models + bust classifiers (TRAIN split only)...")
    report = train_all(db)
    for var, models in report["variables"].items():
        print(f"  {var}: {models}")
    print(f"  split boundaries -> val_start={report['val_start']}  test_start={report['test_start']}")
    t1 = time.time()
    print(f"  [training time: {t1 - t0:.1f}s]")

    print("Fitting quantile and event calibration without reading TEST rows...")
    from app.blending.train_meta_model import get_global_split_boundaries
    val_start, test_start = get_global_split_boundaries(db)
    calibration_records = []
    for variable in VARIABLES:
        calibration_records.extend(fit_calibration_on_validation(db, variable, val_start, test_start))
    try:
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8")) if MANIFEST_PATH.exists() else {}
    except (OSError, json.JSONDecodeError):
        manifest = {}
    write_calibration_report(
        calibration_records,
        {"val_start": str(val_start), "test_start": str(test_start)},
        _manifest_validation_counts(db, manifest),
    )
    t2 = time.time()
    print(f"  [calibration time: {t2 - t1:.1f}s]")

    print("Freezing skill table from train+validation only (valid_time < test_start)...")
    n = freeze_skill_table(db, test_start)
    print(f"  wrote {n} frozen skill-table rows, built_through={test_start}")

    db.close()
    print(f"Done. Total time: {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
