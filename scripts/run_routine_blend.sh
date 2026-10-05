#!/usr/bin/env bash
# Synoptiq routine operational blend — the automated daily workflow.
#
# One command reproduces the whole forecast-office routine:
#   1. ingest the latest real provider cycle (00Z/06Z/12Z/18Z) if credentials
#      and network are available; otherwise fall back to the archived record
#      and SAY SO (never silently synthetic);
#   2. rebuild the verification + operational artifacts from the record;
#   3. export the standard bulletin (JSON/CSV/TXT + CAP XML alerts);
#   4. append one line to ops/run_log.jsonl.
#
# Usage:
#   scripts/run_routine_blend.sh              # latest archived day
#   scripts/run_routine_blend.sh 2025-05-30   # a specific archived day
#   SKIP_INGEST=1 scripts/run_routine_blend.sh   # no network, record only
#
# Cron (daily 06:20 IST, after the 00Z cycle is fully posted):
#   20 6 * * * cd /path/to/Synoptiq && scripts/run_routine_blend.sh >> ops/cron.log 2>&1
set -euo pipefail
cd "$(dirname "$0")/.."
DAY="${1:-}"
STAMP="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "=== Synoptiq routine blend run @ ${STAMP} ==="

export SYNOPTIQ_MODE="${SYNOPTIQ_MODE:-real}"
export SYNOPTIQ_ARTIFACTS_DIR="${SYNOPTIQ_ARTIFACTS_DIR:-$PWD/artifacts/real_12m_aifs_corrected}"
export DATABASE_URL="${DATABASE_URL:-sqlite:///./data/real/prod.sqlite3}"
BLENDS="${SYNOPTIQ_ARTIFACTS_DIR}/inference/historical_blends.jsonl"
RESEARCH="${SYNOPTIQ_ARTIFACTS_DIR}/research"

step () { echo; echo "--- $1"; }

if [ "${SKIP_INGEST:-0}" != "1" ]; then
  step "1/4 ingest latest real provider cycle"
  if python3 training/realdata/ingest_latest_cycle.py; then
    echo "ingest: OK"
  else
    echo "ingest: UNAVAILABLE (no credentials/network) — continuing on the"
    echo "         archived record; the bulletin states its source explicitly."
  fi
else
  step "1/4 ingest skipped (SKIP_INGEST=1)"
fi

step "2/4 rebuild research + operational artifacts"
python3 backend/scripts/research_suite.py --blends "$BLENDS" --out "$RESEARCH/research_suite.json"
python3 backend/scripts/build_advanced_artifacts.py --blends "$BLENDS" --out-dir "$RESEARCH"
python3 backend/scripts/generate_model_card.py || echo "model card: skipped"

step "3/4 export bulletin + CAP alerts"
if [ -n "$DAY" ]; then
  python3 backend/scripts/export_bulletin.py --date "$DAY"
else
  python3 backend/scripts/export_bulletin.py
fi

step "4/4 done"
echo "run log: ops/run_log.jsonl"
tail -1 ops/run_log.jsonl 2>/dev/null || true
echo "=== routine blend complete ==="
