import { queryOptions } from "@tanstack/react-query";
import { request } from "@/features/data/api";

// Operational surfaces are live-only artifacts computed from the archived
// REAL_12M record; there is no fixture variant, so DEMO mode surfaces a
// clear message instead of synthetic numbers.

export interface TrustAtlasCell {
  region: string;
  variable: string;
  lead_hours: number;
  dominating_model: string;
  mean_weights: Record<string, number>;
  best_test_model: string | null;
  test_mae: Record<string, number | null>;
  n_contexts: number;
  regime_breakdown: Record<string, { n: number; mean_weight: Record<string, number> }>;
  reason: string;
}
export interface TrustAtlas {
  scope: string;
  model_colours: Record<string, string>;
  zone_centroids: Record<string, { lat: number; lon: number }>;
  region_lead_matrix: Record<string, Record<string, { dominating_model: string; weights: Record<string, number> }>>;
  cells: TrustAtlasCell[];
}

export interface RegimeProfile {
  regime: string;
  label: string;
  synoptic: string;
  typical_signature: string;
  n_contexts: number;
  n_test_contexts: number;
  mean_weights: Record<string, number>;
  test_mae: Record<string, number | null>;
  best_test_model: string | null;
  policy_shift: { deltas: Record<string, number>; material: boolean; statement: string };
}
export interface RegimeRouter {
  scope: string;
  catalogue: Record<string, { label: string; synoptic: string; typical_signature: string }>;
  global_mean_weights: Record<string, number>;
  profiles: RegimeProfile[];
  current: {
    as_of: string;
    regime: string;
    regime_probs: Record<string, number>;
    profile: RegimeProfile | null;
  };
}

export interface PeakVariable {
  threshold: number;
  alpha: number;
  alpha_calibration: {
    n_validation_high_impact: number;
    objective?: string;
    alpha: number | null;
    validation_tail_loss_at_alpha?: number;
    validation_tail_loss_at_alpha_0?: number;
    validation_mae_at_alpha?: number;
    validation_mae_at_alpha_0?: number;
    note?: string;
  };
  peak_smearing_audit: {
    n_test_high_impact?: number;
    scope?: string;
    mean_observed?: number;
    mean_blend?: number;
    mean_peak_preserved?: number;
    mean_best_single?: number;
    mae_blend?: number;
    mae_peak_preserved?: number;
    mae_change_pct?: number | null;
    p99_ratio_blend?: number;
    p99_ratio_best_single?: number;
    p99_ratio_peak_preserved?: number;
    interpretation?: string;
    descriptive_alpha_sweep?: { alpha: number; under_forecast_bias: number; mae: number }[];
    note?: string;
  };
  gpd_tail: {
    n: number; xi: number | null; sigma: number | null;
    threshold_u95?: number;
    mean_exceedance?: number;
    note?: string;
    example_exceedance_probability?: Record<string, number | null>;
  };
  conformal: {
    q90: number | null;
    nominal_coverage?: number;
    measured_test_coverage?: number | null;
    method?: string;
    example_band?: { level: number; low: number; high: number; half_width: number };
    note?: string;
  };
  conclusion: string;
}
export interface PeakPreservation {
  scope: string;
  thresholds: Record<string, number>;
  variables: Record<string, PeakVariable>;
}

export interface ImpactZone {
  zone: string;
  isi: number;
  colour: string;
  severity: string;
  vulnerability_factor: number;
  vulnerability_rationale?: string;
  components: Record<string, { value: number; threshold: number; ratio: number; contribution: number }>;
  advisory: string;
}
export interface ImpactResponse {
  date: string;
  source?: string;
  hazard_weights?: Record<string, number>;
  colour_bands?: { colour: string; floor: number; word: string }[];
  zones: ImpactZone[];
}

export interface OpsStatus {
  last_run: {
    ran_at: string; valid_date: string; zones: number; max_colour: string; artifacts: string[];
  } | null;
  runs_recorded: number;
  bulletin_days: string[];
  artifacts_present: Record<string, boolean>;
  commands: { routine: string; bulletin_only: string };
}

export interface ArchiveDates {
  dates: string[];
  earliest: string | null;
  latest: string | null;
}

const api = <T>(path: string) => request<T>(path);

export const trustAtlasQuery = queryOptions({
  queryKey: ["trust-atlas"],
  queryFn: () => api<TrustAtlas>("/trust/atlas"),
  staleTime: 600_000,
  retry: 1,
});
export const regimeRouterQuery = queryOptions({
  queryKey: ["regime-router"],
  queryFn: () => api<RegimeRouter>("/regime/router"),
  staleTime: 600_000,
  retry: 1,
});
export const peakPreservationQuery = queryOptions({
  queryKey: ["peak-preservation"],
  queryFn: () => api<PeakPreservation>("/research/peak-preservation"),
  staleTime: 600_000,
  retry: 1,
});
export const impactQuery = (date: string | null) =>
  queryOptions({
    queryKey: ["impact", date ?? "latest"],
    queryFn: () => api<ImpactResponse>(`/impact/severity${date ? `?date=${date}` : ""}`),
    staleTime: 300_000,
    retry: 1,
  });
export const archiveDatesQuery = queryOptions({
  queryKey: ["archive-dates"],
  queryFn: () => api<ArchiveDates>("/archive/dates"),
  staleTime: 600_000,
  retry: 1,
});
export const opsStatusQuery = queryOptions({
  queryKey: ["ops-status"],
  queryFn: () => api<OpsStatus>("/ops/status"),
  staleTime: 60_000,
  retry: 1,
});
