import { createFileRoute } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import type { FormEvent } from "react";
import { AlertTriangle, Download, FileWarning, ShieldAlert } from "lucide-react";
import { APP_DATA_MODE } from "@/features/data/queries";
import { SectionHeading } from "@/features/shared/SectionHeading";
import { TopBar } from "@/features/shared/AppShell";
import { DataStatus } from "@/features/shared/DataStatus";
import { routeHead } from "@/features/shared/RouteMeta";
import { archiveDatesQuery, impactQuery } from "@/features/ops/queries";
import type { ArchiveDates } from "@/features/ops/queries";

export const Route = createFileRoute("/impact")({
  head: () =>
    routeHead(
      "Impact & Alerts",
      "Impact Severity Index, IMD colour codes, NDRF-style advisories and downloadable CAP 1.2 alerts for the pilot zones.",
    ),
  component: Page,
});

const regionLabel = (r: string) => r.replace(/_/g, " ");
const COLOUR_CLASS: Record<string, string> = {
  RED: "impact-red", ORANGE: "impact-orange", YELLOW: "impact-yellow", GREEN: "impact-green",
};

function formatArchiveDate(date: string | null) {
  return date ? `${date.slice(8, 10)}-${date.slice(5, 7)}-${date.slice(0, 4)}` : "";
}

function formatDateStrings(value: string) {
  return value.replace(/\b(\d{4})-(\d{2})-(\d{2})\b/g, (_match, year, month, day) => `${day}-${month}-${year}`);
}

function ArchiveDateControls({ date, draftDate, dateError, archive, onDraftDateChange, onSubmit, onLatest, onEventDate }: {
  date: string | null;
  draftDate: string;
  dateError: string | null;
  archive?: ArchiveDates;
  onDraftDateChange: (date: string) => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  onLatest: () => void;
  onEventDate: () => void;
}) {
  return (
    <form className="rpi-controls" aria-label="Archived impact date" onSubmit={onSubmit}>
      <button type="button" className={`research-btn ${date === null ? "active" : ""}`} onClick={onLatest}>
        Latest
      </button>
      {archive?.dates.includes("2025-05-30") && (
        <button type="button" className={`research-btn ${date === "2025-05-30" ? "active" : ""}`} onClick={onEventDate}>
          30 May 2025 (event)
        </button>
      )}
      <input
        type="date"
        className="rpi-date"
        value={draftDate}
        min={archive?.earliest ?? undefined}
        max={archive?.latest ?? undefined}
        onChange={(event) => onDraftDateChange(event.target.value)}
        required
        aria-label="Pick an archived day"
        aria-describedby={dateError ? "impact-date-error" : "impact-date-range"}
      />
      <button type="submit" className="research-btn" disabled={!draftDate.trim() || !archive?.dates.length}>
        Submit
      </button>
      {archive?.earliest && archive.latest && (
        <span id="impact-date-range" className="research-note">Available: {formatArchiveDate(archive.earliest)} to {formatArchiveDate(archive.latest)}</span>
      )}
      {dateError && <span id="impact-date-error" className="research-note error" role="alert">{dateError}</span>}
    </form>
  );
}

function Page() {
  const [date, setDate] = useState<string | null>(null);
  const [draftDate, setDraftDate] = useState("");
  const [dateError, setDateError] = useState<string | null>(null);
  const q = useQuery(impactQuery(date));
  const archive = useQuery(archiveDatesQuery);
  const mode = APP_DATA_MODE;
  const submitArchiveDate = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!draftDate) {
      setDateError("Select an archived date.");
      return;
    }
    if (
      !archive.data?.dates.includes(draftDate) ||
      (archive.data.earliest && draftDate < archive.data.earliest) ||
      (archive.data.latest && draftDate > archive.data.latest)
    ) {
      const range = archive.data?.earliest && archive.data.latest
        ? ` The archive spans ${formatArchiveDate(archive.data.earliest)} to ${formatArchiveDate(archive.data.latest)}.`
        : "";
      setDateError(`No archived data exists for ${formatArchiveDate(draftDate)}.${range}`);
      return;
    }
    setDateError(null);
    setDate(draftDate);
  };
  const selectLatest = () => {
    setDateError(null);
    setDraftDate("");
    setDate(null);
  };
  const selectEventDate = () => {
    setDateError(null);
    setDraftDate("2025-05-30");
    setDate("2025-05-30");
  };
  const dateControls = (
    <ArchiveDateControls
      date={date}
      draftDate={draftDate}
      dateError={dateError}
      archive={archive.data}
      onDraftDateChange={(value) => {
        setDraftDate(value);
        setDateError(null);
      }}
      onSubmit={submitArchiveDate}
      onLatest={selectLatest}
      onEventDate={selectEventDate}
    />
  );

  if (q.isPending)
    return (
      <>
        <TopBar mode={mode} />
        <div className="page-state" role="status">
          <h2>ASSESSING IMPACT</h2>
          <p>Converting the Synoptiq hazard signal into district-action language.</p>
          {dateControls}
        </div>
      </>
    );
  if (q.isError)
    return (
      <>
        <TopBar mode={mode} />
        <div className="page-state" role="alert">
          <h2>IMPACT ENGINE UNAVAILABLE</h2>
          <p>{formatDateStrings(q.error instanceof Error ? q.error.message : "The impact artifact could not be reached.")}</p>
          <p className="research-note">Choose a date inside the archived range, or use Latest. {archive.data?.earliest && archive.data?.latest ? `Available archive: ${archive.data.earliest} to ${archive.data.latest}.` : ""}</p>
          {dateControls}
          <button className="research-btn" onClick={() => void q.refetch()}>Retry</button>
        </div>
      </>
    );

  const data = q.data!;
  const worst = data.zones[0];
  const apiBase = "/api/v1";

  return (
    <>
      <TopBar mode={mode} />
      <div className="page">
        <SectionHeading
          eyebrow="DISASTER-MANAGEMENT LAYER · IMD COLOUR CODES · CAP 1.2"
          title="Impact & alerts"
          copy={`Impact Severity Index per pilot zone for ${data.date}, combining the Synoptiq hazard signal with a documented static vulnerability factor. Colour bands follow IMD practice; alerts export as OASIS CAP 1.2 XML.`}
          action={<DataStatus mode={mode} />}
        />

        <div className="proof-strip">
          <div>
            <ShieldAlert />
            <span>HIGHEST SEVERITY</span>
            <strong className={worst ? COLOUR_CLASS[worst.colour] : ""}>
              {worst ? `${worst.colour} · ${worst.isi}/10` : "—"}
            </strong>
            <small>{worst ? regionLabel(worst.zone) : "no zones"}</small>
          </div>
          <div>
            <AlertTriangle />
            <span>ZONES AT YELLOW OR ABOVE</span>
            <strong>{data.zones.filter((z) => z.colour !== "GREEN").length}</strong>
            <small>of {data.zones.length} pilot zones</small>
          </div>
          <div>
            <FileWarning />
            <span>CAP ALERTS READY</span>
            <strong>{data.zones.length}</strong>
            <small>one CAP 1.2 XML per zone, downloadable below</small>
          </div>
        </div>

        <section className="panel research-panel">
          <div className="panel-head">
            <div>
              <span className="kicker">ARCHIVED DAY</span>
              <h2>Replay any day in the 12-month record</h2>
            </div>
            {dateControls}
          </div>
          <div className="impact-grid">
            {data.zones.map((z) => (
              <article key={z.zone} className={`impact-card ${COLOUR_CLASS[z.colour] ?? ""}`}>
                <header>
                  <span className="impact-colour">{z.colour}</span>
                  <h3>{regionLabel(z.zone)}</h3>
                  <em>{z.severity} · ISI {z.isi}/10</em>
                </header>
                <div className="impact-components">
                  {Object.entries(z.components).map(([variable, c]) => (
                    <div key={variable}>
                      <span>{variable.replace(/_/g, " ")}</span>
                      <strong>{c.value}</strong>
                      <small>vs {c.threshold} · ratio {c.ratio}</small>
                    </div>
                  ))}
                </div>
                <p className="impact-advisory">{z.advisory}</p>
                <footer>
                  <small>
                    vulnerability factor {z.vulnerability_factor}
                    {z.vulnerability_rationale ? ` — ${z.vulnerability_rationale}` : ""}
                  </small>
                  <a className="research-btn" href={`${apiBase}/impact/cap/${z.zone}.xml${date ? `?date=${date}` : ""}`}
                    download>
                    <Download /> CAP XML
                  </a>
                </footer>
              </article>
            ))}
          </div>
          <p className="research-note">
            ISI = min(10, Σ hazard-ratio × zone vulnerability). Hazard weights
            {" "}{Object.entries(data.hazard_weights ?? {}).map(([k, v]) => `${k.replace(/_/g, " ")} ${v}`).join(" · ")}.
            Vulnerability factors are documented pilot-zone assumptions, not
            fitted parameters — district shapefiles extend the same chain
            unchanged.
          </p>
        </section>
      </div>
    </>
  );
}
