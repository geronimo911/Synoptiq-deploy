# Deployment

Synoptiq has three separately deployed concerns: the Vercel frontend, the Render FastAPI web service, and a Render PostgreSQL database. There is **no Render Cron Job**. PostgreSQL is the production source of truth and the shared lock backend; the Render filesystem is not used for permanent raw data.

```
              ┌──────────────────────┐
              │       Vercel         │
              │ TanStack Start UI    │
              └──────────┬───────────┘
                         │ HTTPS
                         ▼
              ┌──────────────────────┐
              │       Render         │
              │    FastAPI Backend   │
              └──────────┬───────────┘
                         │
             ┌───────────┴───────────┐
             ▼                       ▼
   ┌──────────────────┐    ┌────────────────────┐
   │ Render PostgreSQL│    │ Model/Data Files   │
   │ persistent state │    │ artifacts/         │
   │ shared locking   │    │ real models        │
   └──────────────────┘    └────────────────────┘
```

## Render

Use `render.yaml`, or create the web service and database separately. The blueprint selects the corrected `synoptiq-real-12m-20260930-aifs-unitfix` bundle under `artifacts/real_12m_aifs_corrected/`; deploy that directory with its manifest, models, calibration, metrics, and compressed corrected history seed together.

Set `SYNOPTIQ_CORS_ORIGINS` (the exact deployed Vercel origin), `NASA_EARTHDATA_USERNAME`, and `NASA_EARTHDATA_PASSWORD` in Render secrets. **Do not set `DATABASE_URL` by hand** — Render injects it from the `synoptiq-postgres` database (`fromDatabase: connectionString`), and the app reads it via `os.environ["DATABASE_URL"]`. It is never hard-coded and never exposed to the frontend.

Before the API starts, `python training/realdata/seed_corrected_history.py` imports the corrected historical forecasts, observations and frozen skill into PostgreSQL. It is idempotent: it reads its `synoptiq_history_seed` ledger first and returns `already_seeded` without extracting or validating the archive when the active model version is present, so restarts are fast and existing production data is never rebuilt. Pass `--force` only to deliberately reseed.

Historical training uses `TRAINING_DATABASE_URL` locally and never receives or changes the production `DATABASE_URL`.

## Live data: demand-driven, no cron

The deployment no longer refreshes on a schedule. Freshness is handled on request:

```
user opens the app
  -> the UI renders immediately from the current cached data
  -> the frontend calls POST /api/v1/system/refresh-if-stale in the background
  -> fresh?  yes -> nothing happens
             no  -> acquire the PostgreSQL advisory lock
                    re-check freshness under the lock
                    run the existing worker once
                    persist the new state
                    release the lock
                    frontend refetches the affected queries
```

The `15` in `SYNOPTIQ_REFRESH_MIN_AGE_MINUTES` is a **freshness threshold**, not a schedule.

| Variable | Meaning |
| --- | --- |
| `SYNOPTIQ_AUTO_REFRESH` | `0` disables the in-process background scheduler entirely. Production sets `0`. |
| `SYNOPTIQ_REFRESH_ON_REQUEST` | `1` enables `POST /api/v1/system/refresh-if-stale`. |
| `SYNOPTIQ_REFRESH_MIN_AGE_MINUTES` | A refresh is only attempted when the last cycle is older than this. |

`POST /api/v1/system/refresh-if-stale` returns:

* `{"status": "fresh", "refreshed": false, "last_refresh": "..."}` when the cached cycle is still fresh;
* `{"status": "refresh_started", "refreshed": false}` by default — the call returns at once and the refresh runs in the background, so the UI never waits (poll `GET /api/v1/ops/live-refresh/status` for the outcome);
* with `?wait=true`, the synchronous result: `refreshed` / `already_refreshed` / `refresh_failed`;
* `{"status": "refresh_in_progress", "refreshed": false}` when another caller already holds the lock.

**Concurrency.** Several clients opening the app at once still run at most one refresh. Callers are serialised with a PostgreSQL **session-level advisory lock** (`backend/app/refresh_lock.py`), not a process-local lock, so the guarantee holds across multiple Render instances. After acquiring the lock the code re-checks freshness, so a caller that queued behind a completed refresh returns `already_refreshed` instead of fetching again. The lock is released in a `finally` block, so a failed refresh can never leave it permanently held. On SQLite the lock degrades to a process-local lock, which is correct for local development and tests.

The refresh work itself is not reimplemented: `backend/app/on_demand_refresh.py` delegates to `live_scheduler.run_refresh_once()`, which runs `training/realdata/live_refresh.py --once` as a subprocess. The worker never substitutes synthetic data — if a provider is unavailable it records the per-provider status and continues on the archived record.

Manual and self-hosted operation still works: `POST /api/v1/ops/live-refresh` triggers a refresh on demand, and `deploy/` ships ready-made systemd units and a `crontab.example` for anyone who prefers a schedule.

### AIFS in LIVE mode — the model-id bug, fixed

AIFS previously never reached LIVE while GFS and IFS did. Two causes, both found and fixed:

1. **Wrong Open-Meteo model id.** The live path requested `ecmwf_aifs025`, which Open-Meteo answers with **HTTP 200 and an all-null array** — a silent failure, not an error. The correct id is **`ecmwf_aifs025_single`** (verified 2026-10-01: 24/24 non-null hourly values). `fetch_openmeteo.py` now uses it and the non-finite error message names the model id so this class of bug is loud next time.
2. **Fallback cycle selection stepped back too far.** When the Open-Meteo path failed, the ECMWF Open Data fallback subtracted a full cycle from the newest one and only tried two candidates, so at 06:45 UTC it tried yesterday's 12Z and 00Z and never today's 00Z — which was already published. The fallback now walks **newest-first** through four candidate cycles and tries the cloud mirrors (Azure, Google, AWS) before `data.ecmwf.int`, which is rate-limited and the most likely to fail.

No ECMWF API key is needed for any of this: AIFS is on the public Open Data mirrors. `ECMWF_API_KEY` / `ECMWF_API_EMAIL` are used only by the MARS archive path (`ecmwf_archive.py`) for historical backfill.

### CORS origins — no trailing slash

`SYNOPTIQ_CORS_ORIGINS` must contain the **bare origin**, with no trailing slash
and no path:

```
SYNOPTIQ_CORS_ORIGINS=https://synoptiq-deploy.vercel.app
```

A browser sends `Origin: https://synoptiq-deploy.vercel.app` — never with a
trailing slash — and `CORSMiddleware` matches the list exactly, so a value like
`https://synoptiq-deploy.vercel.app/` matches nothing and **every cross-origin
response is silently blocked by the browser**. The API keeps returning 200 in
the Render logs while the frontend shows empty pages, which makes it hard to
spot. The backend now strips whitespace and trailing slashes from every
configured origin, so this mistake cannot break the deployment, but set it
correctly anyway. Several origins are comma-separated.

Alternatively, leave `VITE_API_BASE_URL` unset on Vercel: the browser then calls
same-origin `/api/v1` and the Nitro gateway proxies server-side, so CORS does
not apply at all.

### Freshness follows the provider cycle, not the visit

A live provider counts as current for as long as the cycle it holds is still the
**current published cycle** for that model — GFS and IFS 6-hourly plus publish
grace, AIFS 12-hourly plus grace (`api_v1.MODEL_FRESHNESS_MINUTES`).

This used to be a fixed 45-minute *ingestion* window, which was wrong for a
demand-driven deployment: refresh only runs when somebody opens the app, so the
ingestion age just measures "time since the last visit". With a 45-minute gate
the UI declared itself `DEGRADED REAL` on any quiet afternoon and withheld the
operational forecast, even though the cycle it was serving was perfectly
current. If you ever see `DEGRADED REAL · HISTORICAL REAL_12M` while
`/api/v1/system/status` shows recent `last_successful_ingestion_time`, the
providers are genuinely not validating — check the per-provider
`validation_result` there.

`SYNOPTIQ_LIVE_FRESHNESS_MINUTES` is now only the fallback window for a model
that is not in the cycle table.

### Provider fallbacks walk backwards through cycles

Both the NOAA GFS and the ECMWF Open Data fallbacks try the newest cycle first
and then step back through older ones. Model products are published hours after
their cycle time (GFS ~3.5–5 h, IFS ~4 h, AIFS ~5 h), so a fallback that only
tried the current cycle would fail outright for much of the day. GFS previously
did exactly that and went `UNAVAILABLE` while IFS recovered via its own
multi-cycle walk.

## Vercel

The frontend is TanStack Start built on Nitro. `frontend/vite.config.ts` registers `nitro()` alongside `tanstackStart()`, `viteReact()` and `tailwindcss()`; Vercel detects TanStack Start + Nitro and applies the correct build settings. `frontend/vercel.json` pins the framework preset (`"framework": "tanstack-start"`) and deliberately contains **no** SPA catch-all rewrite — routing is handled by Nitro, not by rewriting every path to `index.html`.

Set the Vercel project's **Root Directory** to `frontend`, and set only `VITE_API_BASE_URL` to the Render `/api/v1` URL with `VITE_APP_MODE=LIVE`. Do not add NOAA, ECMWF, NASA, CDS, PostgreSQL, or model credentials to Vercel, and never create `VITE_DATABASE_URL`.

`installCommand` is set to `npm install` because the committed `package-lock.json` is currently out of sync (`npm ci` fails on missing `@emnapi/*` entries). Run `npm install` once locally and commit the regenerated lock file to restore `npm ci`.

The frontend does not block on the refresh: it loads cached API data, renders, then calls `refresh-if-stale` asynchronously and invalidates the affected queries once the backend reports fresh data. If the refresh fails or is disabled, the UI keeps showing the last available data.

## Local

Set `SYNOPTIQ_MODE=real` to use the corrected real bundle or `SYNOPTIQ_MODE=fake` to use the explicit local demo archive. Real mode requires PostgreSQL for normal serving; local validation can select `SYNOPTIQ_DATABASE_ROLE=TRAINING` with `TRAINING_DATABASE_URL` pointing to `data/real/training_aifs_corrected.sqlite3`.

For a manual refresh, run `python training/realdata/live_refresh.py`; use `--once` for a single cycle. The worker records provider status, freshness, coverage, and mirror provenance with every ingestion.

## Health

`GET /health` is a lightweight liveness probe for the platform health check. It does not seed the database, load models, or trigger a refresh, and it stays green if the database is briefly unreachable (it reports `"database": "unavailable"` instead of failing). Use `GET /api/v1/system/status` for LIVE readiness, model provenance and ingestion status.
