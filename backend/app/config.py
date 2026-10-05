"""Runtime configuration for the Synoptiq API and training contract."""
import os
from pathlib import Path
from dotenv import dotenv_values


def _load_local_environment() -> None:
    project_root = Path(__file__).resolve().parents[2]
    merged: dict[str, str] = {}
    for path in (project_root / "backend" / ".env", project_root / ".env"):
        for key, value in dotenv_values(path).items():
            if value is not None and value.strip():
                merged[key] = value
    for key, value in merged.items():
        if not os.getenv(key, "").strip():
            os.environ[key] = value


_load_local_environment()


def _env_flag(name: str, default: bool) -> bool:
    """Read a boolean environment flag without ever raising on bad input."""
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    return str(raw).strip().lower() not in {"0", "false", "no", "off"}


def _env_int(name: str, default: int) -> int:
    """Read an int environment value, falling back to the default on bad input."""
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


BASE_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BASE_DIR.parent
ARTIFACTS_BASE_DIR = PROJECT_ROOT / "artifacts"
REQUESTED_MODE = os.getenv("SYNOPTIQ_MODE", "real").strip().lower()
_MODE_ALIASES = {"live": "real", "demo": "fake"}
RUNTIME_MODE = _MODE_ALIASES.get(REQUESTED_MODE, REQUESTED_MODE)
if RUNTIME_MODE not in {"real", "fake"}:
    raise RuntimeError("SYNOPTIQ_MODE must be real or fake")


def get_runtime_mode() -> str:
    return RUNTIME_MODE


APP_MODE = "LIVE" if RUNTIME_MODE == "real" else "DEMO"
DATA_DIR = PROJECT_ROOT / "data" / RUNTIME_MODE
_default_artifacts_dir = ARTIFACTS_BASE_DIR / (
    "real_12m_aifs_corrected"
    if RUNTIME_MODE == "real" and (ARTIFACTS_BASE_DIR / "real_12m_aifs_corrected").exists()
    else RUNTIME_MODE
)
ARTIFACTS_DIR = Path(os.getenv("SYNOPTIQ_ARTIFACTS_DIR", str(_default_artifacts_dir))).resolve()
MODELS_DIR = Path(os.getenv("SYNOPTIQ_MODELS_DIR", str(PROJECT_ROOT / "models" / RUNTIME_MODE))).resolve()
CALIBRATION_DIR = Path(os.getenv("SYNOPTIQ_CALIBRATION_DIR", str(ARTIFACTS_DIR / "calibration"))).resolve()
METRICS_DIR = Path(os.getenv("SYNOPTIQ_METRICS_DIR", str(ARTIFACTS_DIR / "metrics"))).resolve()
PROTOTYPE_DIR = ARTIFACTS_DIR / "prototype"
MANIFESTS_DIR = ARTIFACTS_DIR / "manifests"
DB_PATH = PROJECT_ROOT / "data" / "fake" / "demo" / "synoptiq.db"
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
TRAINING_DATABASE_URL = os.getenv("TRAINING_DATABASE_URL", "").strip()
DATABASE_ROLE = os.getenv("SYNOPTIQ_DATABASE_ROLE", "PRODUCTION").upper()
ACTIVE_MODEL_VERSION = (
    os.getenv("SYNOPTIQ_MODEL_VERSION", os.getenv("MODEL_VERSION", "")).strip()
    if RUNTIME_MODE == "real" else ""
)
LIVE_REFRESH_MINUTES = _env_int("SYNOPTIQ_LIVE_REFRESH_MINUTES", 15)
# Fallback freshness window for a live provider whose model is not in the
# per-model cycle table (see api_v1.MODEL_FRESHNESS_MINUTES). Freshness is
# normally derived from the provider's real cycle cadence (GFS/IFS 6 h + grace,
# AIFS 12 h + grace), NOT from this value: a fixed short window marks current
# data stale whenever the site has been quiet, which is normal for a
# demand-driven deployment.
LIVE_FRESHNESS_MINUTES = _env_int("SYNOPTIQ_LIVE_FRESHNESS_MINUTES", 45)

# --- Refresh policy (production: demand-driven, no scheduled cron) -----------
# SYNOPTIQ_AUTO_REFRESH=0 disables the in-process background scheduler entirely;
# the deployment then refreshes only when a client asks (refresh-if-stale).
AUTO_REFRESH = _env_flag("SYNOPTIQ_AUTO_REFRESH", True)
# Enables POST /api/v1/system/refresh-if-stale.
REFRESH_ON_REQUEST = _env_flag("SYNOPTIQ_REFRESH_ON_REQUEST", True)
# A refresh is only attempted when the last successful cycle is older than this.
# This is a freshness THRESHOLD, not a scheduled interval.
REFRESH_MIN_AGE_MINUTES = _env_int("SYNOPTIQ_REFRESH_MIN_AGE_MINUTES", LIVE_REFRESH_MINUTES)
LIVE_DATA_DIR = Path(os.getenv("SYNOPTIQ_LIVE_DATA_DIR", str(PROJECT_ROOT / "data" / "real" / "live"))).resolve()
ECMWF_API_URL = os.getenv("ECMWF_API_URL", "https://api.ecmwf.int/v1").strip()
ACTIVE_METRICS_DIR = METRICS_DIR / ACTIVE_MODEL_VERSION if RUNTIME_MODE == "real" and ACTIVE_MODEL_VERSION else METRICS_DIR
MANIFEST_PATH = (
    MANIFESTS_DIR / ACTIVE_MODEL_VERSION / "manifest.json"
    if RUNTIME_MODE == "real" and ACTIVE_MODEL_VERSION
    else MANIFESTS_DIR / "fake-demo-manifest.json"
)

if DATABASE_ROLE not in {"PRODUCTION", "TRAINING"}:
    raise RuntimeError("SYNOPTIQ_DATABASE_ROLE must be PRODUCTION or TRAINING")
if DATABASE_ROLE == "TRAINING" and not TRAINING_DATABASE_URL:
    raise RuntimeError("TRAINING_DATABASE_URL is required for training database access")
if DATABASE_ROLE == "TRAINING" and DATABASE_URL and TRAINING_DATABASE_URL == DATABASE_URL:
    raise RuntimeError("TRAINING_DATABASE_URL must not equal DATABASE_URL")
if RUNTIME_MODE == "fake" and DATABASE_ROLE == "TRAINING":
    raise RuntimeError("Fake mode cannot use the training database role")

for _directory in (ARTIFACTS_DIR, MODELS_DIR, DATA_DIR, DB_PATH.parent, LIVE_DATA_DIR):
    _directory.mkdir(exist_ok=True, parents=True)

MODELS = ["GFS", "IFS", "AIFS"]  # candidate forecast sources

VARIABLES = ["precipitation", "temperature", "wind_speed"]

# Section 12: 2-3 pilot zones with contrasting weather behaviour.
PILOT_ZONES = {
    "kerala_western_ghats": {
        "label": "Kerala / Western Ghats",
        "lat_range": (8.3, 12.8),
        "lon_range": (74.9, 77.4),
        "emphasis": "Heavy rainfall / orographic terrain effects",
        "dominant_regimes": ["active_monsoon", "break_monsoon", "pre_monsoon"],
    },
    "bay_of_bengal_east_coast": {
        "label": "Bay of Bengal / East Coast",
        "lat_range": (12.5, 20.5),
        "lon_range": (80.0, 87.5),
        "emphasis": "Coastal systems, monsoon depressions, high wind",
        "dominant_regimes": ["depression", "active_monsoon", "normal"],
    },
    "indo_gangetic_plains": {
        "label": "Indo-Gangetic Plains / NW India",
        "lat_range": (24.0, 30.5),
        "lon_range": (75.0, 83.0),
        "emphasis": "Western disturbances, heatwaves, seasonal regime shift",
        "dominant_regimes": ["western_disturbance", "heatwave", "normal"],
    },
}

SEASONS = ["pre_monsoon", "sw_monsoon", "post_monsoon", "winter"]

REGIMES = [
    "active_monsoon",
    "break_monsoon",
    "depression",
    "western_disturbance",
    "heatwave",
    "pre_monsoon",
    "normal",
]

LEAD_HOURS = [24, 48, 72, 96, 120]

# Event thresholds used for extreme-weather guidance (Section 14 / PS ask).
THRESHOLDS = {
    "precipitation": {"heavy": 20.0, "unit": "mm/24h"},   # CSI @ 20mm is the primary rainfall event KPI
    "temperature": {"heatwave": 40.0, "unit": "deg_C"},
    "wind_speed": {"gale": 62.0, "unit": "km/h"},
}

# Pitch KPI target from the blueprint header (pre-registered, not a claimed result).
TARGET_RELATIVE_CSI_IMPROVEMENT = 0.05  # +5% relative CSI @ 20mm vs best single model

RANDOM_SEED = 42

# --- Strict time-based train/validation/test split (Section: leakage fix) ---
# Split is by TIME, computed once over the full sorted set of unique
# valid_times in the archive, so every row sharing a valid_time lands in the
# same split. Meta-model + bust classifier train on TRAIN only; calibration
# and bust-threshold selection use VALIDATION (frozen meta-model, not
# refit); TEST is touched only by scripts/evaluate_blend.py, after every
# learned artifact (skill table, meta-model, calibration, bust classifier)
# has been frozen.
TRAIN_FRACTION = 0.60
VAL_FRACTION = 0.15
TEST_FRACTION = 0.25  # 1.0 - TRAIN_FRACTION - VAL_FRACTION
