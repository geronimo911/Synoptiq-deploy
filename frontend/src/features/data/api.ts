import {
  mockBlend,
  mockExtreme,
  mockLeadTimes,
  mockRegions,
  mockReplayDetails,
  mockReplaySummaries,
  mockSources,
  mockVariables,
  mockVerification,
  mockWeightMap,
} from "./mock";
import type {
  BlendResponse,
  ExtremeGuidanceResponse,
  HealthResponse,
  LiveRefreshStatus,
  RefreshIfStaleResponse,
  Region,
  ReplayEventDetail,
  ReplayEventSummary,
  SourceMeta,
  SystemStatusResponse,
  VariableMeta,
  VerificationSummary,
  WeightMapResponse,
  RegionCode,
  VariableName,
  Season,
  Regime,
} from "./types";
import { z } from "zod";

export const API_BASE_URL = import.meta.env.DEV
  ? "/api/v1"
  : (import.meta.env["VITE_API_BASE_URL"] ?? "/api/v1");
export class ApiError extends Error {
  constructor(
    message: string,
    public status?: number,
  ) {
    super(message);
  }
}
function apiUrl(path: string) {
  if (!import.meta.env.SSR) return `${API_BASE_URL}${path}`;

  const configuredBase = process.env["SYNOPTIQ_API_URL"] ?? process.env["VITE_API_BASE_URL"];
  if (configuredBase) {
    const url = new URL(configuredBase);
    const basePath = url.pathname.replace(/\/?api\/v1\/?$/, "").replace(/\/$/, "");
    return `${url.origin}${basePath}/api/v1${path}`;
  }
  if (import.meta.env.DEV) return `http://127.0.0.1:8000/api/v1${path}`;
  throw new ApiError("SYNOPTIQ_API_URL must be configured for server-rendered LIVE requests");
}
export async function request<T>(
  path: string,
  signal?: AbortSignal,
  method: "GET" | "POST" = "GET",
): Promise<T> {
  const response = await fetch(apiUrl(path), {
    method,
    signal: signal ?? null,
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    try {
      const body = await response.json();
      detail = body.detail ?? detail;
    } catch {
      /* retain status */
    }
    throw new ApiError(detail, response.status);
  }
  if (response.headers.get("x-synoptiq-data") === "fixture")
    throw new ApiError("Live backend unavailable — fixture data", 503);
  return response.json() as Promise<T>;
}
const verificationRowsSchema = z.array(
  z.object({
    region: z.enum(["KWG", "BOB", "IGP"]),
    variable: z.enum(["precipitation", "temperature", "wind_speed"]),
    metric: z.string(),
    threshold: z.number().nullable(),
    best_single_model: z.string(),
    best_single_model_score: z.number().nullable(),
    synoptiq_score: z.number().nullable(),
    relative_improvement: z.number().nullable(),
    meets_target: z.boolean().nullable(),
    target_relative_improvement: z.number().nullable(),
    test_contexts: z.number().int().nullable().optional(),
    best_single_model_csi: z.number().nullable().optional(),
    synoptiq_csi: z.number().nullable().optional(),
    relative_csi_improvement: z.number().nullable().optional(),
  }),
);
export const liveApi = {
  health: (signal?: AbortSignal) => request<HealthResponse>("/health", signal),
  regions: (signal?: AbortSignal) => request<Region[]>("/regions", signal),
  systemStatus: (signal?: AbortSignal) => request<SystemStatusResponse>("/system/status", signal),
  // Demand-driven refresh: ask the backend to refresh only if the cached cycle
  // is stale. Non-blocking by default, so it never holds up the UI.
  refreshIfStale: (wait = false, signal?: AbortSignal) =>
    request<RefreshIfStaleResponse>(
      `/system/refresh-if-stale?wait=${wait ? "true" : "false"}`,
      signal,
      "POST",
    ),
  liveRefreshStatus: (signal?: AbortSignal) =>
    request<LiveRefreshStatus>("/ops/live-refresh/status", signal),
  variables: (signal?: AbortSignal) => request<VariableMeta[]>("/variables", signal),
  leadTimes: (signal?: AbortSignal) => request<number[]>("/lead-times", signal),
  sources: (signal?: AbortSignal) => request<Record<string, SourceMeta>>("/sources", signal),
  blend: (r: RegionCode, v: VariableName, l: number, signal?: AbortSignal) =>
    request<BlendResponse>(`/forecast/blend?region=${r}&variable=${v}&lead_hours=${l}`, signal),
  weights: (r: RegionCode, v: VariableName, s: Season, g: Regime, signal?: AbortSignal) =>
    request<WeightMapResponse>(
      `/weights/map?region=${r}&variable=${v}&season=${s}&regime=${g}`,
      signal,
    ),
  verification: async (signal?: AbortSignal) => {
    const payload = await request<unknown>("/skill/verification", signal);
    const parsed = verificationRowsSchema.safeParse(payload);
    if (!parsed.success) throw new ApiError("Invalid verification response from API");
    return parsed.data satisfies VerificationSummary[];
  },
  extremes: (r: RegionCode, l: number, signal?: AbortSignal) =>
    request<ExtremeGuidanceResponse>(`/extreme/guidance?region=${r}&lead_hours=${l}`, signal),
  replayEvents: (signal?: AbortSignal) => request<ReplayEventSummary[]>("/replay/events", signal),
  replayEvent: (id: string, signal?: AbortSignal) =>
    request<ReplayEventDetail>(`/replay/events/${encodeURIComponent(id)}`, signal),
};
export const fixtureApi = {
  systemStatus: async (): Promise<SystemStatusResponse> => ({
    mode: "fake",
    ready: false,
    active_model_version: null,
    last_successful_ingestion_time: null,
    providers: Object.fromEntries(
      ["GFS", "IFS", "AIFS"].map((source) => [
        source,
        {
          status: "UNAVAILABLE",
          validation_result: "NOT_EVALUATED",
          timestamp: null,
          reason: "Live provider checks are disabled in explicit DEMO mode.",
          is_real: false,
        },
      ]),
    ) as SystemStatusResponse["providers"],
  }),
  regions: async () => mockRegions,
  variables: async () => mockVariables,
  leadTimes: async () => mockLeadTimes,
  sources: async () => mockSources,
  blend: async (r: RegionCode, v: VariableName, l: number) => mockBlend(r, v, l),
  weights: async (r: RegionCode, v: VariableName, s: Season, g: Regime) => {
    const response = mockWeightMap(r, v, s, g);
    return {
      ...response,
      confidence_decay: response.points.map(({ lead_hours, trust_score }) => ({
        lead_hours,
        trust: trust_score,
      })),
    };
  },
  verification: async () => mockVerification,
  extremes: async (r: RegionCode, l: number) => mockExtreme(r, l),
  replayEvents: async () => mockReplaySummaries,
  replayEvent: async (id: string): Promise<ReplayEventDetail> => {
    const match = mockReplayDetails.find((event) => event.event_id === id) ?? mockReplayDetails[0];
    if (!match) throw new ApiError("No replay fixtures available");
    return match;
  },
};
