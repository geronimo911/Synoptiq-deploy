"""
Bias correction + probability calibration (Section 9).
  raw forecasts -> adaptive blend -> bias correction -> probability calibration -> product

- Quantile mapping: empirical CDF-to-CDF correction of the blended value
  against observed truth. Simple, defensible, student-feasible.
- Isotonic regression: turns the (bias-corrected) blended value into a
  calibrated threshold-exceedance probability per variable/threshold,
  which is the MVP-scope extreme-weather guidance (Section 9 + Section 14).

PER-REGION with SHRINKAGE (fix): calibration is fit separately per pilot
zone, then shrunk toward the pooled/global curve by sample size
(alpha = n / (n + shrink_k)) rather than switched on all-or-nothing. The
blueprint itself flags this exact risk in Section 9 ("a regime-conditioned
version can use a separate mapping per weather regime") — pooling Kerala's
heavy-monsoon rainfall distribution together with Bay of Bengal's and the
Indo-Gangetic Plains' into one global quantile map blurs each region's own
climatology into the others', which measurably hurt event-threshold skill
(CSI) in one region even though the underlying blend was correct. But a
naive *pure* per-region switch just moves the same failure mode to
whichever region has the smallest/noisiest validation sample — it overfits
that region's own quantiles and generalizes worse on TEST than the pooled
global curve did. Shrinkage is the standard fix for exactly that
bias/variance trade-off. Same leakage discipline throughout: fit ONLY on
the temporal VALIDATION window, applied unchanged at inference, never refit
on data being scored.
"""
from __future__ import annotations
import numpy as np
import joblib
from functools import lru_cache
from sklearn.isotonic import IsotonicRegression
from app.config import CALIBRATION_DIR, RUNTIME_MODE, THRESHOLDS

CAL_DIR = CALIBRATION_DIR
CAL_DIR.mkdir(exist_ok=True, parents=True)

MIN_SAMPLES_FOR_REGIONAL_FIT = 30


def _qmap_path(variable: str, region: str | None) -> "object":
    key = f"{variable}_{region}" if region else variable
    return CAL_DIR / f"qmap_{key}.joblib"


def _iso_path(variable: str, thresh_key: str, region: str | None):
    key = f"{variable}_{thresh_key}_{region}" if region else f"{variable}_{thresh_key}"
    return CAL_DIR / f"iso_{key}.joblib"


def load_quantile_map(variable: str, region: str | None = None):
    path = _qmap_path(variable, region)
    if not path.exists():
        return None
    return joblib.load(path)


def build_quantile_curve(blend_vals: np.ndarray, truth_vals: np.ndarray,
                         n_q: int = 50) -> tuple[np.ndarray, np.ndarray] | None:
    if len(blend_vals) < 10 or len(blend_vals) != len(truth_vals):
        return None
    qs = np.linspace(0, 1, n_q)
    return np.quantile(blend_vals, qs), np.quantile(truth_vals, qs)


def apply_quantile_curve(values: np.ndarray, curve: tuple[np.ndarray, np.ndarray]) -> np.ndarray:
    blend_q, truth_q = curve
    return np.interp(values, blend_q, truth_q)


def fit_quantile_map(blend_vals: np.ndarray, truth_vals: np.ndarray, variable: str,
                      region: str | None = None, n_q: int = 50,
                      shrink_toward: tuple | None = None, shrink_k: float = 100.0):
    """
    shrink_toward: optional (blend_q, truth_q) curve (typically the
    variable-only global calibrator) to shrink this fit toward, with
    empirical-Bayes-style weight alpha = n / (n + shrink_k) on the region's
    own curve. This is the fix for small-sample regional overfitting: a
    region with few validation contexts leans mostly on the global curve;
    a region with plenty of contexts (like most of this archive) is barely
    pulled at all. Without this, a purely per-region-or-global switch let
    one region's regional calibrator overfit its own small validation
    sample and generalize worse than the pooled global one on TEST.
    """
    curve = build_quantile_curve(blend_vals, truth_vals, n_q)
    if curve is None:
        return
    blend_q, truth_q = curve
    if shrink_toward is not None:
        g_blend_q, g_truth_q = shrink_toward
        alpha = len(blend_vals) / (len(blend_vals) + shrink_k)
        blend_q = alpha * blend_q + (1 - alpha) * g_blend_q
        truth_q = alpha * truth_q + (1 - alpha) * g_truth_q
    joblib.dump((blend_q, truth_q), _qmap_path(variable, region))


def apply_quantile_map(value: float, variable: str, region: str | None = None) -> float:
    """Prefers the region-specific calibrator; falls back to the
    variable-only (global, pooled-across-regions) one if the region wasn't
    fit (too little validation data), and to identity if neither exists."""
    if region is not None:
        rpath = _qmap_path(variable, region)
        if rpath.exists():
            blend_q, truth_q = joblib.load(rpath)
            return float(np.interp(value, blend_q, truth_q))
    gpath = _qmap_path(variable, None)
    if gpath.exists():
        blend_q, truth_q = joblib.load(gpath)
        return float(np.interp(value, blend_q, truth_q))
    if RUNTIME_MODE == "real":
        raise RuntimeError(f"REAL calibration artifact is missing for {variable}/{region or 'global'}.")
    return value


def fit_exceedance_calibrator(blend_vals: np.ndarray, truth_vals: np.ndarray, variable: str,
                               thresh_key: str, region: str | None = None):
    thresh = THRESHOLDS[variable][thresh_key]
    forecast_vals = np.asarray(blend_vals, dtype=float)
    truth_vals = np.asarray(truth_vals, dtype=float)
    finite = np.isfinite(forecast_vals) & np.isfinite(truth_vals)
    forecast_vals, truth_vals = forecast_vals[finite], truth_vals[finite]
    y = (truth_vals >= thresh).astype(float)
    positive_count = int(y.sum())
    negative_count = int(len(y) - positive_count)
    path = _iso_path(variable, thresh_key, region)
    if positive_count < 5 or negative_count < 5:
        path.unlink(missing_ok=True)
        _load_iso.cache_clear()
        return {"fitted": False, "positive_count": positive_count, "negative_count": negative_count}
    iso = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip")
    iso.fit(forecast_vals, y)
    joblib.dump(iso, path)
    _load_iso.cache_clear()
    return {"fitted": True, "positive_count": positive_count, "negative_count": negative_count}


def exceedance_calibration_source(variable: str, thresh_key: str, region: str | None = None) -> str | None:
    if region is not None and _iso_path(variable, thresh_key, region).exists():
        return "region"
    if _iso_path(variable, thresh_key, None).exists():
        return "global"
    return None


@lru_cache(maxsize=None)
def _load_iso(variable: str, thresh_key: str, region: str | None):
    path = _iso_path(variable, thresh_key, region)
    if not path.exists():
        return None
    return joblib.load(path)


def exceedance_probabilities(calibrated_value: float, variable: str, region: str | None = None) -> dict[str, float | None]:
    out = {}
    for thresh_key in THRESHOLDS.get(variable, {}):
        if thresh_key == "unit":
            continue
        iso = _load_iso(variable, thresh_key, region) if region else None
        if iso is None:
            iso = _load_iso(variable, thresh_key, None)
        if iso is None:
            out[thresh_key] = None
        else:
            probability = float(iso.predict([calibrated_value])[0])
            out[thresh_key] = round(float(np.clip(probability, 0.0, 1.0)), 3)
    return out
