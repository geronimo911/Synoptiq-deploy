import { AlertTriangle, CloudCog } from "lucide-react";
export function DataStatus({
  mode,
  compact = false,
}: {
  mode: "live" | "mock";
  compact?: boolean;
}) {
  return (
    <div
      className={`status-pill ${mode === "live" ? "status-live" : "status-mock"}`}
      title={
        mode === "live"
          ? "Validated REAL_12M historical data; live freshness is shown by each page"
          : "Using synthetic fixtures in explicit DEMO mode"
      }
    >
      {mode === "live" ? <CloudCog /> : <AlertTriangle />}
      <span>
        {compact
          ? mode === "live"
            ? "Real"
            : "Demo"
          : mode === "live"
            ? "REAL_12M historical"
            : "Demo mode · synthetic data"}
      </span>
    </div>
  );
}
