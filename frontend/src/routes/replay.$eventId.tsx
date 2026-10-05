import { createFileRoute, Link } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { motion } from "motion/react";
import { ArrowLeft, ArrowLeftRight, CheckCircle2, FlaskConical, Target } from "lucide-react";
import { Button } from "@/components/ui/button";
import { APP_DATA_MODE, replayEventQuery } from "@/features/data/queries";
import { TopBar } from "@/features/shared/AppShell";
import { DataStatus } from "@/features/shared/DataStatus";
import { routeHead } from "@/features/shared/RouteMeta";
export const Route = createFileRoute("/replay/$eventId")({
  head: () =>
    routeHead(
      "Replay Case",
      "Inspect one verified Synoptiq forecast against the naive average, default model, and observed truth.",
    ),
  component: Page,
});
function Page() {
  const { eventId } = Route.useParams();
  const replay = useQuery(replayEventQuery(eventId));
  if (replay.isPending) {
    return (
      <>
        <TopBar mode={APP_DATA_MODE} />
        <div className="page-state">Loading replay case…</div>
      </>
    );
  }
  if (replay.isError || !replay.data) {
    return (
      <>
        <TopBar mode={APP_DATA_MODE} />
        <div className="page-state" role="alert">
          <h2>Replay case unavailable</h2>
          <p>
            {replay.error instanceof Error
              ? replay.error.message
              : "No replay case data was returned."}
          </p>
          <Button onClick={() => void replay.refetch()}>Retry</Button>
          <Link to="/replay">Back to replay cases</Link>
        </div>
      </>
    );
  }
  const d = replay.data;
  const precision = d.variable === "precipitation" ? 2 : 1;
  const formatValue = (value: number) => value.toFixed(precision);
  const singleModel =
    d.single_model_choice && typeof d.single_model_choice === "object"
      ? d.single_model_choice
      : { model: "Unavailable", forecast_value: Number.NaN };
  const safeSources = Array.isArray(d.raw_sources) ? d.raw_sources : [];
  const baselineErrors = [d.naive_average_error, d.single_model_error].filter(Number.isFinite);
  const outcome = d.outcome ?? (
    !Number.isFinite(d.synoptiq_error) || baselineErrors.length < 2
      ? "MIXED"
      : d.synoptiq_error < Math.min(...baselineErrors)
        ? "WIN"
        : d.synoptiq_error > Math.max(...baselineErrors)
          ? "MISS"
          : "MIXED"
  );
  const miss = outcome === "MISS";
  const outcomeIcon = outcome === "WIN"
    ? <CheckCircle2 />
    : outcome === "MISS"
      ? <FlaskConical />
      : <ArrowLeftRight />;
  const vals = [
    { name: "Naive average", value: Number.isFinite(d.naive_average) ? d.naive_average : 0, error: Number.isFinite(d.naive_average_error) ? d.naive_average_error : 0 },
    {
      name: `Single · ${singleModel.model ?? "Unavailable"}`,
      value: Number.isFinite(singleModel.forecast_value) ? singleModel.forecast_value : Number.isFinite(d.naive_average) ? d.naive_average : 0,
      error: Number.isFinite(d.single_model_error) ? d.single_model_error : Number.isFinite(d.naive_average_error) ? d.naive_average_error : 0,
    },
    { name: "Synoptiq", value: Number.isFinite(d.synoptiq_blend) ? d.synoptiq_blend : 0, error: Number.isFinite(d.synoptiq_error) ? d.synoptiq_error : 0 },
  ];
  const numericValues = vals.map((v) => Number(v.value) || 0);
  const referenceValue = Number.isFinite(d.reference_value) ? d.reference_value : 0;
  const min = Math.min(...numericValues, referenceValue) * 0.9,
    max = Math.max(...numericValues, referenceValue) * 1.07;
  const chartRange = max - min || 1;
  const narrative = Array.isArray(d.narrative) ? d.narrative : [];
  return (
    <>
      <TopBar mode={APP_DATA_MODE} />
      <div className="page replay-detail">
        <div className="back-row">
          <Link to="/replay">
            <ArrowLeft />
            All cases
          </Link>
          <DataStatus mode={APP_DATA_MODE} />
        </div>
        <header className={`replay-hero ${outcome.toLowerCase()}`}>
          <span className="kicker">
            {d.event_id} · +{d.lead_hours}H
          </span>
          <h1>{d.label}</h1>
          <p>{d.headline}</p>
          <div className="verdict">
            {outcomeIcon}
            {outcome === "WIN" ? "Synoptiq beats both baselines" : outcome === "MISS" ? "Scientifically honest miss" : "Mixed baseline result"}
          </div>
        </header>
        <section className="race-panel panel">
          <div className="truth-head">
            <Target />
            <span>VERIFIED TRUTH</span>
            <strong>
              {formatValue(referenceValue)} {d.unit}
            </strong>
          </div>
          <div className="race-track">
            <div
              className="truth-line"
              style={{ left: `${((referenceValue - min) / chartRange) * 100}%` }}
            />
            {vals.map((v, i) => (
              <div className="race-row" key={v.name}>
                <span>{v.name}</span>
                <div className="race-line">
                  <motion.i
                    initial={{ width: 0 }}
                    animate={{ width: `${((v.value - min) / chartRange) * 100}%` }}
                    transition={{ duration: 1, delay: i * 0.35, ease: "easeOut" }}
                  />
                  <b style={{ left: `${((v.value - min) / chartRange) * 100}%` }}>
                    {formatValue(v.value)}
                  </b>
                </div>
                <strong className={v.name === "Synoptiq" ? "accent" : ""}>
                  {formatValue(Number(v.error || 0))} error
                </strong>
              </div>
            ))}
          </div>
          <div className="raw-sources">
            {safeSources.map((s) => (
              <span key={`${d.event_id}-${s.model ?? "source"}`}>
                <b>{s.model ?? "SOURCE"}</b>
                {Number.isFinite(s.forecast_value) ? formatValue(s.forecast_value) : "—"} {d.unit}
              </span>
            ))}
          </div>
        </section>
        <section className="commentary">
          <span className="kicker">MISSION COMMENTARY</span>
          {narrative.map((n, i) => (
            <motion.div
              key={n}
              initial={{ opacity: 0, x: -20 }}
              whileInView={{ opacity: 1, x: 0 }}
              viewport={{ once: true, amount: 0.6 }}
              transition={{ delay: i * 0.08 }}
            >
              <b>{String(i + 1).padStart(2, "0")}</b>
              <p>{n}</p>
            </motion.div>
          ))}
        </section>
      </div>
    </>
  );
}
