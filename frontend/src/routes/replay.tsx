import { createFileRoute, Link, Outlet, useRouterState } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import {
  Activity,
  ArrowLeftRight,
  ArrowRight,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  FlaskConical,
} from "lucide-react";
import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { APP_DATA_MODE, replayEventsQuery } from "@/features/data/queries";
import { SectionHeading } from "@/features/shared/SectionHeading";
import { TopBar } from "@/features/shared/AppShell";
import { DataStatus } from "@/features/shared/DataStatus";
import { SelectControl } from "@/features/shared/Controls";
import { routeHead } from "@/features/shared/RouteMeta";
import { Button } from "@/components/ui/button";
export const Route = createFileRoute("/replay")({
  head: () =>
    routeHead(
      "Counterfactual Bust Replay",
      "Replay verified Synoptiq forecast cases, including clear wins and scientifically honest misses.",
    ),
  component: Page,
});
type ReplayFilters = {
  region: string;
  variable: string;
  lead: string;
  outcome: string;
  page: number;
};
type ReplayFilterKey = Exclude<keyof ReplayFilters, "page">;

function Page() {
  const isDetailRoute = useRouterState({
    select: (state) => state.location.pathname.startsWith("/replay/"),
  });
  const [filters, setFilters] = useState<ReplayFilters>({
    region: "ALL",
    variable: "ALL",
    lead: "ALL",
    outcome: "ALL",
    page: 0,
  });
  const onFilterChange = (key: ReplayFilterKey, value: string) =>
    setFilters((current) => ({ ...current, [key]: value, page: 0 }));
  const onPageChange = (update: (current: number) => number) =>
    setFilters((current) => ({ ...current, page: update(current.page) }));
  return isDetailRoute ? <Outlet /> : (
    <ReplayListPage
      filters={filters}
      onFilterChange={onFilterChange}
      onPageChange={onPageChange}
    />
  );
}

function ReplayListPage({
  filters,
  onFilterChange,
  onPageChange,
}: {
  filters: ReplayFilters;
  onFilterChange: (key: ReplayFilterKey, value: string) => void;
  onPageChange: (update: (current: number) => number) => void;
}) {
  const query = useQuery(replayEventsQuery);
  const { data } = query;
  const rows = Array.isArray(data) ? data : [];
  const filtered = rows.filter((event) =>
    (filters.region === "ALL" || event.region === filters.region)
    && (filters.variable === "ALL" || event.variable === filters.variable)
    && (filters.lead === "ALL" || event.lead_hours === Number(filters.lead))
    && (filters.outcome === "ALL" || (event.outcome ?? "MIXED") === filters.outcome),
  );
  const pageSize = 18;
  const pageCount = Math.max(1, Math.ceil(filtered.length / pageSize));
  const pageRows = filtered.slice(filters.page * pageSize, (filters.page + 1) * pageSize);
  const wins = filtered.filter((event) => event.outcome === "WIN").length;
  const misses = filtered.filter((event) => event.outcome === "MISS").length;
  const mixed = filtered.filter((event) => !event.outcome || event.outcome === "MIXED").length;
  const errorScale: Record<string, number> = { precipitation: 20, temperature: 40, wind_speed: 62 };
  const meanError = (key: "naive_average_error" | "single_model_error" | "synoptiq_error") => {
    const values = filtered
      .map((event) => {
        const value = event[key];
        if (typeof value !== "number" || !Number.isFinite(value)) return null;
        return filters.variable === "ALL" ? value / errorScale[event.variable] : value;
      })
      .filter((value): value is number => value !== null);
    return values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : 0;
  };
  const errorData = [
    { baseline: "Naive mean", error: meanError("naive_average_error"), color: "var(--chart-gfs)" },
    { baseline: "Top-weight source", error: meanError("single_model_error"), color: "var(--chart-aifs)" },
    { baseline: "Synoptiq", error: meanError("synoptiq_error"), color: "var(--chart-ifs)" },
  ];
  if (query.isPending || query.isError) {
    return (
      <>
        <TopBar mode={APP_DATA_MODE} />
        <div className="page-state" role={query.isError ? "alert" : "status"}>
          <h2>{query.isError ? "REPLAY DATA UNAVAILABLE" : "LOADING REPLAY CASES"}</h2>
          <p>
            {query.isError
              ? query.error instanceof Error
                ? query.error.message
                : "The real replay API could not be reached."
              : "Loading verified historical replay cases from the real API."}
          </p>
          {query.isError && <Button onClick={() => void query.refetch()}>Retry</Button>}
        </div>
      </>
    );
  }
  return (
    <>
      <TopBar mode={APP_DATA_MODE} />
      <div className="page">
        <SectionHeading
          eyebrow={APP_DATA_MODE === "live" ? "REPLAY · REAL_12M HELD-OUT TEST" : "DEMO REPLAY"}
          title="Counterfactual bust replay"
          copy="Every frozen test case, including misses. Compare Synoptiq with the naive mean and the model selected by the highest trust weight."
          action={<DataStatus mode={APP_DATA_MODE} />}
        />
        <div className="proof-strip replay-stats">
          <div><Activity /><span>HELD-OUT CASES</span><strong>{filtered.length.toLocaleString()}</strong><small>REAL_12M test split</small></div>
          <div><CheckCircle2 /><span>BEAT BOTH BASELINES</span><strong>{wins.toLocaleString()}</strong><small>{filtered.length ? `${Math.round(wins / filtered.length * 100)}% of selection` : "No cases"}</small></div>
          <div><FlaskConical /><span>MISSED BOTH</span><strong>{misses.toLocaleString()}</strong><small>{mixed.toLocaleString()} mixed outcomes retained</small></div>
        </div>
        <section className="replay-analysis-grid">
          <div className="panel replay-chart-panel">
            <div className="panel-head">
              <div><span className="kicker">COMPARABLE ERROR · TEST SPLIT</span><h2>Baseline scorecard</h2></div>
              <span className="chart-note">
                {filters.variable === "ALL" ? "threshold-normalized MAE" : `MAE in ${filters.variable.replace("_", " ")}`} · {filtered.length.toLocaleString()} cases
              </span>
            </div>
            <p className="scorecard-explainer">Lower is better. When all variables are selected, each error is divided by its event threshold so rain, temperature, and wind can be compared fairly.</p>
            <div className="replay-error-chart">
              {filtered.length ? (
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={errorData} layout="vertical" margin={{ left: 12, right: 24 }}>
                    <CartesianGrid stroke="var(--chart-grid)" horizontal={false} />
                    <XAxis type="number" stroke="var(--muted-foreground)" tickLine={false} axisLine={false} />
                    <YAxis type="category" dataKey="baseline" width={112} stroke="var(--muted-foreground)" tickLine={false} axisLine={false} />
                    <Tooltip
                      formatter={(value) => typeof value === "number" ? value.toFixed(3) : "—"}
                      contentStyle={{ background: "var(--popover)", borderColor: "var(--border)" }}
                    />
                    <Bar dataKey="error" radius={[0, 3, 3, 0]}>
                      {errorData.map((item) => <Cell key={item.baseline} fill={item.color} />)}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              ) : <p className="chart-empty">No held-out cases match these filters.</p>}
            </div>
          </div>
          <div className="panel replay-outcome-panel">
            <span className="kicker">OUTCOME MIX</span>
            <h2>Wins, misses, and mixed results</h2>
            <div className="outcome-track" aria-label={`${wins} wins, ${misses} misses, ${mixed} mixed outcomes`}>
              <i className="outcome-win" style={{ width: `${filtered.length ? wins / filtered.length * 100 : 0}%` }} />
              <i className="outcome-mixed" style={{ width: `${filtered.length ? mixed / filtered.length * 100 : 0}%` }} />
              <i className="outcome-miss" style={{ width: `${filtered.length ? misses / filtered.length * 100 : 0}%` }} />
            </div>
            <div className="outcome-legend">
              <span><i className="outcome-win" />WIN <b>{wins}</b></span>
              <span><i className="outcome-mixed" />MIXED <b>{mixed}</b></span>
              <span><i className="outcome-miss" />MISS <b>{misses}</b></span>
            </div>
            <p>Each row is a distinct held-out region, variable, lead, and valid-time context. The full test set remains available below.</p>
          </div>
        </section>
        <div className="control-deck four replay-filters">
          <SelectControl label="Region" value={filters.region} onChange={(value) => onFilterChange("region", value)} options={[{ value: "ALL", label: "All regions" }, ...["KWG", "BOB", "IGP"].map((value) => ({ value, label: value }))]} />
          <SelectControl label="Variable" value={filters.variable} onChange={(value) => onFilterChange("variable", value)} options={[{ value: "ALL", label: "All variables" }, ...["precipitation", "temperature", "wind_speed"].map((value) => ({ value, label: value.replace("_", " ") }))]} />
          <SelectControl label="Lead time" value={filters.lead} onChange={(value) => onFilterChange("lead", value)} options={[{ value: "ALL", label: "All leads" }, ...[24, 48, 72, 96, 120].map((value) => ({ value: String(value), label: `+${value} hours` }))]} />
          <SelectControl label="Outcome" value={filters.outcome} onChange={(value) => onFilterChange("outcome", value)} options={[{ value: "ALL", label: "All outcomes" }, { value: "WIN", label: "Wins" }, { value: "MIXED", label: "Mixed" }, { value: "MISS", label: "Misses" }]} />
        </div>
        <div className="replay-list">
          {rows.length === 0 ? (
            <div className="page-state">
              {APP_DATA_MODE === "live"
                ? "The frozen REAL_12M held-out inference artifact is not available to this API deployment."
                : "No replay cases are available from the selected data source."}
            </div>
          ) : filtered.length === 0 ? (
            <div className="page-state">No held-out cases match the selected filters.</div>
          ) : (
            pageRows.map((e, i) => {
              const result = e.outcome ?? "MIXED";
              const ResultIcon = result === "WIN" ? CheckCircle2 : result === "MISS" ? FlaskConical : ArrowLeftRight;
              return (
                <Link
                  key={e.event_id}
                  to="/replay/$eventId"
                  params={{ eventId: e.event_id }}
                  className={`replay-card ${result.toLowerCase()}`}
                >
                  <div className="case-index">{String(filters.page * pageSize + i + 1).padStart(2, "0")}</div>
                  <div>
                    <span className="kicker">
                      {e.region} / {e.variable.replace("_", " ")} / +{e.lead_hours ?? 0}H · {e.valid_time.slice(0, 10)}
                    </span>
                    <h2>{e.label}</h2>
                    <p>{e.headline}</p>
                    <div className="case-errors">
                      <span>SYNOPTIQ <b>{typeof e.synoptiq_error === "number" ? e.synoptiq_error.toFixed(2) : "—"}</b></span>
                      <span>NAIVE <b>{typeof e.naive_average_error === "number" ? e.naive_average_error.toFixed(2) : "—"}</b></span>
                      <span>TOP SOURCE <b>{typeof e.single_model_error === "number" ? e.single_model_error.toFixed(2) : "—"}</b></span>
                    </div>
                  </div>
                  <div className="case-flavor">
                    <ResultIcon />
                    {result}
                  </div>
                  <ArrowRight className="case-arrow" />
                </Link>
              );
            })
          )}
        </div>
        {filtered.length > 0 && (
          <div className="replay-pagination">
            <span>{(filters.page * pageSize + 1).toLocaleString()}–{Math.min((filters.page + 1) * pageSize, filtered.length).toLocaleString()} of {filtered.length.toLocaleString()} held-out cases</span>
            <div>
              <button type="button" aria-label="Previous page" disabled={filters.page === 0} onClick={() => onPageChange((current) => Math.max(0, current - 1))}><ChevronLeft /></button>
              <b>{filters.page + 1} / {pageCount}</b>
              <button type="button" aria-label="Next page" disabled={filters.page + 1 >= pageCount} onClick={() => onPageChange((current) => Math.min(pageCount - 1, current + 1))}><ChevronRight /></button>
            </div>
          </div>
        )}
      </div>
    </>
  );
}
