# Synoptiq

Adaptive multi-model weather forecast intelligence that combines GFS, IFS and AIFS forecasts using context-aware ML weights, calibration, trust and bust-risk signals.

## Current architecture

- React + TypeScript + TanStack Start frontend
- FastAPI backend
- GFS + IFS + AIFS forecast sources
- IMERG rainfall verification
- ERA5 temperature/wind verification
- 9 LightGBM source-skill models
- 3 bust-risk classifiers plus threshold classifiers
- Quantile mapping and isotonic calibration
- Rule-based regime context
- SHAP-backed source explanations
- Historical replay and verification

## Python environment

Use the existing root environment only:

```powershell
D:\SIH2026\.venv\Scripts\python.exe
```

Do not create another `.venv` inside this project.

## Real-data credentials

GFS, IFS and AIFS are downloaded from public data endpoints.

For IMERG, configure NASA Earthdata credentials using the Windows `%USERPROFILE%\\_netrc` file expected by the downloader.

ERA5 is fetched through the public Open-Meteo Historical Weather API; no CDS credentials are required.

## Real-data training

Run a small validation window first:

```powershell
D:\SIH2026\.venv\Scripts\python.exe training\run_real_training.py --start YYYY-MM-DD --end YYYY-MM-DD --smoke-only
```

After the smoke test passes, run the real training pipeline:

```powershell
D:\SIH2026\.venv\Scripts\python.exe training\run_real_training.py --start YYYY-MM-DD --end YYYY-MM-DD
```

The pipeline fetches the real forecast and verification data, builds aligned Parquet training tables, loads the real rows into the backend database, trains the Synoptiq ML artifacts, evaluates on the untouched test split, and precomputes replay cases.

## Run locally

Backend:

```powershell
cd D:\SIH2026\Synoptiq\backend
D:\SIH2026\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Frontend, in another terminal:

```powershell
cd D:\SIH2026\Synoptiq
npm install
npm run dev
```

Open `http://localhost:5173`.

Set `SYNOPTIQ_API_URL=http://127.0.0.1:8000` for the frontend server proxy and keep `VITE_APP_MODE=LIVE` for real backend mode. DEMO mode is explicit and never used as an automatic fallback.

## Important scientific boundary

A real-data build is only considered complete after real GFS/IFS/AIFS forecasts are aligned with real IMERG/ERA5 verification data, Synoptiq models are retrained, and the untouched real test period has been evaluated. Synthetic prototype artifacts must not be presented as real forecast skill.
