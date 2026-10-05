export type RegionCode = "KWG" | "BOB" | "IGP";
export type VariableName = "precipitation" | "temperature" | "wind_speed";
export type ModelName = "GFS" | "IFS" | "AIFS";
export type Season = "winter" | "pre_monsoon" | "sw_monsoon" | "post_monsoon";
export type Regime =
  | "active_monsoon"
  | "break_monsoon"
  | "western_disturbance"
  | "depression"
  | "heatwave"
  | "pre_monsoon"
  | "normal";

export interface Region {
  code: RegionCode;
  name: string;
  lat: number;
  lon: number;
  emphasis: string;
  coastal: boolean;
}
export interface VariableMeta {
  variable: VariableName;
  unit: string;
  extreme_threshold: number;
}
export interface SourceMeta {
  kind: string;
  provider: string;
  role: string;
}
export interface SourceContribution {
  model: ModelName;
  forecast_value: number;
  weight: number | null;
  historical_skill: number | null;
}
export interface TrustBreakdown {
  historical_skill_component: number;
  disagreement_component: number;
  lead_time_component: number;
  regime_stability_component: number;
  data_quality_component: number;
  trust_score: number;
}
export interface ExplanationDriver {
  feature: string;
  contribution: number;
  direction: "increases_trust" | "decreases_trust" | "increases_weight" | "decreases_weight";
  detail: string;
}
export interface BlendResponse {
  region: RegionCode;
  region_name: string;
  variable: VariableName;
  unit: string;
  run_time: string;
  valid_time: string;
  lead_hours: number;
  season: Season;
  regime: Regime;
  regime_probs: Record<Regime, number>;
  sources: SourceContribution[];
  disagreement: number;
  raw_blend_value: number;
  bias_corrected_value: number;
  final_value: number;
  calibration?: {
    status: "CALIBRATED" | "NOT_AVAILABLE";
    reason: string | null;
  };
  trust: TrustBreakdown;
  bust_probability: number;
  bust_flag: boolean;
  abstain: boolean;
  explanation: ExplanationDriver[];
  fallback_used: boolean;
  provenance: Record<string, string>;
}
export interface WeightMapPoint {
  lead_hours: number;
  weights: Partial<Record<ModelName, number | null>>;
  trust_score: number;
}
export interface ConfidenceDecayPoint {
  lead_hours: number;
  trust: number;
}
export interface WeightMapResponse {
  region: RegionCode;
  variable: VariableName;
  regime: Regime;
  season: Season;
  points: WeightMapPoint[];
  confidence_decay: ConfidenceDecayPoint[];
}
export interface VerificationSummary {
  region: RegionCode;
  variable: VariableName;
  metric: string;
  threshold: number | null;
  best_single_model: ModelName | string;
  best_single_model_score: number | null;
  synoptiq_score: number | null;
  relative_improvement: number | null;
  meets_target: boolean | null;
  target_relative_improvement: number | null;
  test_contexts?: number | null;
  best_single_model_csi?: number | null;
  synoptiq_csi?: number | null;
  relative_csi_improvement?: number | null;
}
export interface ExtremeProbability {
  variable: VariableName;
  threshold: number;
  unit: string;
  probability: number | null;
  calibrated: boolean;
  probability_reason?: string | null;
  calibration_status?: "CALIBRATED" | "WITHHELD";
  calibration_source?: "region" | "global" | null;
  forecast_value: number;
  threshold_exceeded: boolean;
  trust_score?: number | null;
  bust_probability?: number | null;
  bust_flag?: boolean | null;
  disagreement?: number | null;
  source_range?: { minimum: number; maximum: number } | null;
}
export interface ExtremeGuidanceResponse {
  region: RegionCode;
  lead_hours: number;
  valid_time: string;
  guidance: ExtremeProbability[];
}
export interface ReplayEventSummary {
  event_id: string;
  region: RegionCode;
  variable: VariableName;
  valid_time: string;
  lead_hours?: number;
  label: string;
  headline: string;
  outcome?: "WIN" | "MISS" | "MIXED";
  severity?: string;
  naive_average_error?: number;
  single_model_error?: number;
  synoptiq_error?: number;
}
export interface ReplayEventDetail extends ReplayEventSummary {
  unit: string;
  lead_hours: number;
  raw_sources: SourceContribution[];
  naive_average: number;
  single_model_choice: { model: string; forecast_value: number };
  synoptiq_blend: number;
  reference_value: number;
  naive_average_error: number;
  single_model_error: number;
  synoptiq_error: number;
  narrative: string[];
  split?: string;
}
export interface HealthResponse {
  status: "ok" | "not_seeded";
  historical_data_cached: boolean;
  blend_model_trained: boolean;
  bust_model_trained: boolean;
  replay_events_cached: boolean;
  data_mode?: "real" | "synthetic";
  model_version?: string;
  forecast_rows_cached?: number;
  calibration_artifacts?: number;
}
export type ProviderStatusCode =
  "LIVE" | "INVALID" | "STALE" | "TIMEOUT" | "AUTH_ERROR" | "UNAVAILABLE";
export interface ProviderStatus {
  status: ProviderStatusCode;
  validation_result: string;
  run_time: string | null;
  latest_available_time: string | null;
  age_minutes: number | null;
  validation_reason: string;
  timestamp: string | null;
  reason: string;
  is_real: boolean;
  fresh?: boolean;
  complete?: boolean;
  finite?: boolean;
  source?: string | null;
  source_model?: string | null;
  source_transport?: string | null;
  retrieved_at?: string | null;
  source_run_time?: string | null;
  run_time_basis?: string | null;
  coverage?: Record<string, unknown>;
  fallback_used?: boolean;
}
export interface SystemStatusResponse {
  mode: "real" | "fake";
  ready: boolean;
  live_state?: "REAL LIVE" | "DEGRADED REAL" | "REAL INPUTS UNAVAILABLE";
  active_model_version: string | null;
  test_period?: { start: string; end: string } | null;
  last_successful_ingestion_time: string | null;
  providers: Record<ModelName, ProviderStatus>;
}
export type DataMode = "live" | "mock";

// Response of POST /api/v1/system/refresh-if-stale. `refreshed` is only true
// for the synchronous (wait=true) path; the default path returns
// "refresh_started" and the outcome is polled via LiveRefreshStatus.
export interface RefreshIfStaleResponse {
  status:
    | "fresh"
    | "refreshed"
    | "already_refreshed"
    | "refresh_started"
    | "refresh_in_progress"
    | "refresh_failed"
    | "disabled"
    | string;
  refreshed: boolean;
  last_refresh?: string | null;
  model_version?: string | null;
  data_age_minutes?: number | null;
  error?: string;
  note?: string;
}

// Response of GET /api/v1/ops/live-refresh/status.
export interface LiveRefreshStatus {
  enabled?: boolean;
  interval_minutes?: number;
  runs?: number;
  last_attempt?: string | null;
  last_success?: string | null;
  last_error?: string | null;
  manifest_age_minutes?: number | null;
  worker?: string;
  note?: string;
}
