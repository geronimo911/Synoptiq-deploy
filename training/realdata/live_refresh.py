"""Independent real-time provider refresh for GFS, IFS, and AIFS."""
from __future__ import annotations

import json
import logging
import math
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
LIVE_DIR = Path(os.getenv("SYNOPTIQ_LIVE_DATA_DIR", str(ROOT / "data" / "real" / "live"))).resolve()
LOG_PATH = LIVE_DIR / "live_refresh.log"
MODEL_VERSION = os.getenv("SYNOPTIQ_MODEL_VERSION", "synoptiq-real-12m-20260929")
LEADS = [24, 48, 72, 96, 120]
ZONES = ["kerala_western_ghats", "bay_of_bengal_east_coast", "indo_gangetic_plains"]
MODELS = {
    "GFS": ("gfs", "NCEP GFS Global 0.11/0.25"),
    "IFS": ("ifs", "ECMWF IFS 0.25"),
    "AIFS": ("aifs-single", "ECMWF AIFS 0.25 Single"),
}

LIVE_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(filename=LOG_PATH, level=logging.INFO, format="%(asctime)s %(message)s")


def _atomic_write_parquet(frame: pd.DataFrame, path: Path) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_parquet(temporary, index=False, compression="zstd")
    temporary.replace(path)


def _live_rows(model: str, retrieved_at: datetime) -> list[dict]:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from fetch_openmeteo import REPRESENTATIVE_POINTS, fetch_live_cycle

    source_model = MODELS[model][1]
    source_rows = fetch_live_cycle(MODELS[model][0], retrieved_at.strftime("%Y-%m-%d"))
    rows = []
    for item in source_rows:
        lead = int(item["step"])
        valid_time = retrieved_at + timedelta(hours=lead)
        common = {
            "model": model,
            "region": item["zone"],
            "valid_time": valid_time.replace(tzinfo=None),
            "lead_hours": lead,
            "run_time": retrieved_at.replace(tzinfo=None),
            "model_generation": f"live:{source_model}",
            "season": _season(valid_time.month),
            "regime": "normal",
            "regime_probs": None,
            "lat": float(np.mean([point[0] for point in REPRESENTATIVE_POINTS[item["zone"]]])),
            "lon": float(np.mean([point[1] for point in REPRESENTATIVE_POINTS[item["zone"]]])),
            "retrieved_at_utc": retrieved_at.isoformat(),
            "source_provider": "Open-Meteo",
            "source_model": source_model,
            "source_transport": "https",
            "source_run_time": None,
            "run_time_basis": "retrieval_time",
        }
        rows.extend([
            {**common, "variable": "temperature", "forecast_value": float(item["t2m_c"])},
            {**common, "variable": "precipitation", "forecast_value": float(item["precip_mm"])},
            {**common, "variable": "wind_speed", "forecast_value": float(item["wind_ms"]) * 3.6},
        ])
    return rows


def _fallback_rows(model: str, retrieved_at: datetime) -> list[dict]:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    if model == "GFS":
        import fetch_gfs
        # Same newest-first discipline as the ECMWF path below. GFS lands on the
        # NOAA/AWS bucket roughly 3.5-5 hours after its cycle time, so the
        # current 6-hourly cycle is frequently not published yet. The old code
        # tried that one cycle only and failed outright, which is why GFS went
        # UNAVAILABLE while IFS recovered via its own fallback. Walk backwards
        # and take the newest cycle that is actually there.
        base_cycle = retrieved_at.replace(
            hour=(retrieved_at.hour // 6) * 6, minute=0, second=0, microsecond=0
        )
        last_error = None
        for offset in range(4):
            cycle = base_cycle - timedelta(hours=6 * offset)
            try:
                raw = fetch_gfs.fetch_cycle(
                    cycle.strftime("%Y-%m-%d"),
                    cycle.hour,
                    list(range(6, 145, 6)),
                )
            except Exception as exc:
                last_error = exc
                logging.warning(
                    "provider=GFS cycle=%s failure=%s", cycle.isoformat(), type(exc).__name__
                )
                continue
            if not raw:
                continue
            try:
                return _aggregate_archive_rows(
                    model, raw, retrieved_at, "NOAA/AWS", "noaa-gfs-bdp-pds"
                )
            except Exception as exc:
                last_error = exc
                logging.warning(
                    "provider=GFS cycle=%s invalid=%s: %s",
                    cycle.isoformat(), type(exc).__name__, exc,
                )
        if last_error:
            raise RuntimeError("No published NOAA GFS cycle was usable") from last_error
        raise RuntimeError("No published NOAA GFS cycle returned forecast rows")

    import fetch_ecmwf
    # Cycle cadence and publish delay per model. AIFS runs on a 12-hour cycle
    # and its products reach the cloud mirrors several hours after the cycle
    # time, so the newest cycle must still be TRIED (it is often already
    # published) and only skipped when it is genuinely not there yet. The
    # previous code stepped straight back one cycle and never attempted the
    # newest one, which left AIFS on a stale cycle and therefore not LIVE.
    cycle_hours = 12 if model == "AIFS" else 6
    publish_delay_hours = {"AIFS": 5, "IFS": 4}.get(model, 3)
    latest_cycle = retrieved_at.replace(
        hour=(retrieved_at.hour // cycle_hours) * cycle_hours,
        minute=0,
        second=0,
        microsecond=0,
    )
    # newest-first candidate list: the current cycle, then walk backwards.
    # A cycle whose time is within the publish delay is still attempted —
    # the per-step fetch fails fast (404) when it is not yet published, and
    # a complete-but-slightly-younger cycle is always better than a stale one.
    candidates = [latest_cycle - timedelta(hours=cycle_hours * offset) for offset in range(4)]
    steps = list(range(6, 175, 6)) if model == "AIFS" else list(range(6, 145, 6))
    last_error = None
    # cloud mirrors first: data.ecmwf.int is rate-limited (500 concurrent
    # connections) and is the most likely to fail; azure/google/aws are the
    # official replicas and carry AIFS.
    sources = ("azure", "google", "aws", "ecmwf")
    for cycle in candidates:
        for source in sources:
            try:
                raw = fetch_ecmwf.fetch_cycle(
                    MODELS[model][0], cycle.strftime("%Y-%m-%d"), cycle.hour,
                    steps, source=source, maximum_retries=1,
                )
            except Exception as exc:
                last_error = exc
                logging.warning(
                    "provider=%s source=%s cycle=%s failure=%s",
                    model, source, cycle.isoformat(), type(exc).__name__,
                )
                continue
            if not raw:
                continue
            try:
                return _aggregate_archive_rows(
                    model, raw, retrieved_at, "ECMWF Open Data",
                    f"ECMWF Open Data ({source})",
                )
            except Exception as exc:
                last_error = exc
                logging.warning(
                    "provider=%s source=%s cycle=%s invalid=%s: %s",
                    model, source, cycle.isoformat(), type(exc).__name__, exc,
                )
    if last_error:
        raise RuntimeError(f"No published ECMWF {model} cycle was usable") from last_error
    raise RuntimeError(f"No published ECMWF {model} cycle returned forecast rows")


def _aggregate_archive_rows(model: str, raw_rows: list[dict], retrieved_at: datetime, provider: str, transport: str) -> list[dict]:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from fetch_openmeteo import REPRESENTATIVE_POINTS
    frame = pd.DataFrame(raw_rows)
    if frame.empty:
        raise RuntimeError("fallback returned no rows")
    source_runs = frame[["run_date", "run_hour"]].drop_duplicates()
    if len(source_runs) != 1:
        raise RuntimeError("fallback returned rows from multiple source cycles")
    source_run = source_runs.iloc[0]
    cycle_time = datetime.strptime(
        f"{source_run['run_date']} {int(source_run['run_hour']):02d}", "%Y-%m-%d %H"
    ).replace(tzinfo=timezone.utc)
    generations = frame.get("model_generation", pd.Series(dtype=str)).dropna().astype(str).unique()
    if len(generations) > 1:
        raise RuntimeError("fallback returned rows from multiple model generations")
    generation = generations[0] if len(generations) else MODELS[model][1]
    source_model = "AIFS Single" if model == "AIFS" else MODELS[model][1]
    if model == "AIFS" and generation != "AIFS Single v2":
        raise RuntimeError(f"fallback returned unexpected AIFS generation: {generation}")
    rows = []
    for zone, group in frame.groupby("zone"):
        indexed = group.set_index("step")
        for lead in LEADS:
            if model == "AIFS":
                valid_time = retrieved_at + timedelta(hours=lead)
                source_offset = (valid_time - cycle_time).total_seconds() / 3600
                temperatures = [
                    _interpolate_step(indexed, "t2m_c", source_offset + offset)
                    for offset in (0, 6, 12, 18)
                ]
                winds = [
                    _interpolate_step(indexed, "wind_ms", source_offset + offset)
                    for offset in (0, 6, 12, 18)
                ]
                precipitation_start = _interpolate_step(indexed, "tp_cum_m", source_offset)
                precipitation_end = _interpolate_step(indexed, "tp_cum_m", source_offset + 24)
                temperature_value = float(np.mean(temperatures))
                wind_value = max(winds) * 3.6
                precipitation = max(0.0, precipitation_end - precipitation_start) * 1000.0
            else:
                steps = [lead + offset for offset in (0, 6, 12, 18)]
                if any(step not in indexed.index for step in steps):
                    raise RuntimeError(f"fallback missing {zone} lead {lead}")
                subset = indexed.loc[steps]
                temperature_value = float(subset["t2m_c"].mean())
                wind_value = float(subset["wind_ms"].max()) * 3.6
                if model == "GFS":
                    precip_steps = [lead + offset for offset in (6, 12, 18, 24)]
                    if any(step not in indexed.index for step in precip_steps):
                        raise RuntimeError(f"fallback missing {zone} precipitation lead {lead}")
                    precipitation = float(indexed.loc[precip_steps, "apcp6_mm"].clip(lower=0).sum())
                else:
                    if lead + 24 not in indexed.index:
                        raise RuntimeError(f"fallback missing {zone} cumulative precipitation lead {lead}")
                    precipitation = max(0.0, float(indexed.loc[lead + 24, "tp_cum_m"] - indexed.loc[lead, "tp_cum_m"]) * 1000.0)
                valid_time = cycle_time + timedelta(hours=lead)
            common = {
                "model": model, "region": zone, "valid_time": valid_time.replace(tzinfo=None),
                "lead_hours": lead, "run_time": cycle_time.replace(tzinfo=None),
                "model_generation": f"live:{generation}", "season": _season(valid_time.month),
                "regime": "normal", "regime_probs": None,
                "lat": float(np.mean([point[0] for point in REPRESENTATIVE_POINTS[zone]])),
                "lon": float(np.mean([point[1] for point in REPRESENTATIVE_POINTS[zone]])),
                "retrieved_at_utc": retrieved_at.isoformat(), "source_provider": provider,
                "source_model": source_model, "source_transport": transport,
                "source_run_time": cycle_time.isoformat(), "run_time_basis": "model_run_time",
            }
            rows.extend([
                {**common, "variable": "temperature", "forecast_value": temperature_value},
                {**common, "variable": "precipitation", "forecast_value": precipitation},
                {**common, "variable": "wind_speed", "forecast_value": wind_value},
            ])
    return rows


def _season(month: int) -> str:
    if month in (3, 4, 5):
        return "pre_monsoon"
    if month in (6, 7, 8, 9):
        return "sw_monsoon"
    if month in (10, 11):
        return "post_monsoon"
    return "winter"


def _interpolate_step(indexed: pd.DataFrame, field: str, target_hour: float) -> float:
    lower_step = int(target_hour // 6) * 6
    upper_step = lower_step if target_hour == lower_step else lower_step + 6
    if lower_step not in indexed.index or upper_step not in indexed.index:
        raise RuntimeError(f"ECMWF cycle does not cover source offset {target_hour:g}h")
    lower_value = float(indexed.loc[lower_step, field])
    if upper_step == lower_step:
        return lower_value
    upper_value = float(indexed.loc[upper_step, field])
    fraction = (target_hour - lower_step) / (upper_step - lower_step)
    return lower_value + fraction * (upper_value - lower_value)

def validate_rows(model: str, rows: list[dict], now: datetime) -> dict:
    frame = pd.DataFrame(rows)
    expected = {(zone, variable, lead) for zone in ZONES for variable in ("temperature", "precipitation", "wind_speed") for lead in LEADS}
    observed = set(zip(frame["region"], frame["variable"], frame["lead_hours"])) if not frame.empty else set()
    finite = bool(not frame.empty and np.isfinite(frame["forecast_value"].to_numpy(dtype=float)).all())
    future = bool(not frame.empty and (pd.to_datetime(frame["valid_time"]) > now.replace(tzinfo=None)).all())
    complete = observed == expected and len(frame) == len(expected)
    duplicate = int(frame.duplicated(["model", "region", "variable", "lead_hours", "valid_time"]).sum()) if not frame.empty else 0
    if not complete or not finite or not future or duplicate:
        raise RuntimeError(f"{model} invalid live cycle: complete={complete} finite={finite} future={future} duplicates={duplicate}")
    return {"rows": len(frame), "regions": len(set(frame["region"])), "variables": len(set(frame["variable"])), "leads": len(set(frame["lead_hours"])), "finite": finite, "complete": complete, "future": future}


def fetch_provider(model: str, now: datetime) -> tuple[list[dict], dict]:
    started = time.monotonic()
    fallback_used = False
    error = None
    for attempt, delay in enumerate((0, 2, 5, 10, 20)):
        if delay:
            time.sleep(delay)
        try:
            rows = _live_rows(model, now)
            coverage = validate_rows(model, rows, now)
            source_provider = rows[0]["source_provider"]
            state = {"status": "LIVE", "fresh": True, "complete": True, "finite": True, "source": source_provider, "source_model": rows[0]["source_model"], "source_transport": rows[0]["source_transport"], "retrieved_at": now.isoformat(), "source_run_time": None, "run_time_basis": "retrieval_time", "coverage": coverage, "fallback_used": fallback_used, "reason": "complete future real forecast cycle", "latency_seconds": round(time.monotonic() - started, 3)}
            return rows, state
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            logging.warning("provider=%s attempt=primary-%s failure=%s", model, attempt + 1, error)
            if "returned non-finite values" in error:
                break
    try:
        rows = _fallback_rows(model, now)
        coverage = validate_rows(model, rows, now)
        source_run_time = rows[0].get("source_run_time")
        source_age = (
            now - datetime.fromisoformat(source_run_time).astimezone(timezone.utc)
            if source_run_time else timedelta(0)
        )
        max_source_age = timedelta(hours={"GFS": 10, "IFS": 14, "AIFS": 21}[model])
        fresh = timedelta(0) <= source_age <= max_source_age
        state = {"status": "LIVE" if fresh else "STALE", "fresh": fresh, "complete": True, "finite": True, "source": rows[0]["source_provider"], "source_model": rows[0]["source_model"], "model_generation": rows[0].get("model_generation"), "source_transport": rows[0]["source_transport"], "retrieved_at": now.isoformat(), "source_run_time": source_run_time, "run_time_basis": rows[0].get("run_time_basis", "retrieval_time"), "coverage": coverage, "fallback_used": True, "reason": "complete fresh real source cycle" if fresh else "complete real source cycle exceeds its model-cycle freshness window", "latency_seconds": round(time.monotonic() - started, 3)}
        return rows, state
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        logging.warning("provider=%s attempt=fallback failure=%s", model, error)
    state = {"status": "UNAVAILABLE", "fresh": False, "complete": False, "finite": False, "source": None, "source_model": None, "source_transport": None, "retrieved_at": now.isoformat(), "source_run_time": None, "run_time_basis": "retrieval_time", "coverage": {}, "fallback_used": True, "reason": error or "provider failed", "latency_seconds": round(time.monotonic() - started, 3)}
    return [], state


def persist_cycle(provider_rows: dict[str, list[dict]], states: dict[str, dict], now: datetime) -> None:
    from app.config import LIVE_DATA_DIR
    sys.path.insert(0, str(ROOT / "backend"))
    from app.database import SessionLocal
    from app.models_db import ForecastRow, LiveForecast
    from app.config import ACTIVE_MODEL_VERSION
    for model, rows in provider_rows.items():
        if rows:
            _atomic_write_parquet(pd.DataFrame(rows), LIVE_DIR / f"{model.lower()}_latest.parquet")
    manifest = {"last_refresh": now.isoformat(), "model_version": ACTIVE_MODEL_VERSION or MODEL_VERSION, "providers": states, "blend_readiness": sum(state["status"] == "LIVE" for state in states.values()) >= 2, "frontend_readiness": True}
    temporary = LIVE_DIR / "live_manifest.json.tmp"
    temporary.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    temporary.replace(LIVE_DIR / "live_manifest.json")
    db = SessionLocal()
    db.query(ForecastRow).filter(ForecastRow.model_generation.like("live:%")).delete(synchronize_session=False)
    db.query(LiveForecast).delete(synchronize_session=False)
    for rows in provider_rows.values():
        for row in rows:
            db.add(ForecastRow(model=row["model"], model_generation=row["model_generation"], region=row["region"], run_time=row["run_time"], valid_time=row["valid_time"], lead_hours=row["lead_hours"], variable=row["variable"], lat=row["lat"], lon=row["lon"], forecast_value=row["forecast_value"], season=row["season"], regime=row["regime"], regime_probs=row["regime_probs"]))
    db.add(LiveForecast(region="__cycle__", variable="__cycle__", valid_time=now.replace(tzinfo=None), lead_hours=0, run_time=now.replace(tzinfo=None), ingestion_time=now.replace(tzinfo=None), model_version=ACTIVE_MODEL_VERSION or MODEL_VERSION, source_runs=states, payload=manifest))
    db.commit()
    db.close()


def _previous_live_provider(model: str, now: datetime) -> tuple[list[dict], dict | None]:
    """Keep the last validated source cycle available while a replacement downloads."""
    sys.path.insert(0, str(ROOT / "backend"))
    from app.config import ACTIVE_MODEL_VERSION, LIVE_FRESHNESS_MINUTES
    from app.database import SessionLocal
    from app.models_db import ForecastRow, LiveForecast

    db = SessionLocal()
    try:
        # Tie-break on id: refresh_once persists the cycle twice with the same
        # ingestion_time (a partial marker first, then the complete one), so
        # ordering on ingestion_time alone can return the incomplete row.
        latest = (
            db.query(LiveForecast)
            .order_by(LiveForecast.ingestion_time.desc(), LiveForecast.id.desc())
            .first()
        )
        if not latest or latest.model_version != ACTIVE_MODEL_VERSION:
            return [], None
        ingestion_time = latest.ingestion_time
        if ingestion_time.tzinfo is None:
            ingestion_time = ingestion_time.replace(tzinfo=timezone.utc)
        ingestion_age = now - ingestion_time.astimezone(timezone.utc)
        if ingestion_age < timedelta(0) or ingestion_age > timedelta(minutes=LIVE_FRESHNESS_MINUTES):
            return [], None

        states = (latest.payload or {}).get("providers", {})
        state = states.get(model)
        if not isinstance(state, dict) or state.get("status") != "LIVE":
            return [], None
        if model == "AIFS" and (
            state.get("source") != "ECMWF Open Data"
            or not str(state.get("source_transport", "")).startswith("ECMWF Open Data (")
        ):
            return [], None

        rows = db.query(ForecastRow).filter(
            ForecastRow.model == model,
            ForecastRow.model_generation.like("live:%"),
        ).all()
        if len(rows) != len(ZONES) * len(LEADS) * 3:
            return [], None
        snapshots = [{
            key: getattr(row, key)
            for key in (
                "model", "model_generation", "region", "run_time", "valid_time",
                "lead_hours", "variable", "lat", "lon", "forecast_value", "season",
                "regime", "regime_probs",
            )
        } for row in rows]
        return snapshots, dict(state)
    finally:
        db.close()


def refresh_once() -> dict:
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    logging.info("live refresh starting (cycle minute %s)", now.isoformat())
    provider_rows, states = {}, {}
    previous_aifs_rows, previous_aifs_state = _previous_live_provider("AIFS", now)
    for model in (model for model in MODELS if model != "AIFS"):
        rows, state = fetch_provider(model, now)
        provider_rows[model] = rows
        states[model] = state
        logging.info("provider=%s source=%s success=%s fallback=%s rows=%s valid_min=%s valid_max=%s freshness=%s", model, state.get("source"), state["status"] == "LIVE", state["fallback_used"], state.get("coverage", {}).get("rows", 0), rows[0]["valid_time"].isoformat() if rows else None, rows[-1]["valid_time"].isoformat() if rows else None, state["fresh"])
    if previous_aifs_state:
        current_valid_times = {
            (row["region"], row["variable"], row["lead_hours"]): row["valid_time"]
            for rows in provider_rows.values()
            for row in rows
        }
        previous_aifs_rows = [
            {
                **row,
                "valid_time": current_valid_times.get(
                    (row["region"], row["variable"], row["lead_hours"]), row["valid_time"]
                ),
            }
            for row in previous_aifs_rows
        ]
        provider_rows["AIFS"] = previous_aifs_rows
        states["AIFS"] = {
            **previous_aifs_state,
            "reason": "retaining last validated AIFS cycle while the next cycle is fetched",
            "valid_time_alignment": "current region/variable/lead keys",
        }
    else:
        states["AIFS"] = {
            "status": "UNAVAILABLE",
            "fresh": False,
            "complete": False,
            "finite": False,
            "source": None,
            "source_model": None,
            "source_transport": None,
            "retrieved_at": now.isoformat(),
            "source_run_time": None,
            "run_time_basis": "retrieval_time",
            "coverage": {},
            "fallback_used": False,
            "reason": "AIFS provider refresh is in progress; no previous validated cycle is cached.",
        }
    persist_cycle(provider_rows, states, now)

    rows, state = fetch_provider("AIFS", now)
    if state.get("status") != "LIVE" and previous_aifs_state:
        refresh_error = state.get("reason", "new AIFS cycle was not usable")
        state = {
            **previous_aifs_state,
            "reason": f"new AIFS cycle failed validation; retaining last validated cycle ({refresh_error})",
            "last_refresh_error": refresh_error,
        }
        rows = previous_aifs_rows
    provider_rows["AIFS"] = rows
    states["AIFS"] = state
    logging.info("provider=AIFS source=%s success=%s fallback=%s rows=%s valid_min=%s valid_max=%s freshness=%s", state.get("source"), state["status"] == "LIVE", state["fallback_used"], state.get("coverage", {}).get("rows", 0), rows[0]["valid_time"].isoformat() if rows else None, rows[-1]["valid_time"].isoformat() if rows else None, state["fresh"])
    persist_cycle(provider_rows, states, now)
    logging.info(
        "live refresh complete: live_providers=%s",
        [model for model, state in states.items() if state.get("status") == "LIVE"],
    )
    return states


def refresh_live_data() -> dict:
    """Reusable entry point for the live refresh.

    Both the CLI (``main``) and the API's on-demand refresh
    (backend/app/on_demand_refresh.py, via app.live_scheduler) call this single
    implementation — the fetch logic is never duplicated.
    """
    return refresh_once()


def main() -> None:
    """Refresh loop.

    EXIT CONTRACT (important for cron and the in-process scheduler):
      * exit 0 only when the cycle completed AND at least one provider is
        LIVE — a partial cycle with a recorded per-provider status is fine;
      * exit 2 when the cycle raised, and exit 3 when the cycle completed but
        no provider reached LIVE.

    Previously every failure was swallowed and the process still exited 0, so
    a permanently broken refresh looked healthy forever — the same silent
    failure class that kept AIFS out of LIVE mode.
    """
    interval = int(os.getenv("SYNOPTIQ_LIVE_REFRESH_MINUTES", "15"))
    once = "--once" in sys.argv
    while True:
        exit_code = 0
        try:
            states = refresh_once()
            print(json.dumps(states, indent=2), flush=True)
            live = sum(1 for st in states.values() if st.get("status") == "LIVE")
            if live == 0:
                logging.error("refresh cycle completed with no LIVE provider: %s", states)
                print("live refresh cycle completed but NO provider is LIVE", flush=True)
                exit_code = 3
        except Exception as exc:
            logging.exception("refresh cycle failed: %s", exc)
            print(f"live refresh cycle failed: {exc}", flush=True)
            exit_code = 2
        if once:
            sys.exit(exit_code)
        time.sleep(max(60, interval * 60))


if __name__ == "__main__":
    main()
