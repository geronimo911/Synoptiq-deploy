#!/usr/bin/env bash
# One-shot setup: seed synthetic archive -> train meta-model -> evaluate on
# held-out data -> precompute the offline replay cache. Run this once before
# starting the API (uvicorn app.main:app).
set -e
cd "$(dirname "$0")/.."
export SYNOPTIQ_MODE=fake
echo "== 1/4 Generating synthetic forecast archive =="
python scripts/generate_synthetic_data.py
echo "== 2/4 Training adaptive blending meta-model =="
python scripts/train_model.py
echo "== 3/4 Held-out evaluation (baseline vs Synoptiq CSI@50mm) =="
python scripts/evaluate_blend.py
echo "== 4/4 Precomputing offline Historical Replay cache =="
python scripts/precompute_replay_cache.py
echo "Bootstrap complete. Start the API with:"
echo "  uvicorn app.main:app --reload --port 8000"
