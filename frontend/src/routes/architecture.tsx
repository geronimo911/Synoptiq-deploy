import { createFileRoute } from "@tanstack/react-router";
import { motion } from "motion/react";
import {
  ArrowDown,
  BrainCircuit,
  Database,
  GitMerge,
  ScanSearch,
  ShieldCheck,
  Sparkles,
  Target,
} from "lucide-react";
import { SectionHeading } from "@/features/shared/SectionHeading";
import { TopBar } from "@/features/shared/AppShell";
import { APP_DATA_MODE } from "@/features/data/queries";
import { DataStatus } from "@/features/shared/DataStatus";
import { routeHead } from "@/features/shared/RouteMeta";
export const Route = createFileRoute("/architecture")({
  head: () =>
    routeHead(
      "System Architecture",
      "Understand the auditable Synoptiq pipeline from forecast sources to bias-corrected output, trust, and bust risk.",
    ),
  component: Page,
});
const nodes = [
  { n: "01", title: "Forecast sources", copy: "GFS · IFS · AIFS", icon: Database },
  { n: "02", title: "Normalization", copy: "Units · time · finite-value validation", icon: ScanSearch },
  { n: "03", title: "Context features", copy: "Regime · season · location · historical skill", icon: Sparkles },
  {
    n: "04",
    title: "Source-skill models",
    copy: "9 trained LightGBM models",
    icon: BrainCircuit,
  },
  {
    n: "05",
    title: "Dynamic weights",
    copy: "Context-aware softmax allocation",
    icon: GitMerge,
  },
  {
    n: "06",
    title: "Bust risk",
    copy: "3 variable-specific classifiers",
    icon: ShieldCheck,
  },
  {
    n: "07",
    title: "Trust",
    copy: "Skill · agreement · lead · regime · data quality",
    icon: Target,
  },
  {
    n: "08",
    title: "Weighted Synoptiq decision",
    copy: "Real source forecasts and dynamic weights",
    icon: GitMerge,
  },
  {
    n: "09",
    title: "Calibration",
    copy: "Quantile correction · event calibration when available",
    icon: Sparkles,
  },
  {
    n: "10",
    title: "Explanation",
    copy: "SHAP contributions from trained skill models",
    icon: ShieldCheck,
  },
];
function Page() {
  return (
    <>
      <TopBar mode={APP_DATA_MODE} />
      <div className="page">
        <SectionHeading
          eyebrow="Auditable from source to decision"
          title="How Synoptiq thinks"
          copy="A transparent pipeline turns competing numerical weather predictions into one contextual forecast — without hiding uncertainty."
          action={<DataStatus mode={APP_DATA_MODE} />}
        />
        <div className="pipeline">
          {nodes.map((x, i) => (
            <motion.div
              key={x.n}
              className="pipeline-step"
              initial={{ opacity: 0, y: 32 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, amount: 0.6 }}
              transition={{ duration: 0.45 }}
            >
              <span>{x.n}</span>
              <x.icon />
              <div>
                <h2>{x.title}</h2>
                <p>{x.copy}</p>
              </div>
              {i < nodes.length - 1 && <ArrowDown className="flow-arrow" />}
            </motion.div>
          ))}
        </div>
        <section className="integrity-band">
          <div>
            <span className="kicker">SCIENTIFIC BOUNDARY</span>
            <h2>What is real. What is simulated.</h2>
          </div>
          <p>
            REAL mode uses the isolated real-data prototype, real model artifacts, and real
            inference pipeline. Its historical window is limited, and calibration is not claimed as
            validated at production scale. Synthetic fixtures are available only in explicit DEMO
            mode.
          </p>
        </section>
        <div className="architecture-facts">
          <div>
            <b>3</b>
            <span>forecast sources</span>
          </div>
          <div>
            <b>5</b>
            <span>lead-time horizons</span>
          </div>
          <div>
            <b>5</b>
            <span>trust signals</span>
          </div>
          <div>
            <b>1</b>
            <span>honest forecast</span>
          </div>
        </div>
      </div>
    </>
  );
}
