"""
Pydantic schemas. ForecastRecord mirrors the Section 15 "minimal data contract"
table in the blueprint exactly (model, run_time, valid_time, lead_hours,
variable, lat/lon, forecast_value, regime_probs, historical_skill,
model_weight, trust_score, bust_probability) so every module agrees on the
same input/output shape end to end.
"""
from __future__ import annotations
from datetime import datetime
from typing import Any, Literal, Optional
from pydantic import BaseModel, Field, ConfigDict


class ForecastRecord(BaseModel):
    model_config = ConfigDict(protected_namespaces=())
    model: str = Field(..., description="Forecast source identifier, e.g. GFS/IFS/AIFS")
    run_time: datetime
    valid_time: datetime
    lead_hours: int
    variable: str
    lat: float
    lon: float
    forecast_value: float
    regime_probs: dict[str, float] = Field(default_factory=dict)
    historical_skill: Optional[float] = None
    model_weight: Optional[float] = None
    trust_score: Optional[float] = None
    bust_probability: Optional[float] = None


class RawForecastResponse(BaseModel):
    region: str
    variable: str
    valid_time: datetime
    lead_hours: int
    sources: list[ForecastRecord]
    disagreement: float


class WeightExplanation(BaseModel):
    model_config = ConfigDict(protected_namespaces=())
    model: str
    weight: float
    top_drivers: list[dict] = Field(
        default_factory=list,
        description="SHAP-style {feature, contribution, direction} facts, "
        "highest |contribution| first. This is the source of truth for 'why'.",
    )


class BlendedForecastResponse(BaseModel):
    region: str
    variable: str
    valid_time: datetime
    lead_hours: int
    regime: str
    regime_probs: dict[str, float]
    raw_sources: list[ForecastRecord]
    weights: list[WeightExplanation]
    blended_value_raw: float
    blended_value_calibrated: float
    exceedance_probabilities: dict[str, float | None]
    trust_score: float
    disagreement: float
    bust_probability: float
    abstain: bool
    narrative: str
    analogues: list[dict] = Field(default_factory=list)


class TrustBreakdown(BaseModel):
    trust_score: float
    disagreement: float
    bust_probability: float
    abstain: bool
    signals: dict[str, float]


class ScorecardRow(BaseModel):
    model_config = ConfigDict(protected_namespaces=())
    model: str
    region: str
    lead_hours: int
    rmse: float
    mae: float
    bias: float
    csi_50mm: Optional[float] = None
    pod_50mm: Optional[float] = None
    far_50mm: Optional[float] = None
    fss_50mm: Optional[float] = None


class VerificationResponse(BaseModel):
    region: str
    variable: str
    threshold_label: str
    rows: list[ScorecardRow]
    synoptiq_csi: float
    best_single_model_csi: float
    relative_csi_improvement_pct: float
    target_relative_csi_improvement_pct: float
    meets_target: bool
    # --- added for leakage-fix transparency (additive, back-compatible) ---
    headline_metrics_period: str = "test (held-out, never used in training/calibration)"
    scorecard_rows_period: str = "train+validation (diagnostic skill profile; frozen before test evaluation)"
    fss_metric_note: str = "FSS is available only when computed from real gridded verification data."
    is_synthetic_prototype_result: bool = False


class ProviderStatusResponse(BaseModel):
    status: Literal["LIVE", "INVALID", "STALE", "TIMEOUT", "AUTH_ERROR", "UNAVAILABLE"]
    validation_result: str
    run_time: Optional[datetime] = None
    latest_available_time: Optional[datetime] = None
    age_minutes: Optional[int] = None
    validation_reason: str
    timestamp: Optional[datetime] = None
    reason: str
    is_real: bool
    fresh: bool = False
    complete: bool = False
    finite: bool = False
    source: Optional[str] = None
    source_model: Optional[str] = None
    source_transport: Optional[str] = None
    retrieved_at: Optional[datetime] = None
    source_run_time: Optional[datetime] = None
    run_time_basis: Optional[str] = None
    coverage: dict[str, Any] = {}
    fallback_used: bool = False


class SystemStatusResponse(BaseModel):
    mode: Literal["real", "fake"]
    ready: bool
    live_state: str = "REAL INPUTS UNAVAILABLE"
    active_model_version: Optional[str] = None
    training_period: Any = None
    last_successful_ingestion_time: Optional[datetime] = None
    latest_source_run: Optional[datetime] = None
    latest_source_runs: dict[str, Optional[datetime]]
    providers: dict[str, ProviderStatusResponse]
    supported_lead_times: list[int]
    test_period: Any = None
    last_verification_update: Optional[datetime] = None
    database: dict[str, Any]
    artifacts: dict[str, Any]


class ReplayEventSummary(BaseModel):
    event_id: str
    region: str
    label: str
    valid_time: datetime
    variable: str
    severity: str


class CounterfactualRequest(BaseModel):
    region: str
    variable: str = "precipitation"
    lead_hours: int = 72
    regime_override: Optional[str] = None
    season_override: Optional[str] = None


# ---------------------------------------------------------------------------
# Blend response schema (documented shape of /api/v1/forecast/blend)
# ---------------------------------------------------------------------------
# Declared with extra="allow" so every existing field keeps flowing through
# untouched while the new operational blocks appear in the generated OpenAPI
# documentation at /docs.

from typing import Any  # noqa: E402
from pydantic import BaseModel, ConfigDict  # noqa: E402


class Interval90(BaseModel):
    """90% prediction interval for the blended value."""
    model_config = ConfigDict(extra="allow")
    level: float
    low: float
    high: float
    half_width: float
    method: str


class TailContext(BaseModel):
    """Return-period context from the Generalised Pareto tail fit."""
    model_config = ConfigDict(extra="allow")
    status: str
    threshold_u95: float | None = None
    exceedance_probability_given_above_threshold: float | None = None
    interpretation: str | None = None
    reason: str | None = None


class SynopticRegimeBlock(BaseModel):
    """Active synoptic regime and its probability vector."""
    model_config = ConfigDict(extra="allow")
    regime: str
    label: str
    regime_probs: dict[str, float]


class ConfidenceBlock(BaseModel):
    """Confidence interval block, stating which source produced it."""
    model_config = ConfigDict(extra="allow")
    interval_90: Interval90 | None = None
    source: str | None = None
    tail_context: TailContext | None = None


class ValidationStatisticsBlock(BaseModel):
    """Residual statistics recomputed from the production database."""
    model_config = ConfigDict(extra="allow")
    status: str | None = None
    source: str | None = None
    n_validation_contexts: int | None = None
    conformal: dict[str, Any] | None = None
    residual_tail: dict[str, Any] | None = None
    observed_tail: dict[str, Any] | None = None
    reason: str | None = None


class WeightPolicyBlock(BaseModel):
    """Which weights were applied, and the measured justification."""
    model_config = ConfigDict(extra="allow")
    applied_lambda: float
    source: str
    meta_model_weights: dict[str, float]
    router_regime_weights: dict[str, float] | None = None
    measured_optimum_lambda: float | None = None
    measurement: dict[str, Any] | None = None
    note: str | None = None


class PeakPreservationBlock(BaseModel):
    """Extreme-blending layer state for this context."""
    model_config = ConfigDict(extra="allow")
    status: str
    calibrated_alpha: float | None = None
    effective_alpha: float | None = None
    base_value: float | None = None
    sharpest_model_value: float | None = None
    peak_preserved_value: float | None = None
    applied: bool | None = None
    note: str | None = None


class RegimeRoutingBlock(BaseModel):
    """Explicit statement of how the regime moved the policy."""
    model_config = ConfigDict(extra="allow")
    regime: str
    label: str | None = None
    synoptic: str | None = None
    policy_shift: str | None = None
    best_test_model_in_regime: str | None = None
    mean_weights_in_regime: dict[str, float] | None = None
    status: str | None = None


class BlendResponseV2(BaseModel):
    """Documented shape of the blend endpoint (extra fields pass through)."""
    model_config = ConfigDict(extra="allow")
    region: str
    region_name: str
    variable: str
    unit: str
    run_time: str
    valid_time: str
    lead_hours: int
    season: str
    regime: str
    final_value: float
    confidence_interval_90: ConfidenceBlock | None = None
    synoptic_regime: SynopticRegimeBlock | None = None
    validation_statistics: ValidationStatisticsBlock | None = None
    weight_policy: WeightPolicyBlock | None = None
    peak_preservation: PeakPreservationBlock | None = None
    regime_routing: RegimeRoutingBlock | None = None
