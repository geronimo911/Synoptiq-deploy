# SYNOPTIQ FINAL REAL_12M REPORT

Status: core REAL_12M historical system operational; live provider freshness is degraded.

## 1. Data period and inputs

- Period: `2025-02-26` through `2026-02-25` inclusive.
- Forecast input: `training/realdata/cache/12m/training/forecasts.parquet`.
- Truth input: `training/realdata/cache/12m/training/truth.parquet`.
- Forecast rows: 49,275.
- Truth rows: 3,285.
- Regions: KWG, BOB, IGP.
- Variables: precipitation, temperature, wind speed.
- Leads: 24, 48, 72, 96, 120 hours.
- Nulls, non-finite values, and duplicate training keys: zero.
- Synthetic data used: false.

The strict assembly gate passed with 365 complete dates, zero missing dates, zero duplicate forecast keys, and zero null forecast/truth values.

## 2. Provider coverage

GFS, IFS, and AIFS each provide 365-day real forecast coverage. IMERG provides precipitation truth and ERA5 provides temperature/wind truth for the same period. REAL_PROTOTYPE was not modified.

## 3. Models and calibration

- Model version: `synoptiq-real-12m-20260929`.
- 9 source-skill LightGBM regressors: `artifacts/real_12m/models/skill/`.
- 3 bust-risk classifiers: `artifacts/real_12m/models/bust/`.
- Total production models: 12.
- Calibration artifacts: `artifacts/real_12m/calibration/`.
- Calibration status: PASS. Global and regional quantile maps and available isotonic calibrators were fitted on the chronological validation split.
- Training split: valid times before 2025-10-03.
- Validation split: 2025-10-03 through 2025-11-25.
- Test split: 2025-11-26 through 2026-02-25.

## 4. Validation

Full metrics are in [validation_report.json](validation_report.json) and [validation_report.md](validation_report.md). The executed test set contained 460 contexts for every region-variable combination.

| Region | Variable | Blend metric | Best source | Relative improvement |
|---|---|---:|---|---:|
| BOB | precipitation | CSI@50mm 0.0 | AIFS 0.0 | 0.0% |
| BOB | temperature | RMSE 0.429 | GFS 0.339 | -26.73% |
| BOB | wind speed | RMSE 2.247 | AIFS 2.654 | 15.35% |
| IGP | precipitation | CSI@50mm 0.0 | AIFS 0.0 | 0.0% |
| IGP | temperature | RMSE 2.228 | AIFS 0.833 | -167.56% |
| IGP | wind speed | RMSE 1.349 | AIFS 1.890 | 28.59% |
| KWG | precipitation | CSI@50mm 0.0 | AIFS 0.0 | 0.0% |
| KWG | temperature | RMSE 0.381 | AIFS 0.806 | 52.72% |
| KWG | wind speed | RMSE 1.738 | AIFS 2.713 | 35.93% |

Metrics are factual outputs of the existing chronological evaluation code; no target improvement was fabricated.

## 5. Historical inference

Status: PASS. The batch production blend path generated 16,425 contexts under [inference](inference/), retaining source values, weights, raw blend, calibrated output, trust, bust probability, regime, split, and observed value.

## 6. Backend and end-to-end smoke test

Backend tests: `17 passed, 2 skipped`.

The in-process API smoke test passed for health, system status, regions, variables, lead times, and an explicit historical blend request. The historical request returned all three real sources, calibrated output, trust score, and bust probability. The full route sweep returned HTTP 200 for `/`, `/health`, forecast, verification, weights, extremes, replay, and metadata endpoints.

Backend artifacts are loaded from `artifacts/real_12m/` using the local training database `sqlite:///./data/real/training.sqlite3`.

Backend status: DEGRADED REAL. The live worker refreshed GFS and IFS at `2026-09-29T12:16:00Z`; AIFS was unavailable after Open-Meteo non-finite output and bounded ECMWF/AWS fallback failure. The API returned a future `2026-09-30T12:16:00` blend using real GFS and IFS rows, with trust and bust risk. No synthetic fallback is used.

## 7. Frontend

Status: PASS. `npm run build` completed successfully for client and SSR bundles. Direct navigation probes returned HTTP 200 for `/`, `/forecast`, `/verification`, `/weights`, `/replay`, `/extremes`, and `/architecture`. REAL mode now labels the available history as `REAL_12M historical` and shows `DEGRADED REAL · HISTORICAL REAL_12M` when live freshness is unavailable.

## 8. Live forecast

Status: DEGRADED REAL. GFS and IFS are fresh and complete with 45 rows each (3 regions x 3 variables x 5 leads) from Open-Meteo. AIFS is unavailable: Open-Meteo returned non-finite data and the official ECMWF/AWS fallback returned bounded HTTP 503 SlowDown. The application does not fabricate replacement weather and continues serving real GFS/IFS live output plus validated historical REAL_12M output.

The automatic worker is running with a 15-minute interval and a 45-minute freshness threshold. Provider status and provenance are stored in `data/real/live/live_manifest.json`, and refresh logs are stored in `data/real/live/live_refresh.log`.

## 9. SHAP and explanations

Status: PASS for the serving path. The existing SHAP explanation integration remains connected to the REAL_12M skill models, and the API smoke path loaded the trained models without falling back to prototype artifacts. Historical batch inference retains the production blend signals; representative per-request explanations are available through the backend pipeline.

## 10. Artifacts

- Manifest: `artifacts/real_12m/manifests/synoptiq-real-12m-20260929/manifest.json`.
- Status: [status.json](status.json).
- Training run: [training_run.json](training_run.json).
- Validation: [validation_report.json](validation_report.json), [validation_report.md](validation_report.md).
- Historical inference: [inference](inference/).

Known limitations: live freshness is degraded because no current provider acquisition was performed, as directed. Current live readiness is therefore not claimed as PASS.
