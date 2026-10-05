from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
from dotenv import dotenv_values

sys.path.insert(0, str(Path(__file__).resolve().parent / "realdata"))
from window import (PROTOTYPE_END, PROTOTYPE_START, TRAINING_END, TRAINING_START,
                    validate_prototype_window, validate_window)

_env_values = {}
for _env_path in (ROOT / "backend" / ".env", ROOT / ".env"):
    for _key, _value in dotenv_values(_env_path).items():
        if _value is not None and _value.strip():
            _env_values[_key] = _value
for _key, _value in _env_values.items():
    if not os.getenv(_key, "").strip():
        os.environ[_key] = _value
BACKEND = ROOT / "backend"
REAL = ROOT / "training" / "realdata"
CACHE = ROOT / "data" / "real" / "cache"
ARTIFACTS = ROOT / "artifacts"
MODEL_VERSION = f"synoptiq-real-{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}"
COMMON_DATA_START = TRAINING_START
VERSION_ROOT = ARTIFACTS / "real" / "prototype" / MODEL_VERSION
MANIFEST_ROOT = ARTIFACTS / "real" / "manifests" / MODEL_VERSION
METRICS_ROOT = ARTIFACTS / "real" / "metrics" / MODEL_VERSION
MODELS = ROOT / "models" / "real"
CAL = ARTIFACTS / "real" / "calibration"
DB_PATH = ROOT / "data" / "real" / "training.sqlite3"
BACKUP_ROOT = ROOT / "training" / "_backup_before_real"


def run(cmd: list[str], cwd: Path, env: dict[str, str] | None = None) -> None:
    print("\n> " + " ".join(cmd), flush=True)
    merged = os.environ.copy()
    if env:
        merged.update(env)
    subprocess.run(cmd, cwd=str(cwd), env=merged, check=True)


def py(script: Path, *args: str, env: dict[str, str] | None = None) -> None:
    run([sys.executable, str(script), *args], ROOT, env=env)


def validate_runtime_configuration(ecmwf_source: str, production_url: str) -> None:
    missing = []
    has_earthdata_token = bool(os.getenv("EARTHDATA_TOKEN", "").strip())
    has_earthdata_credentials = bool(os.getenv("NASA_EARTHDATA_USERNAME", "").strip()
                                     and os.getenv("NASA_EARTHDATA_PASSWORD", "").strip())
    if not has_earthdata_token and not has_earthdata_credentials:
        missing.append("EARTHDATA_TOKEN (or NASA_EARTHDATA_USERNAME/NASA_EARTHDATA_PASSWORD)")
    if ecmwf_source == "mars":
        for name in ("ECMWF_API_KEY", "ECMWF_API_EMAIL"):
            if not os.getenv(name, "").strip():
                missing.append(name)
    if missing:
        raise SystemExit("Real-data configuration errors: " + ", ".join(missing))
    if os.getenv("SYNOPTIQ_MODE", "real").strip().lower() in {"real", "live"} and not production_url:
        raise SystemExit("DATABASE_URL is required when production LIVE configuration is enabled")


def validate_training_table() -> dict:
    import pandas as pd

    fpath = CACHE / "training" / "forecasts.parquet"
    tpath = CACHE / "training" / "truth.parquet"
    if not fpath.exists() or not tpath.exists():
        raise SystemExit("Training parquet files were not created.")

    fdf = pd.read_parquet(fpath)
    tdf = pd.read_parquet(tpath)
    required_models = {"GFS", "IFS", "AIFS"}
    required_vars = {"precipitation", "temperature", "wind_speed"}

    if set(fdf["model"].unique()) != required_models:
        raise SystemExit(f"Forecast model validation failed: {sorted(fdf['model'].unique())}")
    if set(fdf["variable"].unique()) != required_vars:
        raise SystemExit(f"Forecast variable validation failed: {sorted(fdf['variable'].unique())}")
    if tdf.empty or set(tdf["variable"].unique()) != required_vars:
        raise SystemExit("Truth table is incomplete; real IMERG/ERA5 verification data is required.")
    if fdf["forecast_value"].isna().any() or tdf["observed_value"].isna().any():
        raise SystemExit("NaN values remain in the training tables.")
    if (fdf.query("variable == 'precipitation'")["forecast_value"] < 0).any():
        raise SystemExit("Negative precipitation forecasts detected.")
    if (tdf.query("variable == 'precipitation'")["observed_value"] < 0).any():
        raise SystemExit("Negative precipitation truth detected.")

    print(f"COMMON_TRAIN_START={pd.to_datetime(fdf['valid_time']).min().date().isoformat()}")
    print(f"COMMON_TRAIN_END={pd.to_datetime(fdf['valid_time']).max().date().isoformat()}")
    print(f"TOTAL_ROWS={len(fdf)}")
    for label, column in (("ROWS_PER_SOURCE", "model"), ("ROWS_PER_REGION", "region"),
                          ("ROWS_PER_VARIABLE", "variable"), ("ROWS_PER_LEAD", "lead_hours")):
        print(label)
        print(fdf.groupby(column).size().to_string())

    return {
        "forecast_rows": int(len(fdf)),
        "truth_rows": int(len(tdf)),
        "start": str(pd.to_datetime(fdf["valid_time"]).min()),
        "end": str(pd.to_datetime(fdf["valid_time"]).max()),
        "models": sorted(fdf["model"].unique().tolist()),
        "variables": sorted(fdf["variable"].unique().tolist()),
        "model_generations": {
            model: sorted(group["model_generation"].dropna().astype(str).unique().tolist())
            for model, group in fdf.groupby("model")
            if "model_generation" in group.columns
        },
    }


def backup_current_state() -> Path:
    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    backup = BACKUP_ROOT / stamp
    backup.mkdir(parents=True, exist_ok=False)
    if MODELS.exists():
        shutil.copytree(MODELS, backup / "models")
    if CAL.exists():
        shutil.copytree(CAL, backup / "calibration")
    for p in METRICS_ROOT.glob("blend_eval_*.json"):
        shutil.copy2(p, backup / p.name)
    if (MANIFEST_ROOT / "manifest.json").exists():
        shutil.copy2(MANIFEST_ROOT / "manifest.json", backup / "manifest.json")
    if DB_PATH.exists():
        shutil.copy2(DB_PATH, backup / "synoptiq.db")
    return backup


def restore_backup(backup: Path) -> None:
    print(f"Restoring previous state from {backup}", flush=True)
    if MODELS.exists():
        shutil.rmtree(MODELS)
    if CAL.exists():
        shutil.rmtree(CAL)
    models_backup = backup / "models"
    cal_backup = backup / "calibration"
    if models_backup.exists():
        shutil.copytree(models_backup, MODELS)
    if cal_backup.exists():
        shutil.copytree(cal_backup, CAL)
    for p in METRICS_ROOT.glob("blend_eval_*.json"):
        p.unlink()
    for p in backup.glob("blend_eval_*.json"):
        shutil.copy2(p, METRICS_ROOT / p.name)
    manifest_backup = backup / "manifest.json"
    manifest_target = MANIFEST_ROOT / "manifest.json"
    if manifest_target.exists():
        manifest_target.unlink()
    if manifest_backup.exists():
        shutil.copy2(manifest_backup, manifest_target)
    db_backup = backup / "synoptiq.db"
    if db_backup.exists():
        shutil.copy2(db_backup, DB_PATH)


def discard_backup(backup: Path) -> None:
    shutil.rmtree(backup, ignore_errors=True)
    try:
        if BACKUP_ROOT.exists() and not any(BACKUP_ROOT.iterdir()):
            BACKUP_ROOT.rmdir()
    except OSError:
        pass


def cleanup_synthetic_production() -> None:
    """Keep fake/demo material intact; mode-specific runtime roots are isolated."""


def write_manifest(stats: dict, evaluation: list[dict], prototype: bool = False) -> None:
    split = evaluation[0].get("split_boundaries", {}) if evaluation else {}
    try:
        git_commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        git_commit = None
    manifest = {
        "data_mode": "real",
        "training_mode": "REAL_PROTOTYPE" if prototype else "REAL_FULL",
        "history_label": "LIMITED_HISTORY" if prototype else "FULL_HISTORY",
        "calibration_status": "INSUFFICIENT_REAL_SAMPLE_SIZE" if prototype else "FIT_OR_FALLBACK",
        "model_version": MODEL_VERSION,
        "training_completed_utc": datetime.utcnow().isoformat() + "Z",
        "forecast_sources": ["GFS", "IFS", "AIFS"],
        "truth_sources": ["IMERG", "ERA5"],
        "forecast_rows": stats["forecast_rows"],
        "truth_rows": stats["truth_rows"],
        "training_period": {"start": stats["start"], "end": stats["end"]},
        "training_window_days": (pd.to_datetime(stats["end"]).date() - pd.to_datetime(stats["start"]).date()).days + 1,
        "synthetic_data_used": False,
        "validation_period": {"start": split.get("val_start"), "end": split.get("test_start")},
        "test_period": {"start": split.get("test_start"), "end": stats["end"]},
        "dataset_version": f"parquet-{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}",
        "feature_schema": "backend.app.blending.features.FEATURE_NAMES",
        "source_models": ["GFS", "IFS", "AIFS"],
        "model_generations": stats.get("model_generations", {}),
        "models": stats["models"],
        "variables": stats["variables"],
        "evaluation_metrics": evaluation,
        "git_commit": git_commit,
        "regions": ["kerala_western_ghats", "bay_of_bengal_east_coast", "indo_gangetic_plains"],
        "lead_hours": [24, 48, 72, 96, 120],
        "notes": "Real GFS/IFS/AIFS forecasts aligned with IMERG rainfall and ERA5 temperature/wind verification.",
    }
    VERSION_ROOT.mkdir(parents=True, exist_ok=True)
    MANIFEST_ROOT.mkdir(parents=True, exist_ok=True)
    (MANIFEST_ROOT / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (VERSION_ROOT / "evaluation.json").write_text(json.dumps(evaluation, indent=2), encoding="utf-8")
    (VERSION_ROOT / "metrics.json").write_text(json.dumps(evaluation, indent=2), encoding="utf-8")


def validate_artifacts(evaluation: list[dict]) -> None:
    expected = [f"skillscore_{variable}_{model}.joblib" for variable in
                ("precipitation", "temperature", "wind_speed") for model in ("GFS", "IFS", "AIFS")]
    expected += [f"bust_{variable}.joblib" for variable in ("precipitation", "temperature", "wind_speed")]
    missing = [name for name in expected if not ((MODELS / "skill" / name) if name.startswith("skillscore_") else (MODELS / "bust" / name)).exists()]
    if missing:
        raise SystemExit("Real artifact validation failed; missing: " + ", ".join(missing))
    if len(evaluation) != 9:
        raise SystemExit(f"Real evaluation must contain 9 region/variable reports, found {len(evaluation)}")
    if not list(CAL.glob("*.joblib")):
        raise SystemExit("Real artifact validation failed; no calibration artifacts were created.")


def main() -> None:
    ap = argparse.ArgumentParser(description="Run the complete real-data Synoptiq training pipeline on Windows.")
    ap.add_argument("--start", default=COMMON_DATA_START.isoformat())
    ap.add_argument("--end", default=TRAINING_END.isoformat())
    ap.add_argument("--hour", type=int, default=0, choices=[0])
    source_default = os.getenv("ECMWF_SOURCE_MODE", "aws").strip().lower()
    if source_default == "historical":
        source_default = "aws"
    ap.add_argument("--ecmwf-source", choices=["aws", "operational", "mars"],
                    default=source_default)
    ap.add_argument("--smoke-only", action="store_true", help="Fetch/build/validate real data only; do not replace models.")
    ap.add_argument("--skip-download", action="store_true", help="Use existing raw/provider caches; fail if required caches are absent.")
    ap.add_argument("--force-rebuild", action="store_true", help="Rebuild processed Parquet and training database from raw caches.")
    ap.add_argument("--real-prototype", action="store_true",
                    help="Run only the fixed real-data prototype window; never implied by defaults.")
    args = ap.parse_args()

    if args.real_prototype:
        args.start, args.end = PROTOTYPE_START.isoformat(), PROTOTYPE_END.isoformat()
        validate_prototype_window(args.start, args.end)
    else:
        validate_window(args.start, args.end)

    training_url = os.getenv("TRAINING_DATABASE_URL", "").strip()
    production_url = os.getenv("DATABASE_URL", "").strip()
    if not training_url:
        raise SystemExit("TRAINING_DATABASE_URL is required; training never uses DATABASE_URL")
    if production_url and training_url == production_url:
        raise SystemExit("TRAINING_DATABASE_URL must not equal DATABASE_URL")
    validate_runtime_configuration(args.ecmwf_source, production_url)

    env = {
        "SYNOPTIQ_BACKEND": str(BACKEND),
        "SYNOPTIQ_MODE": "real",
        "SYNOPTIQ_DATABASE_ROLE": "TRAINING",
        "SYNOPTIQ_MODEL_VERSION": MODEL_VERSION,
    }
    print(f"ROOT={ROOT}")
    print(f"BACKEND={BACKEND}")
    print(f"REAL DATA CACHE={CACHE}")

    if not args.skip_download:
        if args.real_prototype:
            py(REAL / "fetch_openmeteo.py", "--start", args.start, "--end", args.end,
                    "--models", "gfs,ifs,aifs-single", "--real-prototype", env=env)
        else:
            py(REAL / "fetch_gfs.py", "--start", args.start, "--end", args.end,
               "--hour", str(args.hour), env=env)
            py(REAL / "fetch_openmeteo.py", "--start", args.start, "--end", args.end,
               "--models", "ifs,aifs-single", env=env)
        py(REAL / "fetch_imerg.py", "--start", args.start, "--end", args.end, env=env)

    months = []
    start = datetime.strptime(args.start, "%Y-%m-%d").date().replace(day=1)
    end = datetime.strptime(args.end, "%Y-%m-%d").date().replace(day=1)
    cur = start
    while cur <= end:
        months.append(f"{cur.year:04d}-{cur.month:02d}")
        if cur.month == 12:
            cur = cur.replace(year=cur.year + 1, month=1, day=1)
        else:
            cur = cur.replace(month=cur.month + 1, day=1)
    if not args.skip_download:
        py(REAL / "fetch_era5.py", "--start", args.start, "--end", args.end, env=env)
    if args.force_rebuild or not args.skip_download:
        build_args = ["--start", args.start, "--end", args.end, "--hour", str(args.hour)]
        if args.real_prototype:
            build_args.append("--real-prototype")
        py(REAL / "build_training_table.py", *build_args, env=env)

    stats = validate_training_table()
    print(json.dumps(stats, indent=2), flush=True)

    if args.smoke_only:
        print("SMOKE TEST PASSED: real GFS + IFS + AIFS + IMERG + ERA5 are aligned and usable.")
        return

    backup = backup_current_state()
    try:
        py(REAL / "load_into_db.py", env=env)
        py(BACKEND / "scripts" / "train_model.py", env=env)
        py(BACKEND / "scripts" / "evaluate_blend.py", env=env)
        py(BACKEND / "scripts" / "precompute_replay_cache.py", env=env)
        evaluation = []
        for result_path in METRICS_ROOT.glob("blend_eval_*.json"):
            evaluation.append(json.loads(result_path.read_text(encoding="utf-8")))
        if len(evaluation) < 9:
            raise SystemExit("Untouched real test evaluation did not produce all required reports.")
        validate_artifacts(evaluation)
        write_manifest(stats, evaluation, prototype=args.real_prototype)
        if not args.real_prototype:
            cleanup_synthetic_production()
        discard_backup(backup)
        print("REAL TRAINING COMPLETE: real artifacts promoted; fake/demo artifacts retained.")
    except Exception:
        restore_backup(backup)
        print("REAL TRAINING FAILED: previous synthetic/prototype state restored.")
        raise


if __name__ == "__main__":
    main()
