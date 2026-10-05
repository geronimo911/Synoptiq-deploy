# Synoptiq backend

FastAPI backend for Synoptiq adaptive multi-model weather forecast blending.

## Production flow

GFS + IFS + AIFS forecasts are normalized into the common forecast contract, enriched with regime/context and historical skill, scored by the trained Synoptiq meta-models, dynamically weighted, blended, calibrated, and passed through trust/bust/abstention logic before being served through `/api/v1`.

## Training

The real-data training workflow lives at `../training/`. It reads only `../data/real/`, builds aligned Parquet tables from real forecast and verification sources, and writes models to `../models/real/` plus reports/calibration to `../artifacts/real/`. Fake/demo assets are isolated under their matching `fake` roots.

The backend should load production artifacts only after the real-data training workflow has passed its smoke gate and untouched test evaluation.

## API

The frontend-facing contract is under `/api/v1` and provides health, metadata, forecast blending, weight maps, verification, extreme guidance, and replay.
