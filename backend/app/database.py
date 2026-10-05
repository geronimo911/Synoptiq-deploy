from pathlib import Path
from sqlalchemy.engine import make_url
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker, declarative_base
from app.config import DATA_DIR, DATABASE_ROLE, DATABASE_URL, DB_PATH, RUNTIME_MODE, TRAINING_DATABASE_URL

selected_url = "" if RUNTIME_MODE == "fake" else (
    TRAINING_DATABASE_URL if DATABASE_ROLE == "TRAINING" else DATABASE_URL
)
if DATABASE_ROLE == "TRAINING" and TRAINING_DATABASE_URL:
    selected_url = TRAINING_DATABASE_URL
if selected_url and RUNTIME_MODE == "real":
    database_url = make_url(selected_url)
    if database_url.get_backend_name() == "sqlite" and database_url.database not in (None, ":memory:"):
        database_path = Path(database_url.database)
        if not database_path.is_absolute():
            database_path = (Path.cwd() / database_path).resolve()
        real_data_dir = DATA_DIR.resolve()
        if database_path != real_data_dir and real_data_dir not in database_path.parents:
            raise RuntimeError("Real mode SQLite databases must be located under data/real")
if selected_url:
    engine = create_engine(selected_url, pool_pre_ping=True)
elif RUNTIME_MODE == "real" or DATABASE_ROLE == "TRAINING":
    raise RuntimeError("DATABASE_URL is required in real mode")
else:
    engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


def _ensure_forecast_generation_column() -> None:
    """Apply the single additive metadata migration without touching rows."""
    inspector = inspect(engine)
    if not inspector.has_table("forecasts"):
        return
    columns = {column["name"] for column in inspector.get_columns("forecasts")}
    if "model_generation" not in columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE forecasts ADD COLUMN model_generation VARCHAR"))


_ensure_forecast_generation_column()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
