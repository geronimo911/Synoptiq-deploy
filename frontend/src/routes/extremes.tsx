import { createFileRoute } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { z } from "zod";
import { zodValidator } from "@tanstack/zod-adapter";
import { CloudRain, Flame, Wind, ShieldCheck } from "lucide-react";
import { APP_DATA_MODE, extremesQuery, leadTimesQuery, systemStatusQuery } from "@/features/data/queries";
import type { RegionCode } from "@/features/data/types";
import { SelectControl } from "@/features/shared/Controls";
import { SectionHeading } from "@/features/shared/SectionHeading";
import { TopBar } from "@/features/shared/AppShell";
import { routeHead } from "@/features/shared/RouteMeta";
const schema = z.object({
  region: z.enum(["KWG", "BOB", "IGP"]).catch("BOB"),
  lead: z.coerce.number().catch(72),
});
export const Route = createFileRoute("/extremes")({
  validateSearch: zodValidator(schema),
  head: () =>
    routeHead(
      "Extreme Weather Guidance",
      "View calibrated heavy-rain, heat, and high-wind exceedance probabilities from Synoptiq.",
    ),
  component: Page,
});
const icons = { precipitation: CloudRain, temperature: Flame, wind_speed: Wind };
const formatProbability = (value: number) =>
  value > 0 && value < 0.005 ? "<1%" : `${Math.round(value * 100)}%`;
const formatMeasurement = (value: number, unit: string) => {
  const displayUnit: Record<string, string> = {
    deg_C: "°C",
    "mm/24h": "mm / 24h",
    "km/h": "km/h",
  };
  return `${value.toFixed(1)} ${displayUnit[unit] ?? unit}`;
};
function Page() {
  const s = Route.useSearch(),
    nav = Route.useNavigate();
  const leadsQuery = useQuery(leadTimesQuery);
  const statusQuery = useQuery(systemStatusQuery);
  const systemStatus = statusQuery.data;
  const extremesQueryResult = useQuery({
    ...extremesQuery(s.region, s.lead),
    enabled: APP_DATA_MODE === "mock" || systemStatus?.ready === true,
  });
  const r = extremesQueryResult.data;
  const leads = leadsQuery.data ?? [24, 48, 72, 96, 120];
  const guidance = Array.isArray(r?.guidance) ? r.guidance : [];
  const liveUnavailable = APP_DATA_MODE === "live" && !systemStatus?.ready;
  const set = (p: Partial<typeof s>) => nav({ to: ".", search: (q) => ({ ...q, ...p }) });
  return (
    <>
      <TopBar mode={APP_DATA_MODE} />
      <div className="page">
        <SectionHeading
          eyebrow="Threshold exceedance intelligence"
          title="Extreme weather guidance"
          copy="Compare real forecast values with hazard thresholds, model trust, spread, and bust risk."
        />
        <div className="control-deck compact">
          <SelectControl
            label="Region"
            value={s.region}
            onChange={(v) => set({ region: v as RegionCode })}
            options={["KWG", "BOB", "IGP"].map((v) => ({ value: v, label: v }))}
          />
          <SelectControl
            label="Lead time"
            value={String(s.lead)}
            onChange={(v) => set({ lead: Number(v) })}
            options={leads.map((v) => ({ value: String(v), label: `+${v} hours` }))}
          />
        </div>
        <section className="extreme-command panel" aria-label="Atmospheric risk field">
          <div className="extreme-command-copy">
            <span className="kicker">ATMOSPHERIC RISK FIELD</span>
            <h2>Three hazards. One decision window.</h2>
            <p>Synoptiq watches rainfall, heat, and wind together. The cards below show the measured forecast, threshold distance, source spread, trust, and bust risk for {s.region} at +{s.lead} hours.</p>
            <div className="extreme-signal-row"><span><i className="signal-rain" />Rainfall</span><span><i className="signal-heat" />Heat</span><span><i className="signal-wind" />Wind</span><strong>{systemStatus?.ready ? "REAL INPUTS VALIDATED" : "HISTORICAL REAL INPUTS"}</strong></div>
          </div>
          <div className="extreme-radar" aria-hidden="true"><i /><i /><i /><b>+{s.lead}H</b><span>FIELD LOCK</span></div>
        </section>
        {liveUnavailable ? (
          <div className="page-state" role="status">
            <h2>{statusQuery.isPending ? "CHECKING REAL INPUTS" : "REAL INPUTS UNAVAILABLE"}</h2>
            <p>
              {statusQuery.error instanceof Error
                ? statusQuery.error.message
                : "Extreme guidance is withheld until current real provider inputs pass validation."}
            </p>
            {statusQuery.isError && (
              <button type="button" onClick={() => void statusQuery.refetch()}>
                Retry status check
              </button>
            )}
          </div>
        ) : extremesQueryResult.isError ? (
          <div className="page-state" role="alert">
            <h2>GUIDANCE UNAVAILABLE</h2>
            <p>
              {extremesQueryResult.error instanceof Error
                ? extremesQueryResult.error.message
                : "The real extreme-guidance API could not be reached."}
            </p>
            <button type="button" onClick={() => void extremesQueryResult.refetch()}>
              Retry guidance
            </button>
          </div>
        ) : (
        <div className="threat-grid">
          {guidance.length === 0 ? (
            <div className="page-state">
              No extreme-weather guidance is available for this selection.
            </div>
          ) : (
            guidance.map((g) => {
              const Icon = icons[g.variable];
              const thresholdProximity = g.variable === "temperature" || g.variable === "wind_speed";
              const calibrated = g.calibrated
                && g.probability !== null
                && Number.isFinite(g.probability);
              const ratio = g.threshold > 0 ? g.forecast_value / g.threshold : null;
              const meterWidth = ratio === null ? 0 : Math.max(0, Math.min(100, ratio / 1.5 * 100));
              const thresholdState = thresholdProximity
                ? g.threshold_exceeded ? "THRESHOLD EXCEEDED" : "WITHIN THRESHOLD"
                : g.threshold_exceeded
                  ? "THRESHOLD EXCEEDED"
                  : ratio !== null && ratio >= 0.9
                    ? "NEAR THRESHOLD"
                    : "WITHIN THRESHOLD";
              const level = thresholdProximity
                ? g.threshold_exceeded
                  ? "exceeded"
                  : ratio !== null && ratio >= 0.9
                    ? "near"
                    : "within"
                : calibrated
                ? g.probability! >= 0.65
                  ? "calibrated-high"
                  : g.probability! >= 0.35
                    ? "calibrated-medium"
                    : "calibrated-low"
                : g.threshold_exceeded
                  ? "exceeded"
                  : thresholdState === "NEAR THRESHOLD"
                    ? "near"
                    : "within";
              const distance = Math.abs(g.threshold - g.forecast_value);
              const marginDirection = g.forecast_value === g.threshold
                ? "at threshold"
                : g.forecast_value < g.threshold
                  ? "below threshold"
                  : "above threshold";
              const trustPercent = g.trust_score !== null && g.trust_score !== undefined
                && Number.isFinite(g.trust_score)
                ? `${Math.round(g.trust_score * 100)}%`
                : "—";
              const bustPercent = g.bust_probability !== null && g.bust_probability !== undefined
                && Number.isFinite(g.bust_probability)
                ? formatProbability(g.bust_probability)
                : null;
              return (
                <section className={`threat panel ${level}`} key={g.variable}>
                  <div className="threat-icon">
                    <Icon />
                  </div>
                  <span className="kicker">{g.variable.replace("_", " ")}</span>
                  {thresholdProximity ? (
                    <div className="proximity-content">
                      <div className="proximity-heading">
                        <span className="kicker">THRESHOLD PROXIMITY</span>
                        <strong className={`threat-state ${g.threshold_exceeded ? "exceeded" : "within"}`}>
                          {thresholdState}
                        </strong>
                      </div>
                      <div className="proximity-metrics">
                        <div><span>Forecast</span><strong>{formatMeasurement(g.forecast_value, g.unit)}</strong></div>
                        <div><span>Threshold</span><strong>{formatMeasurement(g.threshold, g.unit)}</strong></div>
                        <div><span>Margin</span><strong>{formatMeasurement(distance, g.unit)} {marginDirection}</strong></div>
                        <div><span>Threshold level</span><strong>{ratio === null ? "—" : `${(ratio * 100).toFixed(1)}%`}</strong></div>
                        <div><span>Exceedance</span><strong>{g.threshold_exceeded ? "YES" : "NO"}</strong></div>
                      </div>
                      <div
                        className="proximity-gauge"
                        role="img"
                        aria-label={`${formatMeasurement(g.forecast_value, g.unit)} forecast; threshold ${formatMeasurement(g.threshold, g.unit)}`}
                      >
                        <div className="proximity-track">
                          <i style={{ width: `${meterWidth}%` }} />
                          <b className="proximity-threshold-marker" aria-hidden="true" />
                          <span className="proximity-forecast-marker" style={{ left: `${meterWidth}%` }} aria-hidden="true" />
                        </div>
                        <div className="proximity-scale">
                          <span>0</span>
                          <strong>Threshold</strong>
                          <span>{formatMeasurement(g.threshold * 1.5, g.unit)}</span>
                        </div>
                      </div>
                      {g.source_range && (
                        <div className="proximity-range">
                          <span>Source range</span>
                          <strong>
                            {formatMeasurement(g.source_range.minimum, g.unit)} to {formatMeasurement(g.source_range.maximum, g.unit)}
                          </strong>
                        </div>
                      )}
                      <div className="risk-signals-block">
                        <span className="risk-signals-title">RISK SIGNALS</span>
                        <div className="risk-signals">
                          <div><span>Trust</span><strong>{trustPercent}</strong></div>
                          <div>
                            <span>Bust risk</span>
                            <strong>
                              {g.bust_flag === null || g.bust_flag === undefined ? "—" : g.bust_flag ? "HIGH" : "LOW"}
                              {bustPercent ? ` · ${bustPercent}` : ""}
                            </strong>
                          </div>
                          <div>
                            <span>Model spread</span>
                            <strong>
                              {g.disagreement === null || g.disagreement === undefined
                                ? "—"
                                : formatMeasurement(g.disagreement, g.unit)}
                            </strong>
                          </div>
                        </div>
                      </div>
                      {calibrated && (
                        <div className="calibrated-probability">
                          Calibrated exceedance probability: {formatProbability(g.probability!)}
                        </div>
                      )}
                    </div>
                  ) : (
                    <>
                      {calibrated && <span className="probability-label">Calibrated probability</span>}
                      <h2 className="threat-value">
                        {calibrated
                          ? formatProbability(g.probability!)
                          : formatMeasurement(g.forecast_value, g.unit)}
                      </h2>
                      <strong className="threat-state">{thresholdState}</strong>
                      <div className="threat-metrics">
                        <span>Forecast: {formatMeasurement(g.forecast_value, g.unit)}</span>
                        <span>Threshold: {formatMeasurement(g.threshold, g.unit)}</span>
                        <span>Threshold exceedance: {g.threshold_exceeded ? "YES" : "NO"}</span>
                      </div>
                      <div className="threshold-meter" aria-label={`${ratio === null ? "No" : Math.round(ratio * 100)} percent of threshold`}>
                        <div className="threshold-meter-track">
                          <i style={{ width: `${meterWidth}%` }} />
                          <b aria-hidden="true" />
                        </div>
                        <div className="threshold-meter-labels">
                          <span>0</span>
                          <strong>{ratio === null ? "—" : `${Math.round(ratio * 100)}% OF LIMIT`}</strong>
                          <span>150%</span>
                        </div>
                      </div>
                      {calibrated && (
                        <div className="threat-gauge">
                          <i style={{ transform: `rotate(${g.probability! * 180 - 90}deg)` }} />
                        </div>
                      )}
                      {calibrated && (
                        <div className="calibration yes">
                          <ShieldCheck />
                          <span>CALIBRATED · REAL MODEL · {g.calibration_source?.toUpperCase() ?? "GLOBAL"}</span>
                        </div>
                      )}
                    </>
                  )}
                </section>
              );
            })
          )}
        </div>
        )}
      </div>
    </>
  );
}
