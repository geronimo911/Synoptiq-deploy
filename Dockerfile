FROM python:3.12-slim
WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*
COPY backend/requirements.txt ./backend/requirements.txt
COPY training/requirements-realdata.txt ./training/requirements-realdata.txt
# the API process hosts the live-refresh scheduler and the worker itself lives
# in training/, so the training-side dependencies are needed in the image too
RUN pip install --no-cache-dir -r backend/requirements.txt -r training/requirements-realdata.txt
COPY backend ./backend
# the live-refresh worker and the routine workflow live outside backend/:
# without these the automatic refresh cannot run inside the container
COPY training ./training
COPY scripts ./scripts
COPY ops ./ops
COPY models ./models
COPY artifacts ./artifacts
# NOTE: no data/ files are copied. data/ is git-ignored and not present in the
# repository; the application creates its directories at runtime, and the
# serving database comes from DATABASE_URL (Render PostgreSQL in production).
ENV SYNOPTIQ_MODE=real
# Render overrides this to 0 and refreshes on demand. The default keeps the
# in-process scheduler enabled for standalone self-hosting.
ENV SYNOPTIQ_AUTO_REFRESH=1
ENV SYNOPTIQ_LIVE_REFRESH_MINUTES=15
# Seed the corrected history into the serving database, then serve. The seed
# step is idempotent: it returns "already_seeded" without rebuilding tables
# when the active model version is already present.
CMD ["sh", "-c", "python training/realdata/seed_corrected_history.py && uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port ${PORT:-8000}"]
