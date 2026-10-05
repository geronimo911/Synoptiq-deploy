import { createFileRoute } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { z } from "zod";
import { zodValidator } from "@tanstack/zod-adapter";
import {
  Area,
  AreaChart,
  CartesianGrid,
  Legend,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { APP_DATA_MODE, systemStatusQuery, weightsQuery } from "@/features/data/queries";
import type { RegionCode, VariableName, Season, Regime } from "@/features/data/types";
import { SelectControl } from "@/features/shared/Controls";
import { DataStatus } from "@/features/shared/DataStatus";
import { SectionHeading } from "@/features/shared/SectionHeading";
import { TopBar } from "@/features/shared/AppShell";
import { routeHead } from "@/features/shared/RouteMeta";
const schema = z.object({
  region: z.enum(["KWG", "BOB", "IGP"]).catch("KWG"),
  variable: z.enum(["precipitation", "temperature", "wind_speed"]).catch("precipitation"),
  season: z.enum(["winter", "pre_monsoon", "sw_monsoon", "post_monsoon"]).catch("sw_monsoon"),
  regime: z
    .enum(["active_monsoon", "break_monsoon", "western_disturbance", "depression", "normal"])
    .catch("active_monsoon"),
  lead: z.coerce.number().catch(72),
});
export const Route = createFileRoute("/weights")({
  validateSearch: zodValidator(schema),
  head: () =>
    routeHead(
      "Weight Map Explorer",
      "Explore how GFS, IFS, and AIFS trust weights evolve across Synoptiq's complete lead-time ladder.",
    ),
  component: Page,
});
const seasons = ["winter", "pre_monsoon", "sw_monsoon", "post_monsoon"],
  regimes = ["active_monsoon", "break_monsoon", "western_disturbance", "depression", "normal"];
const modelNames = ["GFS", "IFS", "AIFS"] as const;
function Page() {
  const s = Route.useSearch(),
    nav = Route.useNavigate();
  const set = (p: Partial<typeof s>) => nav({ to: ".", search: (q) => ({ ...q, ...p }) });
  const statusQuery = useQuery(systemStatusQuery);
  const systemStatus = statusQuery.data;
  const weightsResult = useQuery({
    ...weightsQuery(s.region, s.variable, s.season, s.regime),
    enabled: APP_DATA_MODE === "mock" || systemStatus?.ready === true,
  });
  const r = weightsResult.data;
  const points = Array.isArray(r?.points) ? r.points : [];
  const decay = Array.isArray(r?.confidence_decay) ? r.confidence_decay : [];
  const availableModels = APP_DATA_MODE === "mock"
    ? [...modelNames]
    : modelNames.filter((model) => {
        const provider = systemStatus?.providers[model];
        return provider?.status === "LIVE" && provider.is_real;
      });
  const weightData = points.map((p) => ({
    lead: `+${p.lead_hours}h`,
    ...p.weights,
  }));
  const confidenceData = decay.map((point) => ({
    lead: `+${point.lead_hours}h`,
    lead_hours: point.lead_hours,
    trust: point.trust * 100,
  }));
  const expectedLeads = [24, 48, 72, 96, 120];
  const completeWeights = expectedLeads.every((lead) => {
    const point = points.find((item) => item.lead_hours === lead);
    if (!point) return false;
    const weights = Object.values(point.weights);
    const weightModels = Object.keys(point.weights);
    return weights.length === availableModels.length
      && weights.every((weight) => typeof weight === "number" && Number.isFinite(weight))
      && availableModels.every((model) => typeof point.weights[model] === "number" && Number.isFinite(point.weights[model]))
      && weightModels.every((model) => availableModels.includes(model as (typeof modelNames)[number]))
      && Math.abs(weights.reduce((sum, weight) => sum + (typeof weight === "number" ? weight : 0), 0) - 1) <= 0.02;
  });
  const completeConfidence = expectedLeads.every((lead) => {
    const point = decay.find((item) => item.lead_hours === lead);
    return !!point && Number.isFinite(point.trust) && point.trust >= 0 && point.trust <= 1;
  });
  const inputsUnavailable = APP_DATA_MODE === "live" && !systemStatus?.ready;
  const selectedWeights = points.find((point) => point.lead_hours === s.lead)?.weights;
  const confidenceValues = confidenceData.map((point) => point.trust);
  const minTrust = confidenceValues.length ? Math.min(...confidenceValues) : null;
  const maxTrust = confidenceValues.length ? Math.max(...confidenceValues) : null;
  return (
    <>
      <TopBar mode={APP_DATA_MODE} />
      <div className="page">
        <SectionHeading
          eyebrow="Adaptive model trust / full ladder"
          title="Weight map explorer"
          copy="Watch the model hierarchy change as uncertainty accumulates across the supported forecast horizons."
          action={APP_DATA_MODE === "mock" ? <DataStatus mode={APP_DATA_MODE} /> : undefined}
        />
        <div className="control-deck five">
          <SelectControl
            label="Region"
            value={s.region}
            onChange={(v) => set({ region: v as RegionCode })}
            options={["KWG", "BOB", "IGP"].map((v) => ({ value: v, label: v }))}
          />
          <SelectControl
            label="Variable"
            value={s.variable}
            onChange={(v) => set({ variable: v as VariableName })}
            options={["precipitation", "temperature", "wind_speed"].map((v) => ({
              value: v,
              label: v.replace("_", " "),
            }))}
          />
          <SelectControl
            label="Season"
            value={s.season}
            onChange={(v) => set({ season: v as Season })}
            options={seasons.map((v) => ({ value: v, label: v.replace("_", " ") }))}
          />
          <SelectControl
            label="Regime"
            value={s.regime}
            onChange={(v) => set({ regime: v as Regime })}
            options={regimes.map((v) => ({ value: v, label: v.replaceAll("_", " ") }))}
          />
          <SelectControl
            label="Current lead"
            value={String(s.lead)}
            onChange={(v) => set({ lead: Number(v) })}
            options={expectedLeads.map((v) => ({ value: String(v), label: `+${v} hours` }))}
          />
        </div>
        {inputsUnavailable ? (
          <div className="page-state" role="status">
            <h2>{statusQuery.isPending ? "CHECKING REAL INPUTS" : "REAL INPUTS UNAVAILABLE"}</h2>
            <p>
              {statusQuery.error instanceof Error
                ? statusQuery.error.message
                : "No chart is shown because current real source cycles have not passed validation."}
            </p>
            {statusQuery.isError && (
              <button type="button" onClick={() => void statusQuery.refetch()}>
                Retry status check
              </button>
            )}
          </div>
        ) : (
        <>
        <section className="lead-weight-summary panel">
          <div className="panel-head">
            <div>
              <span className="kicker">CURRENT LEAD</span>
              <h2>+{s.lead}h source weights</h2>
            </div>
              <span className="chart-note">from the selected real forecast context</span>
          </div>
          <div className="lead-weight-values">
            {modelNames.map((model) => {
              const weight = selectedWeights?.[model];
              const value = typeof weight === "number" && Number.isFinite(weight)
                ? `${(weight * 100).toFixed(1)}%`
                : availableModels.includes(model)
                  ? "NOT AVAILABLE"
                  : "SOURCE UNAVAILABLE";
              return (
                <div key={model}>
                  <span>{model}</span>
                  <strong>{value}</strong>
                </div>
              );
            })}
          </div>
        </section>
        {completeWeights ? (
        <section className="chart-panel panel">
          <div className="panel-head">
            <div>
              <span className="kicker">MODEL SHARE</span>
              <h2>Trust allocation by lead time</h2>
            </div>
            <span className="chart-note">
              {availableModels.join(" · ")} · weights sum to 100% at each horizon
            </span>
          </div>
          <div className="big-chart">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={weightData}>
                <defs>
                  <linearGradient id="ifs" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0" stopColor="var(--chart-ifs)" stopOpacity=".38" />
                    <stop offset="1" stopColor="var(--chart-ifs)" stopOpacity=".02" />
                  </linearGradient>
                </defs>
                <CartesianGrid stroke="var(--chart-grid)" vertical={false} />
                <XAxis dataKey="lead" stroke="var(--muted-foreground)" />
                <YAxis
                  tickFormatter={(v) => `${Math.round(v * 100)}%`}
                  domain={[0, 1]}
                  stroke="var(--muted-foreground)"
                />
                <Tooltip
                  formatter={(value) => typeof value === "number" ? `${(value * 100).toFixed(1)}%` : "NOT AVAILABLE"}
                  contentStyle={{ background: "var(--popover)", borderColor: "var(--border)" }}
                />
                <Legend />
                {availableModels.includes("IFS") && (
                  <Area
                    type="monotone"
                    dataKey="IFS"
                    stroke="var(--chart-ifs)"
                    fill="url(#ifs)"
                    strokeWidth={3}
                  />
                )}
                {availableModels.includes("GFS") && (
                  <Area
                    type="monotone"
                    dataKey="GFS"
                    stroke="var(--chart-gfs)"
                    fill="transparent"
                    strokeWidth={2}
                  />
                )}
                {availableModels.includes("AIFS") && (
                  <Area
                    type="monotone"
                    dataKey="AIFS"
                    stroke="var(--chart-aifs)"
                    fill="transparent"
                    strokeWidth={2}
                  />
                )}
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </section>
        ) : (
          <div className="page-state" role={weightsResult.isError ? "alert" : "status"}>
            <h2>{weightsResult.isPending ? "LOADING WEIGHTS" : "REAL WEIGHT SERIES NOT AVAILABLE"}</h2>
            <p>
              {weightsResult.error instanceof Error
                ? weightsResult.error.message
                : "The selected live forecast did not return all five lead-time weight allocations."}
            </p>
          </div>
        )}
        {completeConfidence ? (
        <section className="chart-panel panel">
          <div className="panel-head">
            <div>
              <span className="kicker">COMPOSITE TRUST</span>
              <h2>Confidence decay</h2>
            </div>
            <strong className="delta">
              {confidenceData[0]?.trust.toFixed(1)}% → {confidenceData.at(-1)?.trust.toFixed(1)}%
            </strong>
          </div>
          <div className="trust-range">
            <span>MIN {minTrust?.toFixed(1)}%</span>
            <span>MAX {maxTrust?.toFixed(1)}%</span>
          </div>
          <div className="trust-chart">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={confidenceData}>
                <defs>
                  <linearGradient id="trustArea" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0" stopColor="var(--primary)" stopOpacity=".28" />
                    <stop offset="1" stopColor="var(--primary)" stopOpacity=".015" />
                  </linearGradient>
                </defs>
                <CartesianGrid stroke="var(--chart-grid)" vertical={false} />
                <XAxis dataKey="lead" stroke="var(--muted-foreground)" />
                <YAxis domain={[0, 100]} tickFormatter={(value) => `${value}%`} stroke="var(--muted-foreground)" />
                <Tooltip
                  formatter={(value) => typeof value === "number" ? `${value.toFixed(1)}%` : "NOT AVAILABLE"}
                  contentStyle={{ background: "var(--popover)", borderColor: "var(--border)" }}
                />
                <ReferenceLine y={50} stroke="var(--warning)" strokeDasharray="4 4" />
                <Area
                  type="monotone"
                  dataKey="trust"
                  stroke="var(--primary)"
                  strokeWidth={3}
                  fill="url(#trustArea)"
                  dot={{ r: 4, fill: "var(--primary)" }}
                  activeDot={{ r: 6 }}
                />
              </AreaChart>
            </ResponsiveContainer>
          </div>
          <p className="trust-explainer">Trust decreases as forecast horizon increases.</p>
        </section>
        ) : (
          <div className="page-state" role={weightsResult.isError ? "alert" : "status"}>
            <h2>LIVE TRUST SERIES NOT AVAILABLE</h2>
            <p>Current forecast response does not expose lead-specific trust.</p>
            {weightsResult.isError && weightsResult.error instanceof Error && <p>{weightsResult.error.message}</p>}
          </div>
        )}
        </>
        )}
      </div>
    </>
  );
}
