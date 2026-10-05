import { createFileRoute } from "@tanstack/react-router";
import { z } from "zod";
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
} from "@/features/data/mock";

// Same-origin gateway for Synoptiq. LIVE mode proxies only to the configured FastAPI backend.

const region = z.enum(["KWG", "BOB", "IGP"]);
const variable = z.enum(["precipitation", "temperature", "wind_speed"]);
const season = z.enum(["winter", "pre_monsoon", "sw_monsoon", "post_monsoon"]);
const regime = z.enum([
  "active_monsoon",
  "break_monsoon",
  "western_disturbance",
  "depression",
  "normal",
]);
const lead = z.coerce.number().int().min(1).max(720);

function json(body: unknown, status = 200, fixture = true) {
  return Response.json(body, {
    status,
    headers: { "cache-control": "no-store", ...(fixture ? { "x-synoptiq-data": "fixture" } : {}) },
  });
}

function fixtureFor(path: string, q: URLSearchParams): Response {
  const p = (k: string) => q.get(k) ?? undefined;
  try {
    switch (path) {
      case "health":
        return json({ status: "ok", mode: "fixture" });
      case "regions":
        return json(mockRegions);
      case "variables":
        return json(mockVariables);
      case "lead-times":
        return json(mockLeadTimes);
      case "sources":
        return json(mockSources);
      case "forecast/blend":
        return json(
          mockBlend(
            region.parse(p("region")),
            variable.parse(p("variable")),
            lead.parse(p("lead_hours")),
          ),
        );
      case "weights/map":
        return json(
          mockWeightMap(
            region.parse(p("region")),
            variable.parse(p("variable")),
            season.parse(p("season")),
            regime.parse(p("regime")),
          ),
        );
      case "skill/verification":
        return json(mockVerification);
      case "extreme/guidance":
        return json(mockExtreme(region.parse(p("region")), lead.parse(p("lead_hours"))));
      case "replay/events":
        return json(mockReplaySummaries);
    }
    if (path.startsWith("replay/events/")) {
      const id = decodeURIComponent(path.slice("replay/events/".length));
      const match = mockReplayDetails.find((e) => e.event_id === id);
      return match ? json(match) : json({ detail: "Replay event not found" }, 404);
    }
    return json({ detail: "Unknown endpoint" }, 404);
  } catch {
    return json({ detail: "Invalid query parameters" }, 422);
  }
}

async function handle({ request, params }: { request: Request; params: { _splat?: string } }) {
  const path = (params._splat ?? "").replace(/^\/+|\/+$/g, "");
  const url = new URL(request.url);
  const configuredBase =
    process.env["SYNOPTIQ_API_URL"] ??
    process.env["VITE_API_BASE_URL"] ??
    import.meta.env.VITE_API_BASE_URL;
  const configuredBackend = configuredBase?.replace(/\/+$/, "").replace(/\/api\/v1$/, "");
  const isTemplateUrl = configuredBackend?.includes("your-render-service.onrender.com");
  const backend =
    configuredBackend && !(import.meta.env.DEV && isTemplateUrl)
      ? configuredBackend
      : import.meta.env.DEV
        ? "http://127.0.0.1:8000"
        : undefined;
  const demoMode =
    process.env["VITE_APP_MODE"] === "DEMO" || import.meta.env.VITE_APP_MODE === "DEMO";

  if (backend) {
    try {
      const method = request.method.toUpperCase();
      const hasBody = method !== "GET" && method !== "HEAD";
      const init: RequestInit = {
        method,
        headers: {
          Accept: "application/json",
          ...(hasBody
            ? { "content-type": request.headers.get("content-type") ?? "application/json" }
            : {}),
        },
        // POST is used for the demand-driven refresh, which can take longer
        // than a plain read.
        signal: AbortSignal.timeout(hasBody ? 30000 : 8000),
      };
      // Assign the body only when present: the project sets
      // exactOptionalPropertyTypes, so an explicit `body: undefined` is a type error.
      if (hasBody) init.body = await request.text();
      const upstream = await fetch(`${backend.replace(/\/+$/, "")}/api/v1/${path}${url.search}`, init);
      if (upstream.ok || upstream.status === 404 || upstream.status === 422) {
        return new Response(upstream.body, {
          status: upstream.status,
          headers: {
            "content-type": upstream.headers.get("content-type") ?? "application/json",
            "cache-control": "no-store",
          },
        });
      }
      return json({ detail: "Backend request failed" }, upstream.status || 502, false);
    } catch (error) {
      if (demoMode) return fixtureFor(path, url.searchParams);
      return json({ detail: "Live backend unavailable" }, 503, false);
    }
  }
  if (demoMode) return fixtureFor(path, url.searchParams);
  return json(
    { detail: "SYNOPTIQ_API_URL is not configured and DEMO mode is disabled" },
    503,
    false,
  );
}

export const Route = createFileRoute("/api/v1/$")({
  server: {
    handlers: {
      GET: handle,
      POST: handle,
    },
  },
});
