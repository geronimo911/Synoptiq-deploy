"""Physics-preserving extreme blending — the anti-"drizzle smearing" layer.

THE PROBLEM (variance reduction): averaging three models that place a heavy
convective core at slightly different locations broadens the rain patch and
lowers the peak. The blended field is *calmer* than any of its inputs, so
the blend systematically under-forecasts exactly the events disaster
management cares about.

THE FIX, in three pieces, all of them auditable:

1.  Peak-preserving fusion. For high-impact cells the blend is pulled
    towards the sharpest available signal::

        Y_peak = alpha * max_m(Y_m) + (1 - alpha) * weighted_blend

    alpha is calibrated on the validation split (never the test split) by
    minimising error on high-impact days. alpha -> 1 when the issue is a
    dangerous under-forecast (drizzle smearing) and alpha -> 0 when the
    weighted blend is already the better estimator at the tail.

2.  EVT tail model. A Generalised Pareto Distribution is fitted to the
    training exceedances above the 95th percentile so the system can report
    a return-period exceedance probability rather than a bare number::

        P(X - u > y | X > u) = (1 + xi * y / sigma) ** (-1 / xi)

3.  Split-conformal band. The 90% interval comes from the empirical
    quantile of validation residuals, so the coverage claim is checkable
    (and the build script reports the *measured* coverage on the untouched
    test split, including when it falls short of nominal).

Everything here is a pure function of numbers already computed by the
blending pipeline plus the calibrated constants in
``artifacts/.../research/peak_preservation.json``. No randomness, no
network, no hidden state.
"""
from __future__ import annotations

import math

# A cell is "high impact" for peak preservation when the observation (for
# calibration) or the forecast (for operation) reaches this threshold.
IMPACT_THRESHOLDS = {
    "precipitation": 64.5,   # IMD "heavy rain" class
    "wind_speed": 40.0,      # damaging-wind onset
    "temperature": 40.0,     # IMD heatwave criterion for the plains
}


def peak_preserving_value(
    base_value: float,
    sharpest_value: float,
    alpha: float,
) -> float:
    """alpha-weighted fusion of the sharpest model signal and the base blend.

    ``base_value`` is the production blend *after* bias correction (the
    calibrated value the dashboard already shows), and ``sharpest_value`` is
    the strongest single-model signal for the cell. alpha = 0 therefore
    reproduces the ordinary calibrated blend exactly — the layer is a strict
    generalisation, not a fork — and alpha = 1 takes the sharpest model.

    Callers compute ``sharpest_value`` as max_m(values[m]) over the models
    actually available; this function stays a pure two-number operation so
    it is trivially auditable and testable.
    """
    alpha = float(min(1.0, max(0.0, alpha)))
    return float(alpha * sharpest_value + (1.0 - alpha) * base_value)


def alpha_for_context(
    base_alpha: float,
    *,
    bust_probability: float = 0.0,
    disagreement: float = 0.0,
    disagreement_scale: float = 10.0,
) -> float:
    """Modulate the calibrated alpha with live context.

    Two signals push alpha up, both bounded and both explainable:
      * high bust probability -> the blend is known to be unreliable today;
      * large inter-model disagreement -> the models are placing the core in
        different places, which is precisely when averaging smears it.

    The result is clamped to [0, 1] and never exceeds the calibrated alpha
    by more than 0.25, so a single noisy signal cannot flip the policy.
    """
    bump = 0.15 * min(max(bust_probability, 0.0), 1.0)
    bump += 0.10 * min(max(disagreement, 0.0) / disagreement_scale, 1.0)
    return float(min(1.0, max(0.0, base_alpha + bump)))


# ---------------------------------------------------------------------------
# Generalised Pareto tail
# ---------------------------------------------------------------------------

def fit_gpd(exceedances) -> dict:
    """Method-of-moments GPD fit for the tail above a threshold.

    MoM is used deliberately: it is closed-form, needs no optimiser, and on
    the few hundred exceedances a 12-month zone-mean record provides it is
    far more stable than MLE. Returns xi (shape) and sigma (scale) plus the
    sample size so the caller can judge whether the fit is worth trusting.
    """
    xs = [float(x) for x in exceedances if x > 0]
    n = len(xs)
    if n < 10:
        return {"n": n, "xi": None, "sigma": None,
                "note": "fewer than 10 exceedances — tail model not estimated"}
    mean = sum(xs) / n
    var = sum((x - mean) ** 2 for x in xs) / n
    if var <= 0:
        return {"n": n, "xi": None, "sigma": None,
                "note": "degenerate exceedance sample (zero variance)"}
    xi = 0.5 * (1.0 - mean * mean / var)
    sigma = 0.5 * mean * (mean * mean / var + 1.0)
    return {"n": n, "xi": round(float(xi), 4), "sigma": round(float(sigma), 4),
            "mean_exceedance": round(mean, 4)}


def gpd_exceedance_probability(y: float, u: float, xi: float, sigma: float) -> float | None:
    """P(X - u > y | X > u) for y > 0 under the fitted GPD.

    Returns None when the fitted shape puts the requested level outside the
    support of the distribution (xi < 0 and y beyond the upper endpoint) —
    an honest "not estimable" rather than a negative probability.
    """
    if y <= 0:
        return 1.0
    if sigma <= 0:
        return None
    if abs(xi) < 1e-9:
        return float(math.exp(-y / sigma))
    base = 1.0 + xi * y / sigma
    if base <= 0:
        return None  # beyond the finite upper endpoint of a xi < 0 tail
    return float(base ** (-1.0 / xi))


def conformal_band(value: float, q90: float) -> dict:
    """Symmetric split-conformal 90% band around a point forecast."""
    return {
        "level": 0.9,
        "low": round(float(value) - float(q90), 3),
        "high": round(float(value) + float(q90), 3),
        "half_width": round(float(q90), 3),
        "method": "split_conformal_validation_residuals",
    }
