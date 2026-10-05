"""
SQLite tables. In production the blueprint calls for Zarr/Parquet for arrays
+ PostgreSQL/PostGIS for metadata (Section 5, Section 16). For a student
prototype we fold both into SQLite rows keyed on the same canonical schema
so it stays a single free, zero-ops file.
"""
from sqlalchemy import Column, String, Float, Integer, DateTime, JSON
from app.database import Base


class ForecastRow(Base):
    __tablename__ = "forecasts"
    id = Column(Integer, primary_key=True, autoincrement=True)
    model = Column(String, index=True)
    model_generation = Column(String, nullable=True)
    region = Column(String, index=True)
    run_time = Column(DateTime, index=True)
    valid_time = Column(DateTime, index=True)
    lead_hours = Column(Integer, index=True)
    variable = Column(String, index=True)
    lat = Column(Float)
    lon = Column(Float)
    forecast_value = Column(Float)
    season = Column(String, index=True)
    regime = Column(String, index=True)
    regime_probs = Column(JSON, nullable=True)

    # --- added for leakage fix ---
    split = Column(String, index=True, nullable=True)  # "train" | "val" | "test", by valid_time
    # Expanding (leakage-free) per-row features: computed once, vectorized,
    # over the whole archive using groupby().expanding().shift(1), so every
    # row only ever "sees" strictly earlier valid_times, train/val/test alike.
    # See app/data_prep.py::compute_expanding_features for the exact method.
    historical_skill_expanding = Column(Float, nullable=True)
    climatological_anomaly_norm = Column(Float, nullable=True)


class GroundTruthRow(Base):
    __tablename__ = "ground_truth"
    id = Column(Integer, primary_key=True, autoincrement=True)
    region = Column(String, index=True)
    valid_time = Column(DateTime, index=True)
    variable = Column(String, index=True)
    lat = Column(Float)
    lon = Column(Float)
    observed_value = Column(Float)
    source = Column(String, nullable=False)


class SkillRow(Base):
    __tablename__ = "skill_table"
    id = Column(Integer, primary_key=True, autoincrement=True)
    model = Column(String, index=True)
    region = Column(String, index=True)
    lead_hours = Column(Integer, index=True)
    season = Column(String, index=True)
    variable = Column(String, index=True)
    rmse = Column(Float)
    mae = Column(Float)
    bias = Column(Float)
    csi_50mm = Column(Float, nullable=True)
    pod_50mm = Column(Float, nullable=True)
    far_50mm = Column(Float, nullable=True)
    fss_50mm = Column(Float, nullable=True)  # FSS-PROXY (see skill_engine.py docstring) — not true gridded FSS
    n_samples = Column(Integer)
    # Frozen boundary: this table is built ONLY from rows with
    # valid_time < built_through (train+validation, never test). Kept on the
    # row itself so it's auditable from the DB alone, not just from the
    # script that happened to run.
    built_through = Column(DateTime, nullable=True)


class ReplayEvent(Base):
    __tablename__ = "replay_events"
    event_id = Column(String, primary_key=True)
    region = Column(String, index=True)
    variable = Column(String)
    valid_time = Column(DateTime)
    label = Column(String)
    severity = Column(String)
    payload = Column(JSON)  # full precomputed BlendedForecastResponse-shaped dict


class LiveForecast(Base):
    __tablename__ = "live_forecasts"
    id = Column(Integer, primary_key=True, autoincrement=True)
    region = Column(String, index=True)
    variable = Column(String, index=True)
    valid_time = Column(DateTime, index=True)
    lead_hours = Column(Integer, index=True)
    run_time = Column(DateTime, index=True)
    ingestion_time = Column(DateTime, index=True)
    model_version = Column(String, nullable=False)
    source_runs = Column(JSON, nullable=False)
    payload = Column(JSON, nullable=False)
