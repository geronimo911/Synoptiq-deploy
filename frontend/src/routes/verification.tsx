import { createFileRoute } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { CheckCircle2, Minus, TrendingDown, TrendingUp } from "lucide-react";
import { APP_DATA_MODE, systemStatusQuery, verificationQuery } from "@/features/data/queries";
import { SectionHeading } from "@/features/shared/SectionHeading";
import { TopBar } from "@/features/shared/AppShell";
import { DataStatus } from "@/features/shared/DataStatus";
import { routeHead } from "@/features/shared/RouteMeta";
import { Button } from "@/components/ui/button";

export const Route = createFileRoute("/verification")({
  head: () =>
    routeHead(
      "Verification Scorecard",
      "Review held-out Synoptiq performance across the pilot regions and forecast variables.",
    ),
  component: Page,
});

function Page() {
  const query = useQuery(verificationQuery);
  const statusQuery = useQuery(systemStatusQuery);
  const { data } = query;
  const rows = Array.isArray(data) ? data : [];
  const mode = APP_DATA_MODE;
  const regions = ["KWG", "BOB", "IGP"] as const;
  const variables = ["precipitation", "temperature", "wind_speed"] as const;
  const scoredRows = rows.filter(
    (row) => row.relative_improvement !== null && Number.isFinite(row.relative_improvement),
  );
  const positiveRows = scoredRows
    .filter((row) => row.relative_improvement! > 0)
    .sort((a, b) => b.relative_improvement! - a.relative_improvement!);
  const best = positiveRows[0] ?? null;
  const verifiedRegionCount = new Set(rows.map((row) => row.region)).size;
  const verifiedVariableCount = new Set(rows.map((row) => row.variable)).size;
  const contextsReported = rows.length > 0 && rows.every(
    (row) => typeof row.test_contexts === "number"
      && Number.isFinite(row.test_contexts)
      && row.test_contexts === rows[0].test_contexts,
  );
  const contextsPerScore = contextsReported ? rows[0].test_contexts ?? null : null;
  const testPeriod = statusQuery.data?.test_period;
  const testPeriodLabel = testPeriod ? ` · ${testPeriod.start} to ${testPeriod.end}` : "";
  const formatImprovement = (value: number | null) => {
    if (value === null || !Number.isFinite(value)) return "NOT ESTIMABLE";
    const percent = value * 100;
    return `${percent > 0 ? "+" : ""}${percent.toFixed(1)}%`;
  };
  const formatScore = (value: number | null) =>
    value !== null && Number.isFinite(value) ? value.toFixed(3) : "NOT ESTIMABLE";

  if (query.isPending || query.isError)
    return (
      <>
        <TopBar mode={mode} />
        <div className="page-state" role={query.isError ? "alert" : "status"}>
          <h2>{query.isError ? "REAL VERIFICATION UNAVAILABLE" : "LOADING VERIFICATION"}</h2>
          <p>
            {query.isError
              ? query.error instanceof Error
                ? query.error.message
                : "The real verification API could not be reached."
              : "Loading held-out results from the active real model artifacts."}
          </p>
          {query.isError && <Button onClick={() => void query.refetch()}>Retry</Button>}
        </div>
      </>
    );

  if (!rows.length)
    return (
      <>
        <TopBar mode={mode} />
        <div className="page-state">
          No held-out verification results are available yet. Run the real-data training and
          evaluation pipeline first.
        </div>
      </>
    );

  return (
    <>
      <TopBar mode={mode} />
      <div className="page">
        <SectionHeading
          eyebrow={
            mode === "live"
              ? `HELD-OUT TEST${testPeriodLabel}`
              : "DEMO VALIDATION"
          }
          title="The complete scorecard"
          copy="Each cell uses the metric and event threshold recorded in the active held-out REAL_12M evaluation artifact."
          action={<DataStatus mode={mode} />}
        />

        <div className="proof-strip">
          <div>
            <CheckCircle2 />
            <span>REAL SCORECARD ROWS</span>
            <strong>{rows.length}</strong>
            <small>{verifiedRegionCount} regions · {verifiedVariableCount} variables</small>
          </div>
          <div>
            <TrendingUp />
            <span>BEST VALIDATED IMPROVEMENT</span>
            <strong>{best ? formatImprovement(best.relative_improvement) : "NO POSITIVE RESULT"}</strong>
            <small>
              {best
                ? `${best.region} · ${best.variable.replace("_", " ")} · ${best.metric}`
                : "No positive finite improvement in the active artifact"}
            </small>
          </div>
          <div>
            <Minus />
            <span>HELD-OUT CONTEXTS / SCORE</span>
            <strong>{contextsPerScore === null ? "NOT REPORTED" : contextsPerScore}</strong>
            <small>as recorded in the REAL_12M evaluation artifacts</small>
          </div>
        </div>

        <section className="matrix-panel panel">
          <div className="panel-head">
            <div>
              <span className="kicker">VARIABLE-APPROPRIATE METRICS</span>
              <h2>Region × variable matrix</h2>
            </div>
            <div className="matrix-key">
              <span className="negative">BELOW BASELINE</span>
              <span>NEUTRAL</span>
              <span className="positive">IMPROVEMENT</span>
            </div>
          </div>

          <div className="score-matrix">
            <div />
            {variables.map((v) => (
              <div className="matrix-col" key={v}>
                {v.replace("_", " ")}
              </div>
            ))}
            {regions.map((region) => (
              <div className="matrix-row-group" key={region}>
                <div className="matrix-region">{region}</div>
                {variables.map((variable) => {
                  const row = rows.find((r) => r.region === region && r.variable === variable);
                  if (!row)
                    return (
                      <div className="score-cell empty" key={variable}>
                        —
                      </div>
                    );
                  const hasImprovement = row.relative_improvement !== null
                    && Number.isFinite(row.relative_improvement);
                  const tone = !hasImprovement
                    ? "neutral"
                    : row.relative_improvement! < 0
                      ? "negative"
                      : row.meets_target === true
                        ? "positive"
                        : "neutral";
                  return (
                    <div className={`score-cell ${tone}`} key={variable}>
                      <div>
                        {!hasImprovement
                          ? <Minus />
                          : row.relative_improvement! < 0
                            ? <TrendingDown />
                            : <TrendingUp />}
                        <strong>{formatImprovement(row.relative_improvement)}</strong>
                      </div>
                      <span>
                        {row.metric}: Synoptiq {formatScore(row.synoptiq_score)}
                      </span>
                      <span>
                        {row.best_single_model} {formatScore(row.best_single_model_score)}
                      </span>
                      <small>
                        {!hasImprovement
                          ? "NOT ESTIMABLE"
                          : row.meets_target == null
                            ? "BENCHMARK"
                            : row.meets_target
                              ? "TARGET MET"
                              : "TARGET NOT MET"}
                      </small>
                    </div>
                  );
                })}
              </div>
            ))}
          </div>

          <div className="target-note">
            <Minus />
            <span>
              Rainfall uses the artifact's held-out categorical CSI threshold. Temperature and wind
              use held-out RMSE; the evaluation artifacts also retain continuous Bias diagnostics.
            </span>
          </div>
        </section>
      </div>
    </>
  );
}
