from __future__ import annotations
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import logging
import os

from app.database import Base, engine, SessionLocal
from app.models_db import ForecastRow
from app.routers import regions, forecast, verification, replay
from app.api_v1 import router as api_v1_router

Base.metadata.create_all(bind=engine)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
log = logging.getLogger("synoptiq.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Optionally start the in-process live-refresh scheduler.

    Production runs with SYNOPTIQ_AUTO_REFRESH=0: no scheduled refresh, no
    cron. Freshness is demand-driven instead (POST /api/v1/system/refresh-if-stale),
    so the scheduler below stays off and the API simply serves cached data.
    """
    from app.config import AUTO_REFRESH, RUNTIME_MODE
    from app.database import engine

    log.info(
        "synoptiq-api starting: mode=%s database=%s auto_refresh=%s",
        RUNTIME_MODE,
        engine.dialect.name,
        AUTO_REFRESH,
    )
    started = False
    if RUNTIME_MODE == "real":
        from app import live_scheduler
        started = live_scheduler.start()
    log.info("synoptiq-api ready (background_scheduler=%s)", started)
    yield
    if started:
        from app import live_scheduler
        live_scheduler.stop()


app = FastAPI(
    title="Synoptiq API",
    description=(
        "Adaptive AI-NWP multi-model forecast blending. Learns which forecast source "
        "to trust by region, lead time, season and weather regime, then "
        "blends, bias-corrects, calibrates and explains the result."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

def _normalise_origin(origin: str) -> str:
    """Normalise a configured CORS origin.

    CORSMiddleware compares the browser's ``Origin`` header to this list with
    an exact string match, and a browser NEVER sends a trailing slash. So a
    value like ``https://app.vercel.app/`` silently matches nothing and every
    cross-origin response is blocked (the API keeps returning 200 in the logs
    while the frontend shows empty pages). Strip whitespace and any trailing
    slash so that mistake cannot take the deployment down.
    """
    return origin.strip().rstrip("/")


cors_origins = [
    _normalise_origin(origin)
    for origin in os.getenv(
        "SYNOPTIQ_CORS_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173",
    ).split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(regions.router)
app.include_router(forecast.router)
app.include_router(verification.router)
app.include_router(replay.router)
app.include_router(api_v1_router)


@app.api_route("/health", methods=["GET", "HEAD"])
def health():
    """Lightweight liveness probe for the platform health check.

    Deliberately does NOT seed the database, load models, or trigger a refresh:
    Render polls this on every deploy and restart, so it must stay fast and must
    not fail when the database is briefly unreachable.
    """
    payload = {"status": "ok", "service": "synoptiq-api"}
    try:
        db = SessionLocal()
        try:
            payload["forecast_rows_cached"] = db.query(ForecastRow).count()
            payload["database"] = "ok"
        finally:
            db.close()
    except Exception:  # database down must not turn the probe red
        payload["database"] = "unavailable"
    payload["note"] = (
        "Use /api/v1/system/status for LIVE readiness, model provenance and "
        "ingestion status."
    )
    return payload


@app.api_route("/", methods=["GET", "HEAD"])
def root():
    return {
        "name": "Synoptiq API",
        "docs": "/docs",
        "quickstart": [
            "GET /api/v1/regions",
            "GET /api/v1/replay/events",
            "GET /api/v1/forecast/blend?region=KWG&variable=precipitation&lead_hours=72",
            "GET /api/v1/skill/verification",
        ],
    }
