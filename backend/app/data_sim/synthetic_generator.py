"""
Synthetic forecast + ground-truth generator.

Live NOMADS / ECMWF Open Data / CDS / IMERG endpoints are not reachable from
this build environment, so ingestion (Section 4) is simulated here instead —
but every downstream module (skill engine, regime detector, blending,
calibration, trust) consumes exactly the canonical schema in Section 15, so
swapping this module for a real cfgrib/xarray ingestion job later requires no
change anywhere else in the pipeline.

Design goals, matching the blueprint's own framing of the problem:
  - Each model has a *different* bias/noise profile that varies by region,
    regime and lead time, so "no single model is always best" is literally
    true of the synthetic world, and skill genuinely is learnable.
  - Skill degrades with lead time (Section 6).
  - Rainfall is regime-driven and heavy-tailed so CSI/POD/FAR/FSS are
    meaningful, not degenerate.
"""
from __future__ import annotations
import numpy as np
from datetime import datetime, timedelta
from app.config import PILOT_ZONES, MODELS, LEAD_HOURS, SEASONS, RANDOM_SEED

rng = np.random.default_rng(RANDOM_SEED)

# Per-model "personality": baseline skill multiplier and how fast each model
# degrades per lead-hour, tuned per regime so relative model skill genuinely
# swaps around by situation (the whole premise of the PS).
MODEL_PROFILES = {
    "GFS": dict(bias=dict(default=-6.0, active_monsoon=-18.0, depression=-10.0,
                           western_disturbance=2.0, heatwave=0.5),
                noise=dict(default=14.0, active_monsoon=22.0, depression=20.0),
                lead_decay=0.9),
    "IFS": dict(bias=dict(default=-2.0, active_monsoon=4.0, depression=-4.0,
                           western_disturbance=-1.0, heatwave=1.0),
                noise=dict(default=10.0, active_monsoon=13.0, depression=15.0),
                lead_decay=0.6),
    "AIFS": dict(bias=dict(default=1.0, active_monsoon=-9.0, depression=6.0,
                            western_disturbance=-6.0, heatwave=-0.5),
                 noise=dict(default=8.0, active_monsoon=17.0, depression=12.0),
                 lead_decay=1.3),  # AI model: sharp short-lead, degrades faster
}

REGIME_BASE_RAIN = {
    "active_monsoon": 70.0, "break_monsoon": 8.0, "depression": 95.0,
    "western_disturbance": 20.0, "heatwave": 1.0, "pre_monsoon": 15.0,
    "normal": 6.0,
}
REGIME_BASE_TEMP = {
    "heatwave": 42.0, "western_disturbance": 22.0, "active_monsoon": 27.0,
    "break_monsoon": 30.0, "depression": 28.0, "pre_monsoon": 34.0, "normal": 29.0,
}
REGIME_BASE_WIND = {
    "depression": 55.0, "active_monsoon": 30.0, "western_disturbance": 25.0,
    "heatwave": 12.0, "break_monsoon": 15.0, "pre_monsoon": 18.0, "normal": 14.0,
}

SEASON_REGIME_WEIGHTS = {
    "sw_monsoon": {"active_monsoon": 0.35, "break_monsoon": 0.25, "depression": 0.15,
                   "normal": 0.15, "pre_monsoon": 0.05, "western_disturbance": 0.03,
                   "heatwave": 0.02},
    "pre_monsoon": {"pre_monsoon": 0.4, "heatwave": 0.25, "normal": 0.25,
                     "western_disturbance": 0.05, "active_monsoon": 0.02,
                     "break_monsoon": 0.02, "depression": 0.01},
    "post_monsoon": {"depression": 0.3, "normal": 0.35, "break_monsoon": 0.15,
                       "western_disturbance": 0.1, "pre_monsoon": 0.05,
                       "active_monsoon": 0.03, "heatwave": 0.02},
    "winter": {"western_disturbance": 0.4, "normal": 0.45, "heatwave": 0.01,
                "break_monsoon": 0.05, "pre_monsoon": 0.04, "active_monsoon": 0.03,
                "depression": 0.02},
}


def sample_regime(season: str) -> str:
    weights = SEASON_REGIME_WEIGHTS[season]
    regimes, probs = zip(*weights.items())
    return rng.choice(regimes, p=probs)


def _regime_probs_for(true_regime: str) -> dict:
    """Simulate a noisy-but-mostly-right rule-based regime detector output."""
    from app.config import REGIMES
    logits = rng.normal(0, 0.6, size=len(REGIMES))
    idx = REGIMES.index(true_regime)
    logits[idx] += 3.0
    probs = np.exp(logits) / np.exp(logits).sum()
    return {r: round(float(p), 4) for r, p in zip(REGIMES, probs)}


def ground_truth_value(variable: str, regime: str) -> float:
    if variable == "precipitation":
        base = REGIME_BASE_RAIN[regime]
        val = max(0.0, rng.gamma(shape=2.0, scale=max(base, 1.0) / 2.0))
        return round(float(val), 2)
    if variable == "temperature":
        base = REGIME_BASE_TEMP[regime]
        return round(float(rng.normal(base, 2.0)), 2)
    if variable == "wind_speed":
        base = REGIME_BASE_WIND[regime]
        return round(float(max(0.0, rng.normal(base, 6.0))), 2)
    raise ValueError(variable)


def model_forecast_value(model: str, truth: float, variable: str, regime: str, lead_hours: int) -> float:
    profile = MODEL_PROFILES[model]
    bias = profile["bias"].get(regime, profile["bias"]["default"])
    noise_sd = profile["noise"].get(regime, profile["noise"]["default"])
    lead_factor = 1.0 + profile["lead_decay"] * (lead_hours / 24.0) * 0.18
    scale = {"precipitation": 1.0, "temperature": 0.12, "wind_speed": 0.35}[variable]
    err = rng.normal(bias * scale, noise_sd * scale * lead_factor)
    val = truth + err
    if variable == "precipitation":
        val = max(0.0, val)
    if variable == "wind_speed":
        val = max(0.0, val)
    return round(float(val), 2)


def season_for_month(month: int) -> str:
    if month in (3, 4, 5):
        return "pre_monsoon"
    if month in (6, 7, 8, 9):
        return "sw_monsoon"
    if month in (10, 11):
        return "post_monsoon"
    return "winter"


def sample_point(region_key: str) -> tuple[float, float]:
    zone = PILOT_ZONES[region_key]
    lat = round(float(rng.uniform(*zone["lat_range"])), 2)
    lon = round(float(rng.uniform(*zone["lon_range"])), 2)
    return lat, lon


def generate_history(n_cycles_per_region: int = 260, start: datetime | None = None):
    """
    Generate a synthetic multi-season forecast archive across all pilot
    zones, variables and lead times. Yields dict rows ready for DB insert:
    one ground-truth row and one forecast row per model per (region, cycle,
    variable, lead_hours).
    """
    start = start or (datetime(2024, 1, 1))
    for region_key in PILOT_ZONES:
        for c in range(n_cycles_per_region):
            valid_time = start + timedelta(hours=12 * c)
            season = season_for_month(valid_time.month)
            regime = sample_regime(season)
            regime_probs = _regime_probs_for(regime)
            lat, lon = sample_point(region_key)
            for variable in ("precipitation", "temperature", "wind_speed"):
                truth = ground_truth_value(variable, regime)
                yield dict(kind="truth", region=region_key, variable=variable,
                           valid_time=valid_time, lat=lat, lon=lon, value=truth)
                for lead_hours in LEAD_HOURS:
                    run_time = valid_time - timedelta(hours=lead_hours)
                    for model in MODELS:
                        fval = model_forecast_value(model, truth, variable, regime, lead_hours)
                        yield dict(kind="forecast", region=region_key, model=model,
                                   run_time=run_time, valid_time=valid_time,
                                   lead_hours=lead_hours, variable=variable,
                                   lat=lat, lon=lon, forecast_value=fval,
                                   season=season, regime=regime,
                                   regime_probs=regime_probs)
