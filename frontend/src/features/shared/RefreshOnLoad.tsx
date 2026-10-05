import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  REFRESH_ON_LOAD,
  readLiveRefreshStatus,
  requestRefreshIfStale,
} from "@/features/data/queries";

const POLL_INTERVAL_MS = 15_000;
const MAX_POLLS = 8; // ~2 minutes of polling per attempt, then give up quietly
// Re-check freshness roughly every 15 minutes while the tab is active. This is
// the same 15 as the backend's SYNOPTIQ_REFRESH_MIN_AGE_MINUTES threshold: the
// backend still decides whether anything is actually stale, and still holds the
// PostgreSQL advisory lock, so a redundant call costs one cheap request.
const REFRESH_EVERY_MS = 15 * 60_000;

const sleep = (ms: number) =>
  new Promise<void>((resolve) => {
    window.setTimeout(resolve, ms);
  });

/**
 * Fires the demand-driven stale-data refresh after the UI has already rendered.
 *
 * Runs on initial load, whenever the tab becomes visible again, and about every
 * 15 minutes while the tab is active. The page never waits for it: it runs in
 * the background and only invalidates the forecast/data queries once the
 * backend reports fresh data. If the refresh fails or is disabled, the UI keeps
 * showing the cached data it loaded on first paint.
 *
 * There is deliberately no cron and no per-request refresh: the backend decides
 * freshness and serialises concurrent callers with its advisory lock.
 */
export function RefreshOnLoad() {
  const queryClient = useQueryClient();

  useEffect(() => {
    if (!REFRESH_ON_LOAD) return;

    let cancelled = false;
    let running = false; // one in-flight attempt per tab; never stack refreshes

    const invalidate = () => {
      void queryClient.invalidateQueries();
    };

    const pollForCompletion = async (previousSuccess: string | null) => {
      for (let polls = 0; polls < MAX_POLLS && !cancelled; polls += 1) {
        await sleep(POLL_INTERVAL_MS);
        if (cancelled) return;
        const status = await readLiveRefreshStatus();
        if (cancelled) return;
        if (status?.last_success && status.last_success !== previousSuccess) {
          invalidate();
          return;
        }
      }
    };

    const attempt = async () => {
      if (cancelled || running) return;
      if (document.visibilityState === "hidden") return;
      running = true;
      try {
        // Capture the pre-refresh success stamp so we can detect a NEW one.
        const before = await readLiveRefreshStatus();
        const previousSuccess = before?.last_success ?? null;

        const result = await requestRefreshIfStale();
        if (cancelled || !result) return;

        if (result.status === "fresh") return; // nothing to do
        if (result.refreshed) {
          invalidate(); // synchronous (wait=true) path finished
          return;
        }
        if (result.status !== "refresh_started" && result.status !== "refresh_in_progress") {
          return;
        }
        // Non-blocking path: the backend is refreshing under its lock. Poll
        // until a new success stamp appears, then refetch.
        await pollForCompletion(previousSuccess);
      } finally {
        running = false;
      }
    };

    // 1. initial load
    void attempt();

    // 2. returning to the tab
    const onVisibilityChange = () => {
      if (document.visibilityState === "visible") void attempt();
    };
    document.addEventListener("visibilitychange", onVisibilityChange);

    // 3. periodic re-check while the tab is active
    const interval = window.setInterval(() => {
      void attempt();
    }, REFRESH_EVERY_MS);

    return () => {
      cancelled = true;
      document.removeEventListener("visibilitychange", onVisibilityChange);
      window.clearInterval(interval);
    };
  }, [queryClient]);

  return null;
}
