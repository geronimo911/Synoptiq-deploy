#!/usr/bin/env bash
# Package the complete Synoptiq submission workspace.
#
# WHY THIS SCRIPT EXISTS
# A previous archive contained only root-level files and artifacts and was
# missing the actual source tree, which makes the submission unusable. This
# script therefore does three things the old one did not:
#
#   1. it enumerates and VERIFIES every required source directory before
#      zipping, and refuses to produce an archive if any is missing or empty;
#   2. it prints the file counts per directory so a truncated zip is visible
#      immediately rather than discovered by a judge;
#   3. after writing the archive it re-opens the zip and counts the source
#      entries inside it, failing if the counts disagree with the workspace.
#
# It packages raw source only. It does NOT run npm install, npm build or any
# other build step, and it excludes node_modules/, __pycache__/ and .git/.
#
# Usage:
#   ./package_submission.sh                 # writes dist/synoptiq_submission_<timestamp>.zip
#   ./package_submission.sh my_name.zip     # explicit output path
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="${1:-dist/synoptiq_submission_${STAMP}.zip}"
mkdir -p "$(dirname "$OUT")"

# ---------------------------------------------------------------------------
# 1. required source directories (backend layout + frontend)
# ---------------------------------------------------------------------------
REQUIRED=(
  "backend/app"          # API, pipeline, blending, models
  "backend/app/blending" # the blending maths itself
  "backend/scripts"      # training / evaluation / artifact builders
  "backend/tests"        # the test suite
  "training"             # data pipeline (fetchers, ingest, live refresh)
  "frontend"             # the dashboard source
  "artifacts"            # trained artifacts, research + operational outputs
  "models"               # trained model files
)

echo "=== Synoptiq submission packaging ==="
echo "workspace: $ROOT"
echo
fail=0
for dir in "${REQUIRED[@]}"; do
  if [ ! -d "$dir" ]; then
    echo "  MISSING DIR : $dir"
    fail=1
    continue
  fi
  count="$(find "$dir" -type f \
            -not -path "*/node_modules/*" -not -path "*/__pycache__/*" \
            -not -path "*/.git/*" | wc -l | tr -d ' ')"
  if [ "$count" -eq 0 ]; then
    echo "  EMPTY DIR   : $dir"
    fail=1
  else
    printf "  ok  %-22s %5s files\n" "$dir" "$count"
  fi
done

# frontend source must specifically be present, not just the frontend folder
for must in "frontend/src" "frontend/package.json"; do
  if [ ! -e "$must" ]; then
    echo "  MISSING     : $must"
    fail=1
  fi
done

# a couple of load-bearing modules, named explicitly so a layout change is loud
for must in "backend/app/api_v1.py" "backend/app/blending/peaks.py" \
            "backend/app/impact.py" "backend/app/regimes.py" \
            "backend/scripts/research_suite.py"; do
  if [ ! -f "$must" ]; then
    echo "  MISSING     : $must"
    fail=1
  fi
done

if [ "$fail" -ne 0 ]; then
  echo
  echo "REFUSING to build the archive: required source is missing (see above)."
  echo "Fix the workspace layout, then re-run. An archive without source is worse"
  echo "than no archive."
  exit 1
fi

# ---------------------------------------------------------------------------
# 2. build the archive (raw source, no build step)
# ---------------------------------------------------------------------------
echo
echo "--- zipping (excluding node_modules, __pycache__, .git, .env, caches) ---"
rm -f "$OUT"
zip -r -q "$OUT" . \
  -x "*/node_modules/*" \
  -x "node_modules/*" \
  -x "*/__pycache__/*" \
  -x "__pycache__/*" \
  -x "*/.git/*" \
  -x ".git/*" \
  -x "*/.pytest_cache/*" \
  -x "*/.mypy_cache/*" \
  -x "*/.ruff_cache/*" \
  -x "*.pyc" \
  -x "*/.env" \
  -x ".env" \
  -x "dist/*" \
  -x "*/dist/*" \
  -x "*/.DS_Store"

# ---------------------------------------------------------------------------
# 3. verify the archive actually contains the source
# ---------------------------------------------------------------------------
echo
echo "--- verifying archive contents ---"
verify_dir() {
  local dir="$1"
  local in_zip
  in_zip="$(unzip -Z1 "$OUT" | grep -cE "^\.?/?${dir}/" || true)"
  local on_disk
  on_disk="$(find "$dir" -type f \
              -not -path "*/node_modules/*" -not -path "*/__pycache__/*" \
              -not -path "*/.git/*" | wc -l | tr -d ' ')"
  if [ "$in_zip" -lt "$on_disk" ]; then
    echo "  FAIL $dir: $in_zip entries in zip < $on_disk files on disk"
    return 1
  fi
  printf "  ok  %-22s %5s entries in zip\n" "$dir" "$in_zip"
  return 0
}

verify_fail=0
for dir in "${REQUIRED[@]}"; do
  verify_dir "$dir" || verify_fail=1
done
if [ "$verify_fail" -ne 0 ]; then
  echo
  echo "ARCHIVE VERIFICATION FAILED — the zip is incomplete; do not submit it."
  exit 1
fi

size="$(du -h "$OUT" | cut -f1)"
entries="$(unzip -Z1 "$OUT" | wc -l | tr -d ' ')"
echo
echo "=== done ==="
echo "archive : $OUT"
echo "size    : $size"
echo "entries : $entries"
echo
echo "Sanity check any time with:"
echo "  unzip -Z1 '$OUT' | grep -c 'backend/app/blending/'"
echo "  unzip -Z1 '$OUT' | grep -c 'frontend/src/'"
