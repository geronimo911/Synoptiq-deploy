import { createFileRoute } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Globe2, Info, MapPin, Target } from "lucide-react";
import { APP_DATA_MODE } from "@/features/data/queries";
import { SectionHeading } from "@/features/shared/SectionHeading";
import { TopBar } from "@/features/shared/AppShell";
import { DataStatus } from "@/features/shared/DataStatus";
import { routeHead } from "@/features/shared/RouteMeta";
import { trustAtlasQuery } from "@/features/ops/queries";
import type { TrustAtlasCell } from "@/features/ops/queries";
import { TrustAtlasMap } from "@/features/ops/TrustAtlasMap";

export const Route = createFileRoute("/trust-atlas")({
  head: () =>
    routeHead(
      "Model Trust Atlas",
      "Which model is trusted where, and why — the model weight map required by the problem statement, projected on the pilot zones.",
    ),
  component: Page,
});

const regionLabel = (r: string) => r.replace(/_/g, " ");
const varLabel = (v: string) => v.replace(/_/g, " ");
const LEADS = [24, 48, 72, 96, 120];

function ModelChip({ model, colour, weight }: { model: string; colour: string; weight: number }) {
  return (
    <span className="model-chip" style={{ borderColor: colour, color: colour }}>
      {model} {(weight * 100).toFixed(0)}%
    </span>
  );
}

function Page() {
  const q = useQuery(trustAtlasQuery);
  const [region, setRegion] = useState<string | null>(null);
  const [variable, setVariable] = useState<string>("precipitation");
  const mode = APP_DATA_MODE;

  if (q.isPending)
    return (
      <>
        <TopBar mode={mode} />
        <div className="page-state" role="status">
          <h2>LOADING TRUST ATLAS</h2>
          <p>Reading the issued weights and measured skill from the archived record.</p>
        </div>
      </>
    );
  if (q.isError)
    return (
      <>
        <TopBar mode={mode} />
        <div className="page-state" role="alert">
          <h2>TRUST ATLAS UNAVAILABLE</h2>
          <p>{q.error instanceof Error ? q.error.message : "The atlas artifact could not be reached."}</p>
          <p className="research-note">
            Generate it with <code>python backend/scripts/build_advanced_artifacts.py</code>.
          </p>
          <button className="research-btn" onClick={() => void q.refetch()}>Retry</button>
        </div>
      </>
    );

  const atlas = q.data!;
  const colours = atlas.model_colours;
  const cells = atlas.cells.filter((c) => c.variable === variable);
  const regions = Object.keys(atlas.region_lead_matrix);
  const activeRegion = region ?? regions[0] ?? "";
  const activeCell = cells.find((c) => c.region === activeRegion);

  return (
    <>
      <TopBar mode={mode} />
      <div className="page">
        <SectionHeading
          eyebrow="PROBLEM-STATEMENT DELIVERABLE · MODEL WEIGHT MAP"
          title="Model Trust Atlas"
          copy={atlas.scope}
          action={<DataStatus mode={mode} />}
        />

        <div className="proof-strip">
          <div>
            <Globe2 />
            <span>PILOT ZONES MAPPED</span>
            <strong>{regions.length}</strong>
            <small>Kerala Western Ghats · Bay of Bengal coast · Indo-Gangetic Plains</small>
          </div>
          <div>
            <Target />
            <span>DOMINATING MODEL NOW</span>
            <strong>{activeCell?.dominating_model ?? "—"}</strong>
            <small>{varLabel(variable)} · {regionLabel(activeRegion)}</small>
          </div>
          <div>
            <Info />
            <span>CELLS SCORED</span>
            <strong>{atlas.cells.length}</strong>
            <small>region × variable × lead, weights + measured test skill</small>
          </div>
        </div>

        <section className="panel research-panel">
          <div className="panel-head">
            <div>
              <span className="kicker">SPATIAL WEIGHT MAP</span>
              <h2>Who is trusted where</h2>
            </div>
            <div className="rpi-controls">
              {["precipitation", "temperature", "wind_speed"].map((v) => (
                <button key={v} className={`research-btn ${v === variable ? "active" : ""}`}
                  onClick={() => setVariable(v)}>
                  {varLabel(v)}
                </button>
              ))}
            </div>
          </div>
          <div className="atlas-grid">
            <TrustAtlasMap
              zones={Object.entries(atlas.zone_centroids).map(([zone, centroid]) => ({
                zone,
                lat: centroid.lat,
                lon: centroid.lon,
                model: cells.find((cell) => cell.region === zone)?.dominating_model ?? "—",
                colour: colours[cells.find((cell) => cell.region === zone)?.dominating_model ?? "GFS"] ?? "#94a3b8",
              }))}
              selected={activeRegion}
              onSelect={setRegion}
            />
            <div className="atlas-detail">
              {activeCell ? (
                <>
                  <h3>
                    <MapPin /> {regionLabel(activeCell.region)} · {varLabel(activeCell.variable)}
                  </h3>
                  <p className="research-note">{activeCell.reason}</p>
                  <p className="atlas-comparison">Issued weights show what Synoptiq trusts; test MAE shows whether each raw model earned that trust on held-out cases. The comparison is shown for GFS, IFS, and AIFS below.</p>
                  <div className="weight-bars">
                    {Object.entries(activeCell.mean_weights)
                      .sort((a, b) => b[1] - a[1])
                      .map(([model, w]) => (
                        <div key={model} className="weight-bar-row">
                          <span>{model}</span>
                          <div className="weight-bar-track">
                            <div className="weight-bar-fill"
                              style={{ width: `${w * 100}%`, background: colours[model] ?? "#94a3b8" }} />
                          </div>
                          <em>{(w * 100).toFixed(1)}%</em>
                          <small>
                            test MAE {activeCell.test_mae[model] ?? "—"}
                            {activeCell.best_test_model === model ? " · best" : ""}
                          </small>
                        </div>
                      ))}
                  </div>
                </>
              ) : (
                <p className="research-note">No cell data for this selection.</p>
              )}
            </div>
          </div>
        </section>

        <section className="panel research-panel">
          <div className="panel-head">
            <div>
              <span className="kicker">REGION × LEAD MATRIX</span>
              <h2>Dominating model by lead time ({varLabel(variable)})</h2>
            </div>
          </div>
          <div className="research-table-wrap">
            <table className="research-table">
              <thead>
                <tr>
                  <th>Zone</th>
                  {LEADS.map((l) => <th key={l}>+{l}h</th>)}
                </tr>
              </thead>
              <tbody>
                {regions.map((r) => (
                  <tr key={r}>
                    <td>{regionLabel(r)}</td>
                    {LEADS.map((l) => {
                      const cell = cells.find((c) => c.region === r && c.lead_hours === l);
                      if (!cell) return <td key={l}>—</td>;
                      const colour = colours[cell.dominating_model] ?? "#94a3b8";
                      return (
                        <td key={l}>
                          <span className="model-chip" style={{ borderColor: colour, color: colour }}>
                            {cell.dominating_model} {(cell.mean_weights[cell.dominating_model] * 100).toFixed(0)}%
                          </span>
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="research-note">
            Colour encodes the model with the largest issued weight for that
            zone/variable/lead cell: {Object.entries(colours).map(([m, c]) => (
              <span key={m} className="model-chip" style={{ borderColor: c, color: c }}>{m}</span>
            ))}
            . Hover a zone on the map (or click) for the full weight breakdown,
            the measured test MAE per model and the regime conditioning.
          </p>
        </section>
      </div>
    </>
  );
}
