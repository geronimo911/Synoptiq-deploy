import { queryOptions } from "@tanstack/react-query";
import { request } from "@/features/data/api";

// The research suite is a live-only artifact: it is computed offline from the
// archived REAL_12M blend record and served read-only. There is no fixture
// version — in DEMO mode these queries fail with a clear message instead of
// degrading to synthetic numbers.
const RESEARCH_BASE = "/research";

async function research<T>(path: string): Promise<T> {
  return request<T>(`${RESEARCH_BASE}${path}`);
}

export interface ResearchSource {
  mode: string;
  blends_file: string;
  contexts: number;
  period: { start: string; end: string };
  splits: Record<string, number>;
  test_start: string;
  test_end: string;
}
export interface CalibrationSanityRow {
  raw_blend_mae: number;
  bias_corrected_mae: number;
  improvement_pct: number | null;
}
export interface OracleOverall {
  oracle_mae: number;
  best_fixed_mae: number;
  blend_mae: number;
  oracle_advantage_recovered_pct: number | null;
}
export interface AiAblationRow {
  region: string;
  variable: string;
  n: number;
  physics_only_mae: number;
  aifs_only_mae: number;
  full_blend_mae: number;
  ai_value_pct: number | null;
}
export interface ResearchSummary {
  generated_at: string;
  source: ResearchSource;
  calibration_sanity: { note: string; rows: Record<string, CalibrationSanityRow> };
  oracle_overall: OracleOverall;
  ai_ablation_rows: AiAblationRow[];
  commands: { regenerate: string };
}
export interface BaselineRow {
  region: string;
  variable: string;
  n_test_rows: number;
  n_test_days: number;
  mae: Record<string, number | null>;
  best_single_model: string;
  bootstrap: {
    method: string;
    blend_vs_best_single: { improvement_pct: number; ci95_improvement_pct: [number, number] };
    blend_vs_naive_average: { improvement_pct: number; ci95_improvement_pct: [number, number] };
  };
}
export interface HardDayRow {
  region: string;
  variable: string;
  definition: string;
  easy: { n: number; mae: Record<string, number> };
  hard: { n: number; mae: Record<string, number> };
  blend_gain_pct: { easy: number | null; hard: number | null };
}
export interface OracleRow {
  region: string;
  variable: string;
  n: number;
  oracle_mae: number;
  best_fixed_model: string;
  best_fixed_mae: number;
  blend_mae: number;
  oracle_advantage_recovered_pct: number | null;
  per_model_mae: Record<string, number>;
}
export interface ImdSystem {
  threshold_mm: number;
  hits: number;
  misses: number;
  false_alarms: number;
  pod: number | null;
  far: number | null;
  csi: number | null;
  n_events: number;
}
export interface ImdClasses {
  thresholds_mm: Record<string, number>;
  note: string;
  test: Record<string, Record<string, ImdSystem>>;
  full_year: Record<string, Record<string, ImdSystem>>;
}
export interface TrustTier {
  tier: string;
  n: number;
  mean_trust: number;
  mean_abs_error: number;
  median_abs_error: number;
}
export interface TrustAudit {
  tiers: TrustTier[];
  abstain_audit: {
    abstained: { n: number; mean_abs_error: number };
    not_abstained: { n: number; mean_abs_error: number };
  };
  trust_vs_error_rank_correlation: { spearman_rho: number; p_value: number } | null;
  interpretation: string;
}
export interface Diagnostics {
  crossovers: {
    region: string;
    variable: string;
    rmse_by_lead: Record<string, Record<string, number>>;
    best_single_by_lead: Record<string, string>;
    crossovers: { from_lead: number; to_lead: number; from_model: string; to_model: string }[];
    crossover_statement: string | null;
    scope: string;
  }[];
  skill_evolution: {
    window: string;
    scope: string;
    points: { date: string; region: string; variable: string; rmse: Record<string, number | null> }[];
  };
  bias_fingerprints: {
    scope: string;
    min_days: number;
    cards: {
      model: string; region: string; variable: string; season: string;
      n_days: number; mean_bias: number; statement: string;
    }[];
  };
}
export interface RpiAlert {
  region: string;
  variable: string;
  lead_hours: number;
  value: number;
  trust: number;
  bust_probability: number;
  event: string;
  severity_points: number;
  rpi_score: number;
  level: string;
  model_spread: number;
}
export interface RpiResponse {
  as_of: string;
  note: string;
  levels: Record<string, number>;
  alerts: RpiAlert[];
}

export const researchSummaryQuery = queryOptions({
  queryKey: ["research-summary"],
  queryFn: () => research<ResearchSummary>("/summary"),
  staleTime: 600_000,
  retry: 1,
});
export const researchBaselinesQuery = queryOptions({
  queryKey: ["research-baselines"],
  queryFn: () => research<{ note: string; rows: BaselineRow[] }>("/baselines"),
  staleTime: 600_000,
  retry: 1,
});
export const researchHardDaysQuery = queryOptions({
  queryKey: ["research-hard-days"],
  queryFn: () => research<{ rows: HardDayRow[]; claim_template: string }>("/hard-days"),
  staleTime: 600_000,
  retry: 1,
});
export const researchOracleQuery = queryOptions({
  queryKey: ["research-oracle"],
  queryFn: () => research<{ definition: string; rows: OracleRow[]; overall: OracleOverall }>("/oracle"),
  staleTime: 600_000,
  retry: 1,
});
export const researchAiQuery = queryOptions({
  queryKey: ["research-ai"],
  queryFn: () => research<{ definition: string; rows: AiAblationRow[] }>("/ai-ablation"),
  staleTime: 600_000,
  retry: 1,
});
export const researchImdQuery = queryOptions({
  queryKey: ["research-imd"],
  queryFn: () => research<ImdClasses>("/imd-classes"),
  staleTime: 600_000,
  retry: 1,
});
export const researchTrustQuery = queryOptions({
  queryKey: ["research-trust"],
  queryFn: () => research<TrustAudit>("/trust-audit"),
  staleTime: 600_000,
  retry: 1,
});
export const researchDiagnosticsQuery = queryOptions({
  queryKey: ["research-diagnostics"],
  queryFn: () => research<Diagnostics>("/diagnostics"),
  staleTime: 600_000,
  retry: 1,
});
export const rpiQuery = (date: string | null) =>
  queryOptions({
    queryKey: ["rpi", date ?? "latest"],
    queryFn: () => request<RpiResponse>(`/alerts/rpi${date ? `?date=${date}` : ""}`),
    staleTime: 300_000,
    retry: 1,
  });
