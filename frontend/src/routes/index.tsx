import { createFileRoute, Link, ClientOnly } from "@tanstack/react-router";
import { lazy, Suspense } from "react";
import { useQuery } from "@tanstack/react-query";
import { motion } from "motion/react";
import {
  ArrowRight,
  BarChart3,
  CheckCircle2,
  CloudLightning,
  GitCompareArrows,
  Orbit,
  Radar,
  ScanLine,
  ShieldCheck,
  Waves,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { routeHead } from "@/features/shared/RouteMeta";
import { APP_DATA_MODE, systemStatusQuery, verificationQuery } from "@/features/data/queries";
import "./landing.css";

const Globe = lazy(() => import("@/features/landing/WeatherGlobe"));

export const Route = createFileRoute("/")({
  head: () =>
    routeHead(
      "Weather Intelligence Command Center",
      "Synoptiq learns which weather model to trust for each region, regime, season, and lead time — then explains every decision.",
    ),
  component: Page,
});

const models = ["GFS", "IFS", "AIFS"] as const;

function Page() {
  const verification = useQuery(verificationQuery);
  const systemStatus = useQuery(systemStatusQuery);
  const sourceModels = models.map((name) => {
    const provider = systemStatus.data?.providers[name];
    const label =
      APP_DATA_MODE === "mock"
        ? "DEMO SOURCE"
        : provider
          ? provider.status === "LIVE" && provider.is_real
            ? "LIVE SOURCE"
            : provider.status
          : systemStatus.isError
            ? "API UNAVAILABLE"
            : "CHECKING";
    return { name, label, status: label === "LIVE SOURCE" ? "live" : "unavailable" };
  });
  const realRows = Array.isArray(verification.data) ? verification.data : [];
  const positiveImprovements = realRows
    .filter(
      (row) => row.relative_improvement !== null
        && Number.isFinite(row.relative_improvement)
        && row.relative_improvement > 0,
    )
    .sort((a, b) => b.relative_improvement! - a.relative_improvement!);
  const strongest = positiveImprovements[0];
  const second = positiveImprovements[1];
  const verifiedRegionCount = new Set(realRows.map((row) => row.region)).size;
  const hasLiveVerification =
    verification.isSuccess && APP_DATA_MODE === "live" && realRows.length > 0;

  return (
    <div className="landing">
      <nav className="landing-nav">
        <Link to="/" className="brand">
          <span className="brand-mark">
            <Orbit />
          </span>
          <span>
            <b>Synoptiq</b>
            <small>Adaptive weather intelligence</small>
          </span>
        </Link>
        <div>
          <Link to="/architecture">Architecture</Link>
          <Button asChild variant="instrument">
            <Link to="/replay">
              Open replay <ArrowRight />
            </Link>
          </Button>
        </div>
      </nav>
      <section className="hero">
        <div className="hero-grid" />
        <ClientOnly fallback={<div className="globe-fallback" />}>
          <Suspense fallback={<div className="globe-fallback" />}>
            <Globe />
          </Suspense>
        </ClientOnly>
        <div className="hero-copy">
          <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }}>
            <span className="signal-chip">
              <i />
              {APP_DATA_MODE === "live"
                ? "REAL_12M HISTORICAL"
                : "DEMO MODE · SYNTHETIC DATA"}
            </span>
            <h1>Synoptiq</h1>
            <p className="hero-line">One forecast. Every decision exposed.</p>
            <p className="hero-detail">
              Context-aware weather intelligence that learns which model to trust — and tells you
              when it doesn’t.
            </p>
            <div className="hero-actions">
              <Button asChild size="lg" variant="instrument">
                <Link to="/forecast">
                  Enter command center <ArrowRight />
                </Link>
              </Button>
              <Button asChild size="lg" variant="outline">
                <Link to="/replay">Replay verified cases</Link>
              </Button>
            </div>
          </motion.div>
        </div>
        <div className="hero-readout">
          <span>
            REGIONS <b>03</b>
          </span>
          <span>
            LEAD LADDER <b>24—120H</b>
          </span>
          <span>
            TRUST SIGNALS <b>05</b>
          </span>
        </div>
        <div className="scroll-cue">
          <i />
          SCROLL TO TRACE THE SIGNAL
        </div>
      </section>

      <section className="story-section problem">
        <div className="story-copy">
          <span className="index">01 / THE PROBLEM</span>
          <h2>No single model is always right.</h2>
          <p>
            Forecast skill shifts with terrain, season, lead time, and the weather regime itself.
            Static trust fails exactly when conditions change.
          </p>
        </div>
        <div className="disagreement-viz">
          {sourceModels.map((model, i) => (
            <motion.div
              key={model.name}
              className={model.status}
              initial={{ x: i === 0 ? -70 : i === 2 ? 70 : 0, opacity: 0 }}
              whileInView={{ x: 0, opacity: 1 }}
              viewport={{ once: true }}
              transition={{ duration: 0.7, delay: i * 0.12 }}
            >
              <span>{model.name}</span>
              <i />
              <b>{model.label}</b>
            </motion.div>
          ))}
          <div className="truth-plane">VERIFIED STATE</div>
        </div>
      </section>

      <section className="story-section solution">
        <div className="convergence">
          <div className="radar-ring r1" />
          <div className="radar-ring r2" />
          {sourceModels.map((model, i) => (
            <div
              key={model.name}
              className={`signal-node n${i + 1} ${model.status}`}
              aria-label={`${model.name} ${model.label}`}
            >
              <span>{model.name}</span>
              <i />
              <small>{model.label}</small>
            </div>
          ))}
          <div className="blend-core">
            <Waves />
            <strong>SYNOPTIQ</strong>
            <span>WEIGHTED SIGNAL</span>
          </div>
        </div>
        <div className="story-copy">
          <span className="index">02 / THE SOLUTION</span>
          <h2>Trust becomes adaptive.</h2>
          <p>
            Synoptiq scores model history, disagreement, horizon, regime stability, and data quality
            before producing one bias-corrected forecast.
          </p>
          <div className="mini-trust">
            <span>HISTORICAL SKILL</span>
            <span>SOURCE AGREEMENT</span>
            <span>LEAD TIME</span>
          </div>
        </div>
      </section>

      <section className="capabilities">
        <header>
          <span className="index">03 / SYSTEM CAPABILITIES</span>
          <h2>Built for decisions under uncertainty.</h2>
        </header>
        <div className="cap-grid">
          {[
            {
              icon: Radar,
              title: "Trust composition",
              copy: "Five observable signals feed one operational confidence score.",
            },
            {
              icon: ScanLine,
              title: "Explain every weight",
              copy: "Structured contribution drivers show why trust moved.",
            },
            {
              icon: GitCompareArrows,
              title: "Replay every outcome",
              copy: "Compare Synoptiq against naive and single-model baselines.",
            },
            {
              icon: BarChart3,
              title: "Map the lead ladder",
              copy: "See model dominance evolve across the supported lead-time ladder.",
            },
          ].map((x, i) => (
            <motion.article
              key={x.title}
              initial={{ opacity: 0, y: 30 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true }}
              transition={{ delay: i * 0.08 }}
            >
              <x.icon />
              <span>0{i + 1}</span>
              <h3>{x.title}</h3>
              <p>{x.copy}</p>
            </motion.article>
          ))}
        </div>
      </section>

      <section className="proof-section">
        <div>
          <span className="index">04 / HELD-OUT VERIFICATION</span>
          <h2>Rigor makes the wins matter.</h2>
          <p>
            The scorecard is data-driven. Live mode shows only metrics produced by the real held-out
            evaluation pipeline; no synthetic KPI is presented as operational proof.
          </p>
          <Button asChild variant="outline">
            <Link to="/verification">
              View full scorecard <ArrowRight />
            </Link>
          </Button>
        </div>
        <div className="proof-readouts">
          {hasLiveVerification ? (
            <>
              <div className="proof-win">
                <b>
                  {strongest ? `${(strongest.relative_improvement! * 100).toFixed(1)}%` : verifiedRegionCount}
                </b>
                <span>
                  {strongest
                    ? `${strongest.region} · ${strongest.variable.replace("_", " ")} ${strongest.metric}`
                    : "regions evaluated"}
                </span>
                <CheckCircle2 />
              </div>
              <div className="proof-win">
                <b>
                  {second ? `${(second.relative_improvement! * 100).toFixed(1)}%` : realRows.length}
                </b>
                <span>
                  {second
                    ? `${second.region} · ${second.variable.replace("_", " ")} ${second.metric}`
                    : "real scorecard rows"}
                </span>
                <CheckCircle2 />
              </div>
              <div className="proof-honest">
                <ShieldCheck />
                <div>
                  <b>LIVE TEST RESULTS</b>
                  <span>
                    All values above are read from the current held-out verification artifacts.
                  </span>
                </div>
              </div>
            </>
          ) : APP_DATA_MODE === "mock" && verification.isSuccess ? (
            <div className="proof-honest">
              <ShieldCheck />
              <div>
                <b>DEMO VERIFICATION DATA</b>
                <span>These synthetic fixtures are shown only because DEMO mode was selected.</span>
              </div>
            </div>
          ) : verification.isError ? (
            <div className="proof-honest">
              <ShieldCheck />
              <div>
                <b>REAL VERIFICATION UNAVAILABLE</b>
                <span>{verification.error.message}</span>
              </div>
            </div>
          ) : (
            <>
              <div className="proof-honest">
                <ShieldCheck />
                <div>
                  <b>REAL TEST METRICS UNAVAILABLE</b>
                  <span>
                    No real held-out verification rows were returned for the active model.
                  </span>
                </div>
              </div>
            </>
          )}
        </div>
      </section>

      <section className="systems-ready">
        <div className="ready-rings">
          <i />
          <i />
          <i />
        </div>
        <CloudLightning />
        <span>CORE SYSTEM STATUS</span>
        <h2>Enter the forecast.</h2>
        <p>
          Live when the backend is connected. Replay-safe when historical evidence is requested.
        </p>
        <Button asChild size="lg" variant="instrument">
          <Link to="/forecast">
            Launch Synoptiq <ArrowRight />
          </Link>
        </Button>
      </section>
      <footer className="landing-footer">
        <span>SYNOPTIQ · ADAPTIVE WEATHER INTELLIGENCE</span>
        <span>MULTI-MODEL FORECAST INTELLIGENCE</span>
      </footer>
    </div>
  );
}
