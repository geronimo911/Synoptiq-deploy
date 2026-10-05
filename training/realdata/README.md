# Synoptiq — Real-Data Ingestion & Training Package

Replaces the synthetic data generator in the Synoptiq prototype with **real
GFS, IFS and AIFS forecasts and real verification data (IMERG rainfall, ERA5
temperature/wind)**, feeding the *unchanged* training / evaluation pipeline.

## TL;DR

```bash
pip install xarray cfgrib eccodes pyarrow pandas netcdf4 earthaccess

# one-time account:
#   NASA Earthdata (for IMERG):  https://urs.earthdata.nasa.gov/  -> ~/.netrc
#   ERA5 is fetched from Open-Meteo Historical Weather API (no CDS account).
#   GFS + IFS + AIFS: public forecast data; ECMWF Open Data can use the
#   official Azure, Google Cloud, ECMWF, or AWS mirrors (no MARS account needed).

D:/SIH2026/.venv/Scripts/python.exe training/run_real_training.py --end 2026-09-20 --ecmwf-source aws
```

## What this package answers (the questions you had)

**Are we building GFS/IFS/AIFS?** No. Those are *inputs* fetched over HTTPS.
You build the **blending meta-layer on top**: per-source skill-score models
(LightGBM), bust classifiers, and calibration artifacts. The pipeline in
`backend/` already has this structure; this package just feeds it real rows.

**How many models do we train?** Exactly what `backend/scripts/train_model.py`
already trains: 9 skill-score regressors (3 variables × 3 sources), 3 bust
classifiers + 3 bust-threshold classifiers, and the quantile-mapping /
isotonic calibration artifacts. Nothing new to invent.

**How much data?** The common history begins 2025-02-25. The training cycle is
00Z to match the daily verification windows. AIFS Single 00Z is absent on the
first date; no row is fabricated, and complete common contexts begin when all
three forecast sources are present. AIFS Single v1 is tagged before
2026-05-12; v2 is tagged from that day onward.
Historical standard requests use official ECMWF Open Data mirrors; MARS is
not required. AIFS Single data begin on 2025-02-25 for the common
training window. AIFS Single v2 became operational on 2026-05-12; each
forecast row records its generation. Requested windows must be supported by
all providers; unavailable dates are never silently replaced.

## Files

| file | what it does | tested? |
|---|---|---|
| `common.py` | zones, HTTP-with-retry, cropping, units | yes (live) |
| `fetch_gfs.py` | GFS 0.25° via AWS S3 `.idx` byte-range subsetting | yes (live, end-to-end incl. cfgrib decode) |
| `fetch_ecmwf.py` | IFS + AIFS via `ecmwf.opendata.Client` | public ECMWF, Azure, Google, and AWS mirrors; AIFS v1/v2 generation tagged per row |
| `live_refresh.py` | **production live worker**: refreshes GFS, IFS, and AIFS; validates complete future cycles; writes forecast rows and provider provenance | runs every 15 minutes locally and as the Render Cron Job |
| `fetch_imerg.py` | IMERG Daily Late (truth rainfall) via GES DISC | needs your Earthdata login |
| `fetch_era5.py` | ERA5 temperature/wind truth via Open-Meteo Historical Weather API | public API; ERA5 identity and provenance recorded |
| `build_training_table.py` | harmonize everything → ForecastRow/GroundTruthRow rows | logic reviewed; run after fetchers |
| `load_into_db.py` | wipe synthetic archive, insert real rows, re-run split+expanding post-pass | mirrors `generate_synthetic_data.py` |
| `ingest_latest_cycle.py` | legacy one-shot ingestion path; requires Open-Meteo to return all three models | do not use for production scheduling; use `live_refresh.py` |
| `run_backfill.sh` | orchestrates all of the above, resumable | — |

## Conventions (memorize these — judges ask)

- **Verification day D** = `[D 00:00, D+1 00:00) UTC`; `valid_time = D 00:00 UTC`.
- **Lead k** = 24·k hours, k = 1..5 (day-ahead … day-5).
- **precipitation** = 24 h total (mm). **temperature** = daily-mean 2 m (°C).
  **wind_speed** = daily-max 10 m wind (km/h). ERA5 uses the maximum of the
  00/06/12/18 UTC samples, then averages the existing representative points.
- GFS APCP is per-bucket kg/m² (== mm). ECMWF `tp` is cumulative since run
  start; GRIB may label it `kg m**-2` (numerically mm), so normalize to metres
  before differencing and converting daily totals to mm. Never interpret the
  GRIB numeric value as metres based on the parameter name alone.
- Forecast wind is computed per grid cell `sqrt(u²+v²)` **before** zone averaging.

## Resuming & partial data

- Every fetcher skips already-downloaded days — kill and rerun freely.
- Missing steps/days are skipped; the blend's fallback (skill-weighted average)
  already handles a missing source, by design in `backend/app/blending/blend.py`.
- IMERG V07 Final remains the precipitation truth source; ERA5 precipitation is
  requested and validated but never substituted for IMERG.
