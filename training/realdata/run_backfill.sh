#!/usr/bin/env bash
# Synoptiq real-data backfill: run this the evening before you want to train.
#
#   chmod +x run_backfill.sh
#   ./run_backfill.sh 2026-07-01 2026-09-22
#
# What it does (in order):
#   1. GFS 0.25 deg, 00Z cycles, steps 6..144 by 6  (~2.5 MB/step-file subset)
#   2. ECMWF IFS + AIFS, same window (~3 MB/step-file subset)
#   3. IMERG daily Late (needs Earthdata login) + ERA5 via Open-Meteo
#   4. Build the training table
#   5. Load into the Synoptiq SQLite DB (wipes the synthetic archive)
#
# Everything is resumable: re-running skips files that already exist.
# Expected totals for a 90-day window: ~8-10 GB transient downloads
# (GRIB subsets are deleted after decoding), a few minutes per model-week.
set -e
START=${1:-2026-07-01}
END=${2:-$(date -u +%F)}
HOUR=${3:-0}

echo "== 1/4 GFS backfill $START .. $END (${HOUR}Z) =="
python fetch_gfs.py   --start "$START" --end "$END" --hour "$HOUR"

echo "== 2/4 ECMWF IFS + AIFS backfill =="
python fetch_ecmwf.py --start "$START" --end "$END" --hour "$HOUR"

echo "== 3/4 Verification truth =="
# months for ERA5: derive from START..END
M=$(python - "$START" "$END" <<'PY'
import sys
from datetime import datetime, timedelta
s=datetime.strptime(sys.argv[1],"%Y-%m-%d"); e=datetime.strptime(sys.argv[2],"%Y-%m-%d")
ms=[]
while s<=e:
    if s.strftime("%Y-%m") not in ms: ms.append(s.strftime("%Y-%m"))
    s=(s.replace(day=1)+timedelta(days=32)).replace(day=1)
print(",".join(ms))
PY
)
python fetch_imerg.py --start "$START" --end "$END"
python fetch_era5.py  --months "$M"

echo "== 4/4 Build training table + load DB =="
python build_training_table.py
python load_into_db.py

echo "ALL DONE. Next: cd backend && python scripts/train_model.py"
