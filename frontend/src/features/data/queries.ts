import { queryOptions } from "@tanstack/react-query";
import { fixtureApi, liveApi } from "./api";
import type {
  LiveRefreshStatus,
  RefreshIfStaleResponse,
  RegionCode,
  Regime,
  Season,
  VariableName,
} from "./types";

export const APP_DATA_MODE = import.meta.env.VITE_APP_MODE === "DEMO" ? "mock" : "live";
const DEMO_MODE = APP_DATA_MODE === "mock";
const choose = <T>(live: () => Promise<T>, demo: () => Promise<T>) => (DEMO_MODE ? demo() : live());

// Demand-driven refresh: only in LIVE mode, and always best-effort. The UI
// renders from cached data first, so a failed refresh never blocks or breaks it.
export const REFRESH_ON_LOAD = !DEMO_MODE;

export async function requestRefreshIfStale(): Promise<RefreshIfStaleResponse | null> {
  if (DEMO_MODE) return null;
  try {
    return await liveApi.refreshIfStale(false);
  } catch {
    return null;
  }
}

export async function readLiveRefreshStatus(): Promise<LiveRefreshStatus | null> {
  if (DEMO_MODE) return null;
  try {
    return await liveApi.liveRefreshStatus();
  } catch {
    return null;
  }
}

export const regionsQuery = queryOptions({
  queryKey: ["regions"],
  queryFn: () => choose(liveApi.regions, fixtureApi.regions),
  staleTime: 300_000,
});
export const variablesQuery = queryOptions({
  queryKey: ["variables"],
  queryFn: () => choose(liveApi.variables, fixtureApi.variables),
  staleTime: 300_000,
});
export const leadTimesQuery = queryOptions({
  queryKey: ["lead-times"],
  queryFn: () => choose(liveApi.leadTimes, fixtureApi.leadTimes),
  staleTime: 300_000,
});
export const sourcesQuery = queryOptions({
  queryKey: ["sources"],
  queryFn: () => choose(liveApi.sources, fixtureApi.sources),
  staleTime: 300_000,
});
export const systemStatusQuery = queryOptions({
  queryKey: ["system-status"],
  queryFn: () => choose(liveApi.systemStatus, fixtureApi.systemStatus),
  staleTime: 30_000,
  refetchInterval: 30_000,
  retry: 1,
});
export const blendQuery = (r: RegionCode, v: VariableName, l: number) =>
  queryOptions({
    queryKey: ["blend", r, v, l],
    queryFn: () => (DEMO_MODE ? fixtureApi.blend(r, v, l) : liveApi.blend(r, v, l)),
    refetchInterval: 60_000,
    retry: 1,
  });
export const weightsQuery = (r: RegionCode, v: VariableName, s: Season, g: Regime) =>
  queryOptions({
    queryKey: ["weights", r, v, s, g],
    queryFn: () => (DEMO_MODE ? fixtureApi.weights(r, v, s, g) : liveApi.weights(r, v, s, g)),
    retry: 1,
  });
export const verificationQuery = queryOptions({
  queryKey: ["verification"],
  queryFn: () => (DEMO_MODE ? fixtureApi.verification() : liveApi.verification()),
  retry: 1,
});
export const extremesQuery = (r: RegionCode, l: number) =>
  queryOptions({
    queryKey: ["extremes", r, l],
    queryFn: () => (DEMO_MODE ? fixtureApi.extremes(r, l) : liveApi.extremes(r, l)),
    retry: 1,
  });
export const replayEventsQuery = queryOptions({
  queryKey: ["replay-events"],
  queryFn: () => (DEMO_MODE ? fixtureApi.replayEvents() : liveApi.replayEvents()),
  staleTime: 300_000,
  retry: 1,
});
export const replayEventQuery = (id: string) =>
  queryOptions({
    queryKey: ["replay-event", id],
    queryFn: () => (DEMO_MODE ? fixtureApi.replayEvent(id) : liveApi.replayEvent(id)),
    retry: 1,
  });
