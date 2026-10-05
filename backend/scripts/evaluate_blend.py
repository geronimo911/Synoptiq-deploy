"""
Held-out temporal evaluation (Section 13 step 15, Section 21 headline proof
point) — LEAKAGE-FREE VERSION.

Everything used here was frozen before this script runs: the per-source
meta-models and bust classifiers (trained on TRAIN only), the calibration
(fit on VALIDATION only, using the frozen meta-model's own predictions), and
the skill table (built from TRAIN+VALIDATION only). This script itself only
ever reads TEST-split rows, and the "historical_skill_expanding"/
"climatological_anomaly_norm" features those rows carry were computed via
groupby().expanding().shift(1) — i.e. every TEST row's features are built
only from what was already known strictly before that row's own valid_time,
never from the test outcome being scored or from anything after it.

Writes artifacts/blend_eval_<region>_<variable>.json, which
/verification/scorecard reads — so the API only ever reports a number this
script actually measured, never a placeholder.
"""
import sys, pathlib, json, time
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import numpy as np
from app.database import SessionLocal
from app.data_prep import prepare_variable_frame, build_batch_blend_inputs
from app.blending.train_meta_model import get_global_split_boundaries
from app.blending.blend import batch_blend
from app.blending.calibration import (
    apply_quantile_map, exceedance_calibration_source, exceedance_probabilities,
)
from app.skill_engine import _csi_pod_far
from app.config import (
    ACTIVE_METRICS_DIR, ACTIVE_MODEL_VERSION, CALIBRATION_DIR,
    PILOT_ZONES, THRESHOLDS,
)


def evaluate(db, region: str, val_start, test_start, variable: str):
    df = prepare_variable_frame(db, variable, val_start, test_start)
    if df.empty:
        return None
    test_df = df[(df["split"] == "test") & (df["region"] == region)]
    if test_df.empty:
        return None

    feats_by_model, values_by_model, truth, context_meta = build_batch_blend_inputs(test_df, variable)
    if not feats_by_model or len(truth) < 10:
        return None

    fallback_skill = {m: context_meta["historical_skill_expanding"].to_numpy() for m in feats_by_model}
    blended_raw, _, source = batch_blend(variable, feats_by_model, values_by_model, fallback_skill)
    blended_calibrated = np.array([apply_quantile_map(v, variable, region=region) for v in blended_raw])

    result = {
        "region": region,
        "variable": variable,
        "n_test_contexts": int(len(truth)),
        "weight_source": source,
        "split_boundaries": {"val_start": str(val_start), "test_start": str(test_start)},
    }

    if variable in {"temperature", "wind_speed"}:
        threshold_key = next(key for key in THRESHOLDS[variable] if key != "unit")
        threshold = THRESHOLDS[variable][threshold_key]
        source = exceedance_calibration_source(variable, threshold_key, region)
        probabilities = [
            exceedance_probabilities(value, variable, region).get(threshold_key)
            for value in blended_calibrated
        ]
        labels = (truth >= threshold).astype(float)
        if source is not None and all(probability is not None for probability in probabilities):
            scores = np.asarray(probabilities, dtype=float)
            reliability = []
            for lower, upper in zip(np.linspace(0.0, 1.0, 11)[:-1], np.linspace(0.0, 1.0, 11)[1:]):
                mask = (scores >= lower) & ((scores < upper) | ((upper == 1.0) & (scores <= upper)))
                if mask.any():
                    reliability.append({
                        "lower": float(lower), "upper": float(upper), "count": int(mask.sum()),
                        "mean_predicted_probability": float(scores[mask].mean()),
                        "observed_event_frequency": float(labels[mask].mean()),
                    })
            result["event_calibration"] = {
                "variable": variable,
                "event": threshold_key,
                "threshold": threshold,
                "calibration_source": source,
                "n_test": int(len(labels)),
                "positive_count": int(labels.sum()),
                "negative_count": int(len(labels) - labels.sum()),
                "brier_score": float(np.mean((scores - labels) ** 2)),
                "event_frequency": float(labels.mean()),
                "mean_predicted_probability": float(scores.mean()),
                "expected_calibration_error": float(sum(
                    bucket["count"] / len(labels) * abs(
                        bucket["mean_predicted_probability"] - bucket["observed_event_frequency"]
                    ) for bucket in reliability
                )),
                "reliability": reliability,
            }
        else:
            result["event_calibration"] = {
                "variable": variable,
                "event": threshold_key,
                "threshold": threshold,
                "calibration_source": None,
                "n_test": int(len(labels)),
                "positive_count": int(labels.sum()),
                "negative_count": int(len(labels) - labels.sum()),
                "brier_score": None,
                "event_frequency": float(labels.mean()),
                "mean_predicted_probability": None,
                "expected_calibration_error": None,
                "reliability": [],
                "status": "No fitted real calibrator; probability metrics unavailable.",
            }

    model_errors = {}
    if variable == "precipitation":
        thresh = THRESHOLDS[variable]["heavy"]
        synoptiq_csi, _, _ = _csi_pod_far(blended_calibrated, truth, thresh)
        per_model = {}
        for m, preds in values_by_model.items():
            csi_m, _, _ = _csi_pod_far(preds, truth, thresh)
            per_model[m] = {
                "csi": float(csi_m) if not np.isnan(csi_m) else 0.0,
                "mae": float(np.mean(np.abs(preds - truth))),
                "rmse": float(np.sqrt(np.mean((preds - truth) ** 2))),
            }
        best_model = max(per_model, key=lambda model: per_model[model]["csi"])
        best_single = per_model[best_model]["csi"]
        synoptiq_csi = 0.0 if np.isnan(synoptiq_csi) else float(synoptiq_csi)
        rel_improve = ((synoptiq_csi - best_single) / best_single * 100.0) if best_single > 0 else 0.0
        naive = np.mean(np.vstack(list(values_by_model.values())), axis=0)
        result.update({
            "metric": f"CSI@{thresh:g}mm",
            "best_single_model": best_model,
            "best_single_model_csi": round(best_single, 4),
            "synoptiq_csi": round(synoptiq_csi, 4),
            "synoptiq_mae": round(float(np.mean(np.abs(blended_calibrated - truth))), 4),
            "synoptiq_rmse": round(float(np.sqrt(np.mean((blended_calibrated - truth) ** 2))), 4),
            "per_model_metrics": per_model,
            "naive_average_mae": round(float(np.mean(np.abs(naive - truth))), 4),
            "naive_average_rmse": round(float(np.sqrt(np.mean((naive - truth) ** 2))), 4),
            "relative_csi_improvement_pct": round(float(rel_improve), 2),
            "fss_metric_type": "not applicable at this threshold summary; FSS-proxy remains diagnostic only",
        })
    else:
        blend_rmse = float(np.sqrt(np.mean((blended_calibrated - truth) ** 2)))
        per_model = {
            m: {
                "mae": float(np.mean(np.abs(preds - truth))),
                "rmse": float(np.sqrt(np.mean((preds - truth) ** 2))),
                "bias": float(np.mean(preds - truth)),
            }
            for m, preds in values_by_model.items()
        }
        best_model = min(per_model, key=lambda model: per_model[model]["rmse"])
        best_single_rmse = per_model[best_model]["rmse"]
        rel_improve = ((best_single_rmse - blend_rmse) / best_single_rmse * 100.0) if best_single_rmse > 0 else 0.0
        naive = np.mean(np.vstack(list(values_by_model.values())), axis=0)
        result.update({
            "metric": "RMSE",
            "best_single_model": best_model,
            "best_single_model_rmse": round(best_single_rmse, 3),
            "synoptiq_rmse": round(blend_rmse, 3),
            "synoptiq_mae": round(float(np.mean(np.abs(blended_calibrated - truth))), 3),
            "synoptiq_bias": round(float(np.mean(blended_calibrated - truth)), 3),
            "per_model_metrics": per_model,
            "naive_average_mae": round(float(np.mean(np.abs(naive - truth))), 3),
            "naive_average_rmse": round(float(np.sqrt(np.mean((naive - truth) ** 2))), 3),
            "relative_rmse_improvement_pct": round(float(rel_improve), 2),
        })

    ACTIVE_METRICS_DIR.mkdir(exist_ok=True, parents=True)
    out_path = ACTIVE_METRICS_DIR / f"blend_eval_{region}_{variable}.json"
    out_path.write_text(json.dumps(result, indent=2))
    return result


def _write_calibration_evaluation(results: list[dict], test_start) -> None:
    report_path = CALIBRATION_DIR / "calibration_report.json"
    if report_path.exists():
        report = json.loads(report_path.read_text(encoding="utf-8"))
    else:
        report = {"model_version": ACTIVE_MODEL_VERSION or None, "calibrators": []}
    report["test_evaluation"] = {
        "status": "complete",
        "data_split": "untouched_test_after_calibrators_frozen",
        "test_start": str(test_start),
        "events": [
            {"region": item["region"], **item["event_calibration"]}
            for item in results if item.get("event_calibration") is not None
        ],
    }
    CALIBRATION_DIR.mkdir(exist_ok=True, parents=True)
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    markdown_path = CALIBRATION_DIR / "calibration_report.md"
    prior_markdown = markdown_path.read_text(encoding="utf-8") if markdown_path.exists() else "# Event Calibration Report\n"
    prior_markdown = prior_markdown.split("\n## Untouched TEST Evaluation", 1)[0]
    lines = ["", "## Untouched TEST Evaluation", "", f"TEST starts at {test_start}; calibrators were frozen before scoring.", ""]
    lines.append("| Region | Variable | Event | N | Brier | Event frequency | Mean predicted | ECE | Status |")
    lines.append("|---|---|---|---:|---:|---:|---:|---:|---|")

    def format_metric(value):
        return "n/a" if value is None else value

    for event in report["test_evaluation"]["events"]:
        lines.append(
            f"| {event['region']} | {event.get('variable', '')} | {event['event']} | "
            f"{event['n_test']} | {format_metric(event.get('brier_score'))} | "
            f"{format_metric(event.get('event_frequency'))} | "
            f"{format_metric(event.get('mean_predicted_probability'))} | "
            f"{format_metric(event.get('expected_calibration_error'))} | "
            f"{event.get('status', 'scored')} |"
        )
    markdown_path.write_text(prior_markdown.rstrip() + "\n" + "\n".join(lines) + "\n", encoding="utf-8")


def main():
    t0 = time.time()
    db = SessionLocal()
    val_start, test_start = get_global_split_boundaries(db)
    print(f"Evaluating on TEST split only (valid_time >= {test_start}).")
    results = []
    for region in PILOT_ZONES:
        for variable in ("precipitation", "temperature", "wind_speed"):
            r = evaluate(db, region, val_start, test_start, variable)
            if not r:
                print(f"{region} [{variable}]: not enough held-out test samples to evaluate.")
                continue
            results.append(r)
            if variable == "precipitation":
                print(
                    f"{region} [{variable}]: Synoptiq CSI@50mm={r['synoptiq_csi']} vs "
                    f"{r['best_single_model']}={r['best_single_model_csi']} "
                    f"({r['relative_csi_improvement_pct']:+.1f}%, n={r['n_test_contexts']})"
                )
            else:
                print(
                    f"{region} [{variable}]: Synoptiq RMSE={r['synoptiq_rmse']} vs "
                    f"{r['best_single_model']}={r['best_single_model_rmse']} "
                    f"({r['relative_rmse_improvement_pct']:+.1f}%, n={r['n_test_contexts']})"
                )
    _write_calibration_evaluation(results, test_start)
    print(f"[evaluation time: {time.time() - t0:.1f}s]")
    db.close()


if __name__ == "__main__":
    main()
