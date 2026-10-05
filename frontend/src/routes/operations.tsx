import { createFileRoute } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { CalendarClock, CheckCircle2, Download, FileText, Terminal } from "lucide-react";
import { APP_DATA_MODE } from "@/features/data/queries";
import { SectionHeading } from "@/features/shared/SectionHeading";
import { TopBar } from "@/features/shared/AppShell";
import { DataStatus } from "@/features/shared/DataStatus";
import { routeHead } from "@/features/shared/RouteMeta";
import { opsStatusQuery } from "@/features/ops/queries";

export const Route = createFileRoute("/operations")({
  head: () =>
    routeHead(
      "Operations",
      "The routine forecast workflow: one command that ingests the latest cycle, rebuilds the artifacts, exports the bulletin and logs the run.",
    ),
  component: Page,
});

const STEPS = [
  { n: "1", title: "Ingest the latest real cycle", detail: "training/realdata/ingest_latest_cycle.py pulls the newest complete 00Z/06Z/12Z/18Z cycle of GFS + IFS + AIFS. If credentials or network are unavailable it says so and continues on the archived record — never synthetic." },
  { n: "2", title: "Rebuild verification + operational artifacts", detail: "research_suite.py, build_advanced_artifacts.py and generate_model_card.py refresh every number the dashboard shows from the same record." },
  { n: "3", title: "Export the standard bulletin", detail: "export_bulletin.py writes JSON + CSV + plain-text bulletins and one CAP 1.2 XML alert per zone into artifacts/…/bulletins/<date>/." },
  { n: "4", title: "Log the run", detail: "One line is appended to ops/run_log.jsonl; this page reads it back so the workflow is auditable, not assumed." },
];

function Page() {
  const q = useQuery(opsStatusQuery);
  const mode = APP_DATA_MODE;

  if (q.isPending)
    return (
      <>
        <TopBar mode={mode} />
        <div className="page-state" role="status">
          <h2>LOADING OPERATIONS</h2>
          <p>Reading the routine-run log and bulletin inventory.</p>
        </div>
      </>
    );
  if (q.isError)
    return (
      <>
        <TopBar mode={mode} />
        <div className="page-state" role="alert">
          <h2>OPERATIONS STATUS UNAVAILABLE</h2>
          <p>{q.error instanceof Error ? q.error.message : "The ops endpoint could not be reached."}</p>
          <button className="research-btn" onClick={() => void q.refetch()}>Retry</button>
        </div>
      </>
    );

  const s = q.data!;
  const last = s.last_run;
  const apiBase = "/api/v1";

  return (
    <>
      <TopBar mode={mode} />
      <div className="page">
        <SectionHeading
          eyebrow="ROUTINE OPERATIONAL WORKFLOW"
          title="Operations"
          copy="The problem statement asks for an automated script/dashboard for routine forecasting. This is that loop: one command, four steps, every product written to disk and logged."
          action={<DataStatus mode={mode} />}
        />

        <div className="proof-strip">
          <div>
            <CalendarClock />
            <span>LAST ROUTINE RUN</span>
            <strong>{last ? last.valid_date : "NEVER RUN"}</strong>
            <small>{last ? `logged ${last.ran_at.slice(0, 16).replace("T", " ")} UTC` : "run scripts/run_routine_blend.sh to populate"}</small>
          </div>
          <div>
            <FileText />
            <span>BULLETIN DAYS ON DISK</span>
            <strong>{s.bulletin_days.length}</strong>
            <small>{s.bulletin_days.slice(-3).join(", ") || "none yet"}</small>
          </div>
          <div>
            <CheckCircle2 />
            <span>ARTIFACTS HEALTHY</span>
            <strong>{Object.values(s.artifacts_present).filter(Boolean).length}/5</strong>
            <small>{Object.entries(s.artifacts_present).map(([k, v]) => `${k}${v ? " ✓" : " ✗"}`).join(" · ")}</small>
          </div>
        </div>

        <section className="panel research-panel">
          <div className="panel-head">
            <div>
              <span className="kicker">THE LOOP</span>
              <h2>Four steps, one command</h2>
            </div>
            <div className="rpi-controls">
              <code className="research-btn">{s.commands.routine}</code>
            </div>
          </div>
          <ol className="ops-steps">
            {STEPS.map((step) => (
              <li key={step.n}>
                <span className="ops-step-n">{step.n}</span>
                <div>
                  <strong>{step.title}</strong>
                  <p>{step.detail}</p>
                </div>
              </li>
            ))}
          </ol>
        </section>

        <section className="panel research-panel">
          <div className="panel-head">
            <div>
              <span className="kicker">SCHEDULING</span>
              <h2>Run it on a schedule</h2>
            </div>
          </div>
          <div className="ops-cron">
            <div>
              <h3><Terminal /> Linux / macOS (cron)</h3>
              <pre>{`# daily 06:20 IST — after the 00Z cycle is fully posted
20 6 * * * cd /path/to/Synoptiq && scripts/run_routine_blend.sh >> ops/cron.log 2>&1`}</pre>
            </div>
            <div>
              <h3><Terminal /> Windows (Task Scheduler)</h3>
              <pre>{`schtasks /Create /TN "Synoptiq routine forecast" /SC DAILY /ST 06:20 ^
  /TR "powershell -ExecutionPolicy Bypass -File C:\\path\\Synoptiq\\scripts\\run_routine_blend.ps1"`}</pre>
            </div>
          </div>
        </section>

        <section className="panel research-panel">
          <div className="panel-head">
            <div>
              <span className="kicker">PRODUCTS</span>
              <h2>Download today's bulletin</h2>
            </div>
            <div className="rpi-controls">
              {s.bulletin_days.slice(-3).reverse().map((day) => (
                <span key={day} className="research-note">{day}</span>
              ))}
            </div>
          </div>
          <div className="rpi-controls">
            {(["json", "csv", "txt"] as const).map((fmt) => (
              <a key={fmt} className="research-btn" href={`${apiBase}/ops/bulletin?format=${fmt}`} download>
                <Download /> bulletin.{fmt}
              </a>
            ))}
          </div>
          {last && (
            <p className="research-note">
              Last run produced {last.artifacts.length} files for {last.valid_date}
              (highest colour {last.max_colour}); {s.runs_recorded} runs recorded
              in ops/run_log.jsonl.
            </p>
          )}
        </section>
      </div>
    </>
  );
}
