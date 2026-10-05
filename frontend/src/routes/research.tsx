import { createFileRoute } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import {
  Activity, AlertTriangle, BrainCircuit, CheckCircle2, Crosshair, FlaskConical,
  Minus, Scale, ShieldQuestion, TrendingDown, TrendingUp,
} from "lucide-react";
import { APP_DATA_MODE } from "@/features/data/queries";
import { SectionHeading } from "@/features/shared/SectionHeading";
import { TopBar } from "@/features/shared/AppShell";
import { DataStatus } from "@/features/shared/DataStatus";
import { routeHead } from "@/features/shared/RouteMeta";
import { Button } from "@/components/ui/button";
import {
  rpiQuery, researchAiQuery, researchBaselinesQuery, researchDiagnosticsQuery,
  researchHardDaysQuery, researchImdQuery, researchOracleQuery, researchSummaryQuery,
  researchTrustQuery,
} from "@/features/research/queries";
import { archiveDatesQuery, peakPreservationQuery, regimeRouterQuery } from "@/features/ops/queries";
import type { BaselineRow, HardDayRow, OracleRow, RpiAlert } from "@/features/research/queries";

export const Route = createFileRoute("/research")({
  head: () =>
    routeHead(
      "Proof & Research",
      "Baselines, oracle regret, hard-day skill, IMD classes, trust audit and the Risk Priority Index — computed from the archived 12-month real record.",
    ),
  component: Page,
});

const fmt = (v: number | null | undefined, d = 3) =>
  v === null || v === undefined || !Number.isFinite(v) ? "—" : v.toFixed(d);
const pct = (v: number | null | undefined) =>
  v === null || v === undefined || !Number.isFinite(v) ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(1)}%`;
const regionLabel = (r: string) => r.replace(/_/g, " ");
const varLabel = (v: string) => v.replace(/_/g, " ");
const publicCopy = (value: string) => value.replaceAll(/blend_gain_pct/gi, "Synoptiq gain").replaceAll(/blend/gi, "Synoptiq");

function Panel({ kicker, title, children }: { kicker: string; title: string; children: React.ReactNode }) {
  return (
    <section className="panel research-panel">
      <div className="panel-head">
        <div>
          <span className="kicker">{kicker}</span>
          <h2>{title}</h2>
        </div>
      </div>
      {children}
    </section>
  );
}

function ResearchTable({ head, rows }: { head: string[]; rows: (string | React.ReactNode)[][] }) {
  return (
    <div className="research-table-wrap">
      <table className="research-table">
        <thead>
          <tr>{head.map((h) => <th key={h}>{h}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>{r.map((c, j) => <td key={j}>{c}</td>)}</tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function LevelBadge({ level }: { level: string }) {
  return <span className={`rpi-level rpi-${level.toLowerCase()}`}>{level}</span>;
}

function RpiPanel() {
  const [date, setDate] = useState<string | null>(null);
  const q = useQuery(rpiQuery(date));
  const archive = useQuery(archiveDatesQuery);
  const quickDates = [
    { label: "14 Jul 2025 (monsoon)", value: "2025-07-14" },
    { label: "15 Aug 2025", value: "2025-08-15" },
  ].filter((item) => archive.data?.dates.includes(item.value));
  const quick = [
    { label: "Latest", value: null },
    ...quickDates,
  ];
  return (
    <Panel kicker="OPERATIONAL ALERTS" title="Risk Priority Index">
      <div className="rpi-controls">
        {quick.map((c) => (
          <Button
            key={c.label}
            size="sm"
            variant={(date ?? "") === (c.value ?? "") ? "default" : "outline"}
            onClick={() => setDate(c.value)}
          >
            {c.label}
          </Button>
        ))}
        <input
          type="date"
          className="rpi-date"
          min={archive.data?.earliest ?? undefined}
          max={archive.data?.latest ?? undefined}
          onChange={(e) => setDate(e.target.value || null)}
          aria-label="Pick an archived day"
        />
      </div>
      {q.isPending && <p className="research-note">Loading archived cycle…</p>}
      {q.isError && (
        <p className="research-note error" role="alert">
          {q.error instanceof Error ? q.error.message : "RPI unavailable."}
        </p>
      )}
      {q.data && (
        <>
          <p className="research-note">
            As of <strong>{q.data.as_of}</strong> · severity follows IMD classes
            (rain 15.6 / 64.5 / 115.5 mm, heat 40 °C, wind 50 km/h), scaled by issued
            trust and lead-time decay.
          </p>
          {q.data.alerts.length === 0 ? (
            <p className="research-note">
              No active alerts on this day — the honest state. Pick a monsoon date to
              see the index fire.
            </p>
          ) : (
            <ul className="rpi-list">
              {q.data.alerts.map((a: RpiAlert, i) => (
                <li key={i}>
                  <LevelBadge level={a.level} />
                  <div>
                    <strong>{regionLabel(a.region)} · {varLabel(a.variable)} · +{a.lead_hours}h</strong>
                    <span>
                      {a.value} · {a.event.replace(/_/g, " ")} · trust {a.trust} · spread {a.model_spread}
                    </span>
                  </div>
                  <em>RPI {a.rpi_score}</em>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </Panel>
  );
}

function Sparkline({ points, keys }: { points: { date: string; rmse: Record<string, number | null> }[]; keys: string[] }) {
  const w = 1120, h = 300, pad = 58;
  const all = points.flatMap((p) => keys.map((k) => p.rmse[k]).filter((v): v is number => v != null && Number.isFinite(v)));
  if (!points.length || !all.length) return <p className="research-note">No rolling-skill points.</p>;
  const min = Math.min(...all), max = Math.max(...all);
  const x = (i: number) => pad + (i / Math.max(1, points.length - 1)) * (w - 2 * pad);
  const y = (v: number) => h - pad - ((v - min) / Math.max(1e-9, max - min)) * (h - 2 * pad);
  const colors: Record<string, string> = { GFS: "#7dd3fc", IFS: "#fbbf24", AIFS: "#a78bfa", blend: "#34d399" };
  const tickIndexes = Array.from(new Set([0, Math.floor((points.length - 1) / 3), Math.floor((points.length - 1) * 2 / 3), points.length - 1]));
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="skill-chart" role="img" aria-label="Rolling 30-day RMSE">
      {[0, 0.5, 1].map((fraction) => {
        const value = min + (max - min) * fraction;
        const yValue = y(value);
        return <g key={fraction}><line x1={pad} x2={w - pad} y1={yValue} y2={yValue} className="skill-grid-line" /><text x={pad - 10} y={yValue + 4} textAnchor="end" className="chart-label">{value.toFixed(2)}</text></g>;
      })}
      {tickIndexes.map((index) => <g key={`tick-${index}`}><line x1={x(index)} x2={x(index)} y1={pad} y2={h - pad} className="skill-time-line" /><text x={x(index)} y={h - 14} textAnchor="middle" className="chart-label">{points[index]?.date}</text></g>)}
      {keys.map((k) => (
        <polyline
          key={k}
          className="skill-line"
          fill="none"
          stroke={colors[k] ?? "#94a3b8"}
          strokeWidth={k === "blend" ? 2.5 : 1.5}
          points={points
            .map((p, i) => (p.rmse[k] != null && Number.isFinite(p.rmse[k] as number) ? `${x(i)},${y(p.rmse[k] as number)}` : null))
            .filter(Boolean)
            .join(" ")}
        />
      ))}
      {keys.map((k, i) => (
        <text key={k} x={w - pad - (keys.length - 1 - i) * 86} y={22} className="chart-label" fill={colors[k] ?? "#94a3b8"}>{k === "blend" ? "Synoptiq" : k}</text>
      ))}
      {points.flatMap((p, i) => keys.map((k) => p.rmse[k] != null && Number.isFinite(p.rmse[k] as number) ? (
        <circle key={`${i}-${k}`} cx={x(i)} cy={y(p.rmse[k] as number)} r="3" fill={colors[k] ?? "#94a3b8"}>
          <title>{`${p.date} · ${k} RMSE ${Number(p.rmse[k]).toFixed(3)}`}</title>
        </circle>
      ) : null))}
    </svg>
  );
}

function SkillEvolutionPanel({
  points,
}: {
  points: { date: string; region: string; variable: string; rmse: Record<string, number | null> }[];
}) {
  const cells = Array.from(new Set(points.map((p) => `${p.region}:${p.variable}`)));
  const [cell, setCell] = useState("");
  const selectedCell = cell || cells[0] || "";
  const sel = points.filter((p) => `${p.region}:${p.variable}` === selectedCell);
  const legend = [{ key: "GFS", label: "GFS" }, { key: "IFS", label: "IFS" }, { key: "AIFS", label: "AIFS" }, { key: "blend", label: "Synoptiq" }];
  return (
    <Panel kicker="DYNAMIC SKILL" title="Rolling 30-day RMSE — the best model changes through the year">
      <div className="rpi-controls">
        {cells.map((c) => (
          <Button key={c} size="sm" variant={c === selectedCell ? "default" : "outline"} onClick={() => setCell(c)}>
            {regionLabel(c.split(":")[0] ?? "")} · {varLabel(c.split(":")[1] ?? "")}
          </Button>
        ))}
      </div>
      <div className="skill-legend" aria-label="Chart legend">
        {legend.map((item) => <span key={item.key} className={`skill-legend-${item.key}`}><i />{item.label}</span>)}
        <small>RMSE · lower is better · weekly points across the archive</small>
      </div>
      <Sparkline points={sel} keys={["GFS", "IFS", "AIFS", "blend"]} />
    </Panel>
  );
}


function PeakPreservationPanel() {
  const q = useQuery(peakPreservationQuery);
  if (q.isPending || q.isError || !q.data)
    return <Panel kicker="EXTREME LAYER" title="Peak preservation & the drizzle problem">
      <p className="research-note">
        {q.isError ? (q.error instanceof Error ? q.error.message : "Peak-preservation artifact unavailable.")
          : "Loading the extreme-weather audit…"}
      </p>
    </Panel>;
  return (
    <Panel kicker="EXTREME LAYER · EVT + CONFORMAL" title="Peak preservation & the drizzle problem">
      <p className="research-note">{publicCopy(q.data.scope)}</p>
      <ResearchTable
        head={["Variable", "Alpha (calibrated)", "p99 ratio: Synoptiq / best single / peak-preserved", "Conformal 90% band", "Measured test coverage", "GPD tail (n, ξ, σ)"]}
        rows={Object.entries(q.data.variables).map(([variable, e]) => {
          const a = e.peak_smearing_audit;
          const status = e.alpha > 0 ? `ACTIVE at ${e.alpha}` : "INACTIVE (α=0)";
          return [
            varLabel(variable),
            <span key="a" className={e.alpha > 0 ? "pos" : ""}>{status}</span>,
            `${fmt(a.p99_ratio_blend, 3)} / ${fmt(a.p99_ratio_best_single, 3)} / ${fmt(a.p99_ratio_peak_preserved, 3)}`,
            e.conformal.q90 === null ? "not estimable" : `± ${fmt(e.conformal.q90, 2)}`,
            e.conformal.measured_test_coverage === null || e.conformal.measured_test_coverage === undefined
              ? "—" : `${(e.conformal.measured_test_coverage * 100).toFixed(1)}% of nominal 90%`,
            `${e.gpd_tail.n}, ${fmt(e.gpd_tail.xi, 3)}, ${fmt(e.gpd_tail.sigma, 3)}`,
          ];
        })}
      />
      <ul className="bias-cards">
        {Object.entries(q.data.variables).map(([variable, e]) => (
          <li key={variable}>
            <FlaskConical />
            <span><strong>{varLabel(variable)}</strong> — {publicCopy(e.conclusion)}</span>
          </li>
        ))}
      </ul>
      <p className="research-note">
        A p99 ratio below 1.0 is the variance-reduction signature: the forecast
        field is calmer than reality at the tail. Alpha is calibrated on the
        validation split only; where validation could not justify a tail
        correction the layer stays inactive and the risk is communicated
        through the conformal band and the EVT tail instead.
      </p>
    </Panel>
  );
}

function RegimeRouterPanel() {
  const q = useQuery(regimeRouterQuery);
  if (q.isPending || q.isError || !q.data)
    return <Panel kicker="SYNOPTIC ROUTING" title="Regime router">
      <p className="research-note">
        {q.isError ? (q.error instanceof Error ? q.error.message : "Regime router artifact unavailable.")
          : "Loading the regime-conditioned weight profiles…"}
      </p>
    </Panel>;
  const r = q.data;
  return (
    <Panel kicker="SYNOPTIC ROUTING · POLICY SHIFT" title="Regime router — what the regime changed">
      <div className="research-callout">
        <Activity />
        <span>
          Current regime ({r.current.as_of}): <strong>{r.current.profile?.label ?? r.current.regime}</strong>
          {r.current.profile?.synoptic ? ` — ${r.current.profile.synoptic}` : ""}
        </span>
      </div>
      <ResearchTable
        head={["Regime", "Contexts", "Mean issued weights", "Best model (test)", "Policy shift"]}
        rows={r.profiles.map((p) => [
          p.label,
          p.n_contexts.toLocaleString(),
          Object.entries(p.mean_weights).map(([m, w]) => `${m} ${(w * 100).toFixed(0)}%`).join(" · "),
          p.best_test_model ?? "—",
          p.policy_shift.material ? <span key="s" className="pos">{p.policy_shift.statement}</span> : "no material shift",
        ])}
      />
      <p className="research-note">
        Regime probabilities come from the transparent rule-based detector
        (app/regime_detector.py) and already feed the meta-model as features;
        this panel makes the resulting weight conditioning explicit, measured
        and quotable. Global all-weather profile:{" "}
        {Object.entries(r.global_mean_weights).map(([m, w]) => `${m} ${(w * 100).toFixed(0)}%`).join(" · ")}.
      </p>
    </Panel>
  );
}

function Page() {
  const summary = useQuery(researchSummaryQuery);
  const baselines = useQuery(researchBaselinesQuery);
  const hardDays = useQuery(researchHardDaysQuery);
  const oracle = useQuery(researchOracleQuery);
  const ai = useQuery(researchAiQuery);
  const imd = useQuery(researchImdQuery);
  const trust = useQuery(researchTrustQuery);
  const diagnostics = useQuery(researchDiagnosticsQuery);
  const mode = APP_DATA_MODE;

  if (summary.isPending)
    return (
      <>
        <TopBar mode={mode} />
        <div className="page-state" role="status">
          <h2>LOADING RESEARCH SUITE</h2>
          <p>Loading the archived 12-month proof artifacts.</p>
        </div>
      </>
    );

  if (summary.isError)
    return (
      <>
        <TopBar mode={mode} />
        <div className="page-state" role="alert">
          <h2>RESEARCH SUITE UNAVAILABLE</h2>
          <p>
            {summary.error instanceof Error
              ? summary.error.message
              : "The research artifacts could not be reached."}
          </p>
          <p className="research-note">
            Regenerate with: <code>{summary.error instanceof Error ? "" : "python backend/scripts/research_suite.py …"}</code>
          </p>
          <Button onClick={() => void summary.refetch()}>Retry</Button>
        </div>
      </>
    );

  const s = summary.data!;
  const aiPositive = (ai.data?.rows ?? []).filter((r) => (r.ai_value_pct ?? 0) > 0).length;
  const oraclePct = s.oracle_overall.oracle_advantage_recovered_pct;

  return (
    <>
      <TopBar mode={mode} />
      <div className="page">
        <SectionHeading
          eyebrow="PROOF & RESEARCH · READ-ONLY ARTIFACTS"
          title="The evidence pack"
          copy={`Every number on this page is computed offline from the archived REAL_12M forecast record (${s.source.contexts.toLocaleString()} contexts, ${s.source.period.start} → ${s.source.period.end}, untouched test ${s.source.test_start} → ${s.source.test_end}) and served read-only.`}
          action={<DataStatus mode={mode} />}
        />

        <div className="proof-strip">
          <div>
            <Crosshair />
            <span>ORACLE ADVANTAGE RECOVERED</span>
            <strong>{oraclePct === null ? "—" : `${oraclePct.toFixed(1)}%`}</strong>
            <small>of the hindsight-oracle's MAE edge over the best fixed model</small>
          </div>
          <div>
            <BrainCircuit />
            <span>AI MODEL EARNS ITS SLOT</span>
            <strong>{aiPositive}/9 cells</strong>
            <small>GFS+IFS-only forecast loses to the AI-inclusive forecast</small>
          </div>
          <div>
            <CheckCircle2 />
            <span>BIAS CORRECTION HELPS</span>
            <div className="bias-summary">
              {["precipitation", "temperature", "wind_speed"].map((variable) => {
                const row = s.calibration_sanity.rows[variable];
                return <span key={variable}><b>{(row?.improvement_pct ?? 0).toFixed(0)}%</b><small>{varLabel(variable)}</small></span>;
              })}
            </div>
            <small>test MAE reduction, labelled by variable</small>
          </div>
        </div>

        <RpiPanel />

        {diagnostics.data && <SkillEvolutionPanel points={diagnostics.data.skill_evolution.points} />}

          <Panel kicker="PROFESSIONAL BASELINES" title="Beating persistence and climatology, with confidence intervals">
          {baselines.data && (
            <>
              <ResearchTable
                head={["Region · variable", "Synoptiq", "Best single", "Naive avg", "Persistence", "Climatology", "Synoptiq vs best single (95% CI)"]}
                rows={baselines.data.rows.map((r: BaselineRow) => [
                  `${regionLabel(r.region)} · ${varLabel(r.variable)}`,
                  <strong key="b">{fmt(r.mae["blend"])}</strong>,
                  `${r.best_single_model} ${fmt(r.mae[r.best_single_model])}`,
                  fmt(r.mae["naive_average"]),
                  fmt(r.mae["persistence"]),
                  fmt(r.mae["climatology"]),
                  `${r.bootstrap.blend_vs_best_single.improvement_pct >= 0 ? "+" : ""}${r.bootstrap.blend_vs_best_single.improvement_pct.toFixed(1)}% [${r.bootstrap.blend_vs_best_single.ci95_improvement_pct[0].toFixed(1)}, ${r.bootstrap.blend_vs_best_single.ci95_improvement_pct[1].toFixed(1)}]`,
                ])}
              />
              <p className="research-note">{publicCopy(baselines.data.note)} {baselines.data.rows[0]?.bootstrap.method}</p>
            </>
          )}
        </Panel>

        <Panel kicker="SKILL ON HARD DAYS" title="Does Synoptiq earn its keep when models split?">
          {hardDays.data && (
            <>
              <ResearchTable
                head={["Region · variable", "Easy days gain", "Hard days gain", "Hard-day n", "Verdict"]}
                rows={hardDays.data.rows.map((r: HardDayRow) => {
                  const e = r.blend_gain_pct.easy ?? 0, h = r.blend_gain_pct.hard ?? 0;
                  const verdict = h > e ? "advantage concentrates on hard days" : h > 0 ? "positive but not hard-day concentrated" : "no hard-day advantage (honest miss)";
                  return [
                    `${regionLabel(r.region)} · ${varLabel(r.variable)}`,
                    <span key="e" className={e >= 0 ? "pos" : "neg"}>{pct(r.blend_gain_pct.easy)}</span>,
                    <span key="h" className={h >= 0 ? "pos" : "neg"}>{pct(r.blend_gain_pct.hard)}</span>,
                    r.hard.n,
                    verdict,
                  ];
                })}
              />
              <p className="research-note">{publicCopy(hardDays.data.rows[0]?.definition ?? "")}. {publicCopy(hardDays.data.claim_template ?? "")}</p>
            </>
          )}
        </Panel>

        <Panel kicker="ORACLE REGRET" title="Why not always use your best model?">
          {oracle.data && (
            <>
              <div className="research-callout">
                <Scale />
                <span>
                  Synoptiq recovers <strong>{oraclePct === null ? "—" : `${oraclePct.toFixed(1)}%`}</strong> of the
                  hindsight-oracle's MAE advantage over the best fixed single model
                  (oracle {fmt(oracle.data.overall.oracle_mae)} · best fixed {fmt(oracle.data.overall.best_fixed_mae)} ·
                  Synoptiq {fmt(oracle.data.overall.blend_mae)}).
                </span>
              </div>
              <ResearchTable
                head={["Region · variable", "Oracle", "Best fixed", "Synoptiq", "Recovered"]}
                rows={oracle.data.rows.map((r: OracleRow) => [
                  `${regionLabel(r.region)} · ${varLabel(r.variable)}`,
                  fmt(r.oracle_mae),
                  `${r.best_fixed_model} ${fmt(r.best_fixed_mae)}`,
                  fmt(r.blend_mae),
                  pct(r.oracle_advantage_recovered_pct),
                ])}
              />
              <p className="research-note">{publicCopy(oracle.data.definition)}</p>
            </>
          )}
        </Panel>

        <Panel kicker="AI vs PHYSICS" title="What does adding the AI model buy?">
          {ai.data && (
            <>
              <ResearchTable
                head={["Region · variable", "GFS+IFS only", "AIFS only", "Synoptiq", "AI value"]}
                rows={ai.data.rows.map((r) => [
                  `${regionLabel(r.region)} · ${varLabel(r.variable)}`,
                  fmt(r.physics_only_mae),
                  fmt(r.aifs_only_mae),
                  fmt(r.full_blend_mae),
                  <span key="v" className={(r.ai_value_pct ?? 0) >= 0 ? "pos" : "neg"}>{pct(r.ai_value_pct)}</span>,
                ])}
              />
              <p className="research-note">{publicCopy(ai.data.definition)}</p>
            </>
          )}
        </Panel>

        <Panel kicker="IMD RAINFALL CLASSES" title="Verified in the IMD's own vocabulary">
          {imd.data && (
            <>
              <ResearchTable
                head={["Class", "Threshold", "Events (year)", "Synoptiq POD", "Synoptiq FAR", "Synoptiq CSI", "Best single CSI"]}
                rows={Object.entries(imd.data.full_year).map(([cls, sys]) => {
                  const blend = sys["blend"]!;
                  const others = (["GFS", "IFS", "AIFS"] as const)
                    .map((m) => ({ m, csi: sys[m]!.csi }))
                    .filter((o) => o.csi !== null)
                    .sort((a, b) => (b.csi ?? 0) - (a.csi ?? 0));
                  const few = blend.n_events < 20;
                  const best = others[0];
                  return [
                    cls.replace(/_/g, " "),
                    `≥ ${imd.data!.thresholds_mm[cls] ?? "—"} mm`,
                    blend.n_events,
                    few ? "too few events" : fmt(blend.pod, 2),
                    few ? "—" : fmt(blend.far, 2),
                    few ? "not estimable" : fmt(blend.csi, 2),
                    few || !best ? "—" : `${best.m} ${fmt(best.csi, 2)}`,
                  ];
                })}
              />
              <p className="research-note">{publicCopy(imd.data.note)} Heavy-rain classes over the year have too few zone-mean events for stable estimates — shown honestly rather than inflated.</p>
            </>
          )}
        </Panel>

        <Panel kicker="CONFIDENCE CALIBRATION AUDIT" title="Did the system earn its trust?">
          {trust.data && (
            <>
              <ResearchTable
                head={["Issued trust tier", "Contexts", "Mean issued trust", "Observed mean |error|"]}
                rows={trust.data.tiers.map((t) => [
                  t.tier,
                  t.n.toLocaleString(),
                  fmt(t.mean_trust, 2),
                  fmt(t.mean_abs_error),
                ])}
              />
              <div className="research-callout">
                {trust.data.trust_vs_error_rank_correlation ? <TrendingDown /> : <ShieldQuestion />}
                <span>
                  Spearman ρ(trust, |error|) = <strong>{fmt(trust.data.trust_vs_error_rank_correlation?.spearman_rho, 3)}</strong>
                  {trust.data.trust_vs_error_rank_correlation && trust.data.trust_vs_error_rank_correlation.p_value < 0.001
                    ? " (significant: higher trust really does mean lower error — though not perfectly monotone across tiers, shown as-is)"
                    : ""}
                  . Abstained forecasts: {fmt(trust.data.abstain_audit.abstained.mean_abs_error)} vs
                  kept {fmt(trust.data.abstain_audit.not_abstained.mean_abs_error)} mean |error|.
                </span>
              </div>
              <p className="research-note">{trust.data.interpretation}</p>
            </>
          )}
        </Panel>

        <RegimeRouterPanel />

        <PeakPreservationPanel />

        {diagnostics.data && (
          <>
            <Panel kicker="LEAD-TIME Crossovers" title="Where the best model changes">
              <ul className="crossover-list">
                {diagnostics.data.crossovers.map((c, i) => (
                  <li key={i}>
                    <Activity />
                    <span>
                      {c.crossover_statement ?? `${regionLabel(c.region)} · ${varLabel(c.variable)}: no crossover in the record`}
                    </span>
                  </li>
                ))}
              </ul>
            </Panel>

            <Panel kicker="MODEL BIAS FINGERPRINTS" title="Concrete, checkable facts the system learned">
              <ul className="bias-cards">
                {diagnostics.data.bias_fingerprints.cards.slice(0, 8).map((c, i) => (
                  <li key={i}>
                    <FlaskConical />
                    <span>{c.statement}</span>
                  </li>
                ))}
              </ul>
              <p className="research-note">
                {diagnostics.data.bias_fingerprints.scope}, minimum {diagnostics.data.bias_fingerprints.min_days} days per card.
              </p>
            </Panel>
          </>
        )}

        <div className="target-note">
          <Minus />
          <span>Offline artifacts are regenerated by the Operations workflow after each validated retrain. The model card is rebuilt from the same evidence.</span>
        </div>
      </div>
    </>
  );
}
