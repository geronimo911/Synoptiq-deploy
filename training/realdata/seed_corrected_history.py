"""Seed corrected REAL_12M history and frozen skill into the serving database."""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table, select, text

from app.config import ACTIVE_MODEL_VERSION, ARTIFACTS_DIR, DATABASE_ROLE, RUNTIME_MODE
from app.database import Base, SessionLocal, engine
from app.models_db import ForecastRow, GroundTruthRow, ReplayEvent, SkillRow, LiveForecast

SEED_DIR = ARTIFACTS_DIR / "seed"
SEED_ARCHIVE = Path(os.getenv(
    "SYNOPTIQ_HISTORY_SEED_ARCHIVE",
    str(SEED_DIR / "training_aifs_corrected.zip"),
)).resolve()
UNIT_REPORT = SEED_DIR / "unit-correction.json"

seed_metadata = MetaData()
seed_table = Table(
    "synoptiq_history_seed",
    seed_metadata,
    Column("model_version", String(160), primary_key=True),
    Column("seeded_at", DateTime, nullable=False),
    Column("forecast_rows", Integer, nullable=False),
    Column("truth_rows", Integer, nullable=False),
)


def _parse_datetime(value):
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)


def _source_rows(connection, table: str, columns: list[str], query: str | None = None):
    selected = ", ".join(columns)
    cursor = connection.execute(query or f"SELECT {selected} FROM {table}")
    names = [item[0] for item in cursor.description]
    json_columns = {"regime_probs", "payload", "source_runs"}
    date_columns = {"run_time", "valid_time", "built_through", "ingestion_time"}
    while batch := cursor.fetchmany(2000):
        records = []
        for values in batch:
            record = dict(zip(names, values))
            for name in json_columns.intersection(record):
                value = record[name]
                if isinstance(value, str):
                    record[name] = json.loads(value) if value else None
            for name in date_columns.intersection(record):
                record[name] = _parse_datetime(record[name])
            records.append(record)
        yield records


def _validate_source(source_db: Path, expected_version: str) -> tuple[int, int]:
    manifest_path = ARTIFACTS_DIR / "manifests" / expected_version / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    correction = json.loads(UNIT_REPORT.read_text(encoding="utf-8"))
    if manifest.get("data_mode") != "real" or manifest.get("synthetic_data_used") is not False:
        raise RuntimeError("Corrected history seed requires a validated non-synthetic REAL manifest.")
    if manifest.get("model_version") != expected_version:
        raise RuntimeError("History seed manifest does not match the active model version.")
    if correction.get("correction_factor") != 0.001 or not correction.get("other_rows_unchanged"):
        raise RuntimeError("AIFS precipitation unit-correction evidence is missing or invalid.")

    connection = sqlite3.connect(f"file:{source_db.as_posix()}?mode=ro", uri=True)
    try:
        forecast_count = connection.execute(
            "SELECT COUNT(*) FROM forecasts WHERE model_generation IS NULL OR model_generation NOT LIKE 'live:%'"
        ).fetchone()[0]
        truth_count = connection.execute("SELECT COUNT(*) FROM ground_truth").fetchone()[0]
        skill_count = connection.execute("SELECT COUNT(*) FROM skill_table").fetchone()[0]
        aifs_max = connection.execute(
            "SELECT MAX(forecast_value) FROM forecasts WHERE model='AIFS' AND variable='precipitation' AND (model_generation IS NULL OR model_generation NOT LIKE 'live:%')"
        ).fetchone()[0]
    finally:
        connection.close()
    if forecast_count != manifest.get("training_rows") or truth_count != manifest.get("truth_rows"):
        raise RuntimeError("Corrected history source row counts do not match its manifest.")
    if skill_count == 0 or aifs_max is None or aifs_max > 200:
        raise RuntimeError("Corrected source has no frozen skill or still contains implausible AIFS precipitation.")
    return forecast_count, truth_count


def _extract_source_database(archive: Path, destination: Path) -> Path:
    if not archive.is_file():
        raise FileNotFoundError(f"Corrected history seed archive not found: {archive}")
    with zipfile.ZipFile(archive) as zipped:
        matches = [name for name in zipped.namelist() if name.endswith("training_aifs_corrected.sqlite3")]
        if len(matches) != 1:
            raise RuntimeError("Seed archive must contain exactly one corrected training SQLite database.")
        extracted = destination / "training_aifs_corrected.sqlite3"
        with zipped.open(matches[0]) as source, extracted.open("wb") as target:
            while block := source.read(1024 * 1024):
                target.write(block)
    return extracted


def _already_seeded() -> bool:
    """Cheaply check the seed ledger without extracting the archive.

    On a Render restart the archive is normally already seeded; reading the
    ledger alone keeps startup fast and avoids re-validating 4.5 MB of zip on
    every boot. The full path below still re-checks under the advisory lock, so
    a concurrent first-time seed is still safe.
    """
    db = SessionLocal()
    try:
        seed_table.create(bind=db.connection(), checkfirst=True)
        row = db.execute(
            select(seed_table.c.model_version).where(
                seed_table.c.model_version == ACTIVE_MODEL_VERSION
            )
        ).scalar_one_or_none()
        return row is not None
    except Exception:
        db.rollback()
        return False
    finally:
        db.close()


def seed_history(force: bool = False) -> dict:
    if RUNTIME_MODE != "real" or DATABASE_ROLE != "PRODUCTION":
        raise RuntimeError("Corrected history seeding is only allowed in real production database mode.")
    if not ACTIVE_MODEL_VERSION:
        raise RuntimeError("SYNOPTIQ_MODEL_VERSION must be configured before history seeding.")

    Base.metadata.create_all(bind=engine)

    # Fast path: on a Render restart the archive is usually already seeded.
    # Detecting that from the ledger alone means we never re-extract or
    # re-validate the seed archive on every boot, and never rebuild the
    # production tables. Existing production data is always preserved unless
    # --force is passed explicitly.
    if not force and _already_seeded():
        return {"status": "already_seeded", "model_version": ACTIVE_MODEL_VERSION}

    with tempfile.TemporaryDirectory(prefix="synoptiq-history-seed-") as temporary:
        source_db = _extract_source_database(SEED_ARCHIVE, Path(temporary))
        forecast_count, truth_count = _validate_source(source_db, ACTIVE_MODEL_VERSION)
        source = sqlite3.connect(f"file:{source_db.as_posix()}?mode=ro", uri=True)
        db = SessionLocal()
        try:
            seed_table.create(bind=db.connection(), checkfirst=True)
            if engine.dialect.name == "postgresql":
                db.execute(text("SELECT pg_advisory_xact_lock(831742609)"))
            already_seeded = db.execute(
                select(seed_table.c.model_version).where(seed_table.c.model_version == ACTIVE_MODEL_VERSION)
            ).scalar_one_or_none()
            if already_seeded and not force:
                db.rollback()
                return {"status": "already_seeded", "model_version": ACTIVE_MODEL_VERSION}

            db.query(ForecastRow).delete(synchronize_session=False)
            db.query(GroundTruthRow).delete(synchronize_session=False)
            db.query(SkillRow).delete(synchronize_session=False)
            db.query(ReplayEvent).delete(synchronize_session=False)
            db.query(LiveForecast).delete(synchronize_session=False)

            forecast_columns = [column.name for column in ForecastRow.__table__.columns if column.name != "id"]
            for batch in _source_rows(
                source,
                "forecasts",
                forecast_columns,
                "SELECT {} FROM forecasts WHERE model_generation IS NULL OR model_generation NOT LIKE 'live:%'".format(
                    ", ".join(forecast_columns)
                ),
            ):
                db.bulk_insert_mappings(ForecastRow, batch)

            truth_columns = [column.name for column in GroundTruthRow.__table__.columns if column.name != "id"]
            for batch in _source_rows(source, "ground_truth", truth_columns):
                db.bulk_insert_mappings(GroundTruthRow, batch)

            skill_columns = [column.name for column in SkillRow.__table__.columns if column.name != "id"]
            for batch in _source_rows(source, "skill_table", skill_columns):
                db.bulk_insert_mappings(SkillRow, batch)

            db.execute(seed_table.delete())
            db.execute(seed_table.insert().values(
                model_version=ACTIVE_MODEL_VERSION,
                seeded_at=datetime.now(timezone.utc).replace(tzinfo=None),
                forecast_rows=forecast_count,
                truth_rows=truth_count,
            ))
            db.commit()
            return {
                "status": "seeded",
                "model_version": ACTIVE_MODEL_VERSION,
                "forecast_rows": forecast_count,
                "truth_rows": truth_count,
            }
        except Exception:
            db.rollback()
            raise
        finally:
            source.close()
            db.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="Replace historical rows even if this version is already seeded.")
    args = parser.parse_args()
    print(json.dumps(seed_history(force=args.force), indent=2), flush=True)


if __name__ == "__main__":
    main()