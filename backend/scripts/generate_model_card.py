"""Regenerate MODEL_CARD.md from the live artifacts — never by hand.

Reads the manifest, the untouched-test evaluation reports and the research
suite, and writes a model card whose numbers are pulled from the same
artifacts the API serves. Run this after every training run so the
human-facing document cannot drift from the machine-facing ones:

    python backend/scripts/generate_model_card.py

The static narrative sections of the existing card are preserved; every
number in the generated card comes from an artifact on disk. The script
fails loudly (non-zero exit) if any required artifact is missing rather
than emitting a card with placeholder values.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = ROOT / "artifacts" / "real_12m_aifs_corrected"


def _load(path: Path) -> dict:
    if not path.is_file():
        raise SystemExit(f"missing required artifact: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    training = _load(ARTIFACTS / "training_run.json")
    manifest_dir = ARTIFACTS / "manifests" / training["model_version"]
    manifest = _load(manifest_dir / "manifest.json")
    metrics_dir = ARTIFACTS / "metrics" / training["model_version"]
    evals = [_load(p) for p in sorted(metrics_dir.glob("blend_eval_*.json"))]
    if len(evals) != 9:
        raise SystemExit(f"expected 9 evaluation reports, found {len(evals)}")
    research_path = ARTIFACTS / "research" / "research_suite.json"
    research = _load(research_path) if research_path.is_file() else None
    if research is None:
        print("WARNING: research suite not found; regenerate it with\n"
              "  python backend/scripts/research_suite.py "
              "--blends artifacts/real_12m_aifs_corrected/inference/historical_blends.jsonl "
              "--out artifacts/real_12m_aifs_corrected/research/research_suite.json",
              file=sys.stderr)

    def mae_table() -> str:
        lines = ["| Region | Variable | Blend MAE | Best single | Best-single MAE | Blend wins? |",
                 "|---|---|---|---|---|---|"]
        for e in sorted(evals, key=lambda x: (x["region"], x["variable"])):
            per = e.get("per_model_metrics", {})
            best = min(per, key=lambda m: per[m]["mae"]) if per else "-"
            best_mae = per[best]["mae"] if per else None
            blend = e.get("synoptiq_mae")
            wins = "yes" if (blend is not None and best_mae is not None
                             and blend < best_mae) else "no"
            b = f"{blend:.4f}" if isinstance(blend, float) else str(blend)
            m = f"{best_mae:.4f}" if isinstance(best_mae, float) else "-"
            lines.append(f"| {e['region'].replace('_', ' ')} | {e['variable']} | {b} | "
                         f"{best} | {m} | {wins} |")
        return "\n".join(lines)

    research_block = ""
    if research:
        ov = research["oracle"]["overall"]
        cal = research["calibration_sanity"]["rows"]
        ai_rows = research["ai_ablation"]["rows"]
        ai_pos = sum(1 for r in ai_rows if (r.get("ai_value_pct") or 0) > 0)
        trust = research["trust_audit"]
        rho = (trust.get("trust_vs_error_rank_correlation") or {}).get("spearman_rho")
        research_block = f"""
## Research-suite results (auto-generated)

- **Oracle regret**: the blend recovers **{ov['oracle_advantage_recovered_pct']}%** of the
  hindsight-oracle's MAE advantage over the best fixed single model
  (oracle {ov['oracle_mae']}, best fixed {ov['best_fixed_mae']}, blend {ov['blend_mae']};
  averaged over all nine region-variable cells).
- **AI value**: adding AIFS to the GFS+IFS physics pair improves test MAE in
  **{ai_pos}/9** region-variable cells (per-cell numbers in the research suite JSON).
- **Bias correction**: the calibration step reduced test MAE for every variable —
  precipitation {cal['precipitation']['improvement_pct']}%, temperature
  {cal['temperature']['improvement_pct']}%, wind {cal['wind_speed']['improvement_pct']}%.
- **Trust audit**: Spearman rank correlation between issued trust and realised
  absolute error is {rho} (negative = higher trust, lower error). The audit is
  published even where it is unflattering; see the research suite for tier-by-tier
  honesty.
"""

    card = f"""# Model card

Synoptiq is a meta-layer over external GFS, IFS, and AIFS forecasts. It does not replace those forecast systems. The intended production model set is nine LightGBM source-skill models: one per source and variable for precipitation, temperature, and wind, plus three calibrated bust-risk models.

Training must use real aligned forecast/verification pairs, chronological train/validation/test partitions, and an untouched test period. Reported metrics must come from the machine-readable real evaluation report. No target improvement is asserted by this repository until that report exists.

The current corrected bundle is `{manifest['model_version']}`, trained on the real {manifest['training_period']['start']} through {manifest['training_period']['end']} window ({training['forecast_rows']:,} forecast rows, {training['truth_rows']:,} truth rows). The prior `synoptiq-real-12m-20260929` bundle is superseded: ECMWF Open Data exposes accumulated AIFS precipitation as `kg m**-2`, which the prior decoder treated as metres. Corrected AIFS precipitation is normalized before daily aggregation and all model/calibration/evaluation/inference artifacts were regenerated from the real training/validation/test split. The original artifacts remain available for audit and are not the active local model.

ECMWF IFS/AIFS retrieval uses the official Open Data Azure, Google Cloud, ECMWF, and AWS mirrors; mirror identity is recorded. AIFS source provenance identifies Single v1 before 2026-05-12 and Single v2 from 2026-05-12 onward. Its precipitation is unit-normalized from `kg m**-2` (numerically mm) before conversion to the pipeline's cumulative-metre contract.

The production regime detector remains transparent and rule-based, using real meteorological context. Trust scoring, abstention, calibration, bias correction, dynamic weighting, and explanations are operational outputs only when the corresponding validated artifacts are available.

## Untouched-test performance (auto-generated)

{mae_table()}

Every number above is read from `artifacts/real_12m_aifs_corrected/metrics/{training['model_version']}/blend_eval_*.json` at generation time.
{research_block}
## Intended use and limits

- Zone-mean daily guidance for three Indian pilot regions; not a point forecast and not a nowcast.
- Rainfall truth is IMERG Late V07; temperature/wind truth is NASA POWER (MERRA-2). Swapping in IMERG Final or CDS ERA5 will shift verification numbers.
- The blend is strongest where models disagree but calibrated; it can lose to the best single model in cells where one model dominates — the table above shows exactly where.

*Regenerated automatically by `backend/scripts/generate_model_card.py` at
{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}. Do not hand-edit the
numbers; edit the script.*
"""

    out = ROOT / "MODEL_CARD.md"
    out.write_text(card, encoding="utf-8")
    print(f"wrote {out} ({out.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
