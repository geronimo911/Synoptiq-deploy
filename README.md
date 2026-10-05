# Synoptiq

Synoptiq blends external NOAA GFS and ECMWF IFS/AIFS forecasts with a trained meta-layer. It is not a replacement forecast model. Backend mode is explicit: `SYNOPTIQ_MODE=real` uses only authentic data and real artifacts; `SYNOPTIQ_MODE=fake` uses only synthetic/demo data and artifacts. Legacy `LIVE`/`DEMO` values remain accepted aliases.

## Structure

- `backend/`: FastAPI API, database models, blending and versioned artifact loading.
- `frontend/`: React/TanStack frontend. LIVE mode reads the API; DEMO is opt-in and visibly identified.
- `training/`: real-data training and latest-cycle worker entrypoints.
- `data/real/` and `data/fake/`: isolated provider/training data and demo archive.
- `models/real/` and `models/fake/`: isolated trained model files.
- `artifacts/real_12m_aifs_corrected/`: active corrected REAL_12M models, calibration, inference, and seed bundle.
- `artifacts/real_12m/`: superseded AIFS-precipitation bundle retained for audit; do not deploy it.
- `artifacts/fake/`: isolated demo manifests, metrics and calibration.

## Research & verification suite (new)

`backend/scripts/research_suite.py` computes every proof claim offline from
the archived 12-month blend record (`artifacts/real_12m_aifs_corrected/
inference/historical_blends.jsonl` — per context: the three raw provider
values, issued weights, raw and bias-corrected blend, trust, bust
probability, abstain flag and observed truth) and writes
`artifacts/real_12m_aifs_corrected/research/research_suite.json`.

Headline results on the untouched test split (26 Nov 2025 - 25 Feb 2026,
n=4,140 contexts) and the full 12-month record:

- **Oracle regret**: the blend recovers **51.7%** of the hindsight-oracle's
  MAE advantage over the best fixed single model (oracle 0.764, best fixed
  1.049, blend 0.902 — averaged over the nine region-variable cells).
- **AI value**: adding AIFS to the GFS+IFS physics pair improves test MAE in
  **8 of 9** region-variable cells (up to -93% MAE in Kerala temperature).
- **Professional baselines**: persistence and climatology rows sit next to
  the per-model and naive-average rows, with cluster-bootstrap 95% CIs on
  the headline improvements (1,000 resamples over test days, seed 42).
- **Bias correction helps everywhere**: test MAE -7.1% (rain), -24.4%
  (temperature), -37.7% (wind).
- **Skill on hard days**: the blend's advantage concentrates on
  model-split days in Kerala rain (+9.9% hard vs +2.6% easy) and Kerala
  temperature (+63% vs +51%); the honest misses (BOB/IGP temperature) are
  published alongside.
- **IMD rainfall classes** (15.6 / 64.5 / 115.5 mm): full-year moderate-rain
  CSI 0.39 (blend) vs AIFS 0.50 / GFS 0.26; heavy-rain classes honestly
  report too few zone-mean events for stable estimates.
- **Trust audit**: Spearman rho(trust, |error|) = -0.13 (p ~ 1e-17) — higher
  trust really does mean lower error, though not perfectly monotone across
  tiers; the unflattering details are in the artifact, not hidden.

Served read-only by `/api/v1/research/*` and visualised on the new
**Proof & research** page (oracle regret, hard days, baselines with CIs,
AI-vs-physics ablation, IMD classes, trust audit, bias fingerprints,
lead-time crossovers, rolling 30-day skill evolution, and the
**Risk Priority Index** — an IMD-class action-level alert index with a date
picker to replay any archived day, e.g. `?date=2025-07-14` for a monsoon
day with 10 active alerts).

The blend endpoint now also returns an `arithmetic` block — the exact
`value x weight` chain from the three provider values through the weighted
sum and the bias-correction step to the final forecast — so the forecast
page can show "check us", not just "trust us".

`MODEL_CARD.md` is regenerated from the live artifacts by
`backend/scripts/generate_model_card.py` — never hand-edited.

Regenerate after any retrain:

```bash
python backend/scripts/research_suite.py \
  --blends artifacts/real_12m_aifs_corrected/inference/historical_blends.jsonl \
  --out artifacts/real_12m_aifs_corrected/research/research_suite.json
python backend/scripts/generate_model_card.py
```

## Operational layers (new)

Five gaps closed against the MoES/NCMRWF expectations, all computed from the
same archived REAL_12M record and served read-only:

**1. Model Trust Atlas (the PS-mandated model weight map)** — `/trust/atlas`
and the *Trust atlas* page: every region x variable x lead cell with the
issued weights, the measured untouched-test MAE per model, the dominating
model, the regime breakdown and a plain-language reason; plus a region x lead
dominating-model matrix and a schematic pilot-zone map (district shapefiles
plug into the same layer). Measured result: AIFS carries the largest issued
weight in 39 of 45 cells, GFS in 6, IFS in 0 — a finding the atlas states
rather than hides.

**2. Synoptic regime router** — `/regime/router` and the research page. The
rule-based regime detector already fed the meta-model; this layer makes the
conditioning explicit: the catalogue of seven Indian synoptic regimes, the
mean issued weights and measured test skill in each, and the measured policy
shift ("in the Cyclonic Low / Depression regime the issued weights stay within
2% of the all-weather profile"), so the regime contribution is quotable
rather than assumed.

**3. Physics-preserving extreme blending (peak smearing)** —
`backend/app/blending/peaks.py`, `/research/peak-preservation`. The audit
quantifies the drizzle problem on the real record: the blend's 99th
percentile is **0.64x** the observed 99th percentile, and on high-impact days
(obs >= 64.5 mm) the blend averages 48.0 mm against 74.9 mm observed. The
honest finding is that max-fusion cannot fix it — the sharpest single model
averages only 44.7 mm, so every model misses the same events. The layer is
therefore INACTIVE (alpha=0) rather than silently sharpening the field, and
risk is communicated through the split-conformal band (with its **measured**
test coverage: 98.8% rain, 81.2% temperature, 95.9% wind against a nominal
90%) and a Generalised-Pareto tail fit (rain xi=0.12, heavy-tailed as
expected). A descriptive full-record sweep flags wind for recalibration
(alpha~0.95 would cut high-impact MAE 4.16 -> 3.38) when the validation
window includes a wind season.

**4. Impact & Vulnerability Engine (disaster management)** —
`backend/app/impact.py`, `/impact/severity`, `/impact/cap/{zone}.xml` and the
*Impact & alerts* page. The blended hazard becomes an Impact Severity Index
(0-10) per zone, scaled by a documented static vulnerability factor, mapped to
IMD colour codes, with NDRF-style advisories and one downloadable OASIS CAP
1.2 alert XML per zone. Worked example from the record: 30 May 2025 — Kerala
Western Ghats ISI 6.52 (ORANGE), Bay of Bengal coast 3.86 (YELLOW),
Indo-Gangetic Plains 1.66 (GREEN).

**5. Routine operational workflow** — `scripts/run_routine_blend.sh` (and the
PowerShell twin for Windows Task Scheduler), `backend/scripts/export_bulletin.py`,
`/ops/status`, `/ops/bulletin?format=json|csv|txt` and the *Operations* page.
One command runs the whole forecast-office loop: ingest the latest real cycle
(or say honestly that it is unavailable and continue on the archive), rebuild
every artifact, export the standard bulletin (JSON + CSV + plain text + CAP
XMLs) and append a line to `ops/run_log.jsonl`. Cron and Task Scheduler
snippets are on the Operations page.

Regenerate everything after a retrain (this is what the routine script runs):

```bash
python backend/scripts/research_suite.py --blends artifacts/real_12m_aifs_corrected/inference/historical_blends.jsonl --out artifacts/real_12m_aifs_corrected/research/research_suite.json
python backend/scripts/build_advanced_artifacts.py --blends artifacts/real_12m_aifs_corrected/inference/historical_blends.jsonl --out-dir artifacts/real_12m_aifs_corrected/research
python backend/scripts/export_bulletin.py
```

## Live data, automation and packaging (new)

**AIFS now goes LIVE.** Two bugs kept it out of LIVE mode while GFS and IFS
worked: the Open-Meteo model id was `ecmwf_aifs025`, which returns HTTP 200
with an all-null array (silent failure) instead of the correct
`ecmwf_aifs025_single`; and the ECMWF Open Data fallback stepped back a full
cycle and never tried the newest one. Both are fixed, and the mirror path was
verified end-to-end (AIFS steps download from the Google and Azure mirrors;
no API key is required).

**Nothing needs to be run by hand, ever.** `backend/app/live_scheduler.py`
starts a background refresher inside the API process: it catches up on
startup and then refreshes every `SYNOPTIQ_LIVE_REFRESH_MINUTES` (default 15)
for the life of the deployment — weeks or months, container or bare uvicorn.
`deploy/` ships systemd units and a crontab example for self-hosted hosts;
`render.yaml` already has a 15-minute Render cron job; the Dockerfile now
copies `training/`, `scripts/` and `ops/` so the worker runs in-container.
Check it any time with `GET /api/v1/ops/live-refresh/status`, or force one
with `POST /api/v1/ops/live-refresh`.

**The blend endpoint now carries uncertainty and regime.** `/api/v1/forecast/blend`
recomputes the 90% split-conformal interval and the Generalised-Pareto tail
from the **production database's own validation residuals**
(`backend/app/validation_stats.py`: `forecasts.split == 'val'` joined to
`ground_truth`, blended with the same vectorized path the trainer uses), and
returns it alongside the active synoptic regime, the weight policy and the
peak-preservation state. The response shape is documented in `schemas.py`
(`BlendResponseV2`) and visible at `/docs`.

**The regime-router weights are applied at their measured optimum.** The
router reports its regime-mean weights, but the measurement on the untouched
test split (`regime_router.json → weight_policy_measurement`) shows
`lambda = 0` is best for every variable — the meta-model already receives
regime probabilities as features, so substituting the coarser regime-mean
weights costs accuracy (temperature MAE 0.99 → 1.48 at lambda = 1). The
endpoint therefore applies the meta-model weights, reports the router weights
alongside for audit, and exposes `SYNOPTIQ_REGIME_WEIGHT_LAMBDA` for
experiments. The numbers behind that decision ship in the artifact.

**Packaging.** `bash package_submission.sh` builds the submission archive,
verifying before and after zipping that every required source directory is
present (`backend/app`, `backend/app/blending`, `backend/scripts`,
`backend/tests`, `training`, `frontend`, `artifacts`, `models`) and refusing
to emit an archive if any is missing or empty. It packages raw source only —
no `npm install`, no build — and excludes `node_modules/`, `__pycache__/` and
`.git/`.

**Upper-air scoping.** `UPPER_AIR_SCOPE.md` specifies the 850 hPa wind and
500 hPa height pull needed for a true synoptic classifier: exact Open-Meteo
parameters, the u/v decomposition convention, measured payload and latency
(289-point 2.5° grid = 1.74 MB in 1.5 s per model), the reshape contract for
the ML pipeline, and the leakage rules for PCA. No code yet, as scoped.

## Local setup

Use the existing environment, not a new virtual environment:

```powershell
. D:\SIH2026\.venv\Scripts\Activate.ps1
D:\SIH2026\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
D:\SIH2026\.venv\Scripts\python.exe -m pip install -r training\requirements-realdata.txt
npm --prefix frontend install
```

Copy the root `.env.example` to `.env` and fill the provider and database values. Runtime Python configuration reads this root file only. Real mode requires PostgreSQL (or the isolated training role for local prototype validation), a validated real model manifest, and `SYNOPTIQ_MODEL_VERSION`. Fake mode uses only its local SQLite archive and fake artifacts.

## Real data

Configure NASA Earthdata username/password for IMERG. ERA5 uses the public Open-Meteo Historical Weather API with the ERA5 dataset. GFS, IFS, and AIFS use their public provider endpoints. Credentials belong only in backend/training secrets, never in the frontend. See [DATA_SOURCES.md](DATA_SOURCES.md).

## Training and serving

Run the real-data smoke test before training, then:

```powershell
D:\SIH2026\.venv\Scripts\python.exe training\run_real_training.py --end YYYY-MM-DD
D:\SIH2026\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port 8000
npm --prefix frontend run dev
```

Training and live ingestion are separate processes. The API serves frozen validated artifacts; the scheduled worker retrieves the newest cycle and writes forecasts to PostgreSQL. See [TRAINING.md](TRAINING.md) and [DEPLOYMENT.md](DEPLOYMENT.md).

## Honest evaluation

Only the untouched real test-period report may support accuracy claims. The repository does not claim a percentage improvement without actual metrics. `/api/v1/system/status` reports readiness, model version, database state, and ingestion provenance without inventing values.
