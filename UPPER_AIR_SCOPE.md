# Upper-Air Data Scoping — `fetch_openmeteo.py` Extension

**Status:** scoping only. No code is written by this document.
**Audience:** whoever implements the synoptic regime upgrade.
**Related:** `backend/app/regime_detector.py`, `backend/app/regimes.py`, `training/realdata/fetch_openmeteo.py`.

---

## 1. Why upper-air, and what exactly is missing today

The regime detector is currently driven by **surface proxies**: zone-mean
precipitation, 2 m temperature, 10 m wind speed and the calendar season
(`detect_regime(variable_medians, season, lat)`). Those proxies are honest but
ambiguous. A day with 45 mm of rain over Kerala can be an *active monsoon
trough*, a *depression* passing the coast, or a *pre-monsoon convective burst* —
the surface signature is similar, the synoptic cause is not, and the models
that verify best differ between them.

The canonical discriminator between Indian synoptic regimes is the **mid- and
low-level circulation**:

| Regime | Upper-air signature |
|---|---|
| Active monsoon trough | Strong low-level westerly jet at 850 hPa across the peninsula; trough axis at ~20–22°N; low 500 hPa heights over the monsoon zone |
| Break monsoon | Trough displaced to the Himalayan foothills (~28–30°N); 850 hPa westerlies weak over central India |
| Western disturbance | Mid-latitude trough in the westerlies crossing NW India; negative 500 hPa height anomaly over 30–35°N, 60–75°E |
| Cyclonic depression | Closed 850 hPa circulation with a height minimum over the Bay of Bengal / Arabian Sea; strong low-level vorticity |
| Heat dome / heatwave | Positive 500 hPa height anomaly with subsidence over central India; weak low-level flow |

The variables that carry this information — and that we therefore need — are
**850 hPa zonal and meridional wind** and **500 hPa geopotential height**,
with 850 hPa temperature and 500 hPa vertical velocity as useful secondaries.

---

## 2. Exact Open-Meteo API parameters

`fetch_openmeteo.py` already uses two endpoints and a multi-coordinate
comma-separated request style. The upper-air pull reuses that exact pattern:

* **Live endpoint:** `https://api.open-meteo.com/v1/forecast`
* **Previous-runs endpoint (backfill):** `https://previous-runs-api.open-meteo.com/v1/forecast`

### 2.1 Hourly variables to request

| Purpose | Open-Meteo parameter | Units returned |
|---|---|---|
| 850 hPa wind magnitude | `wind_speed_850hPa` | km/h |
| 850 hPa wind direction | `wind_direction_850hPa` | degrees (meteorological: direction the wind comes **from**) |
| 500 hPa geopotential height | `geopotential_height_500hPa` | m |
| 850 hPa temperature (secondary) | `temperature_850hPa` | °C |
| 500 hPa vertical velocity (secondary) | `vertical_velocity_500hPa` | Pa/s |

**Important:** Open-Meteo exposes pressure-level wind as *speed and direction*,
not as u/v components. The decomposition must be done client-side with the
standard meteorological convention (direction = where the wind blows **from**):

```
u = -speed * sin(direction_radians)
v = -speed * cos(direction_radians)
```

Getting this sign convention wrong is the single most likely implementation
bug; it silently produces a mirrored circulation and a regime classifier that
looks plausible and is wrong. It must have a unit test with a hand-worked
example (e.g. a due-westerly at 20 km/h must give u = +20, v = 0).

### 2.2 Full request parameter set

| Parameter | Value | Note |
|---|---|---|
| `latitude` | comma-separated list | multi-point request, as used today |
| `longitude` | comma-separated list | same order as latitude |
| `hourly` | the variables in §2.1 | one comma-separated list |
| `models` | `gfs_seamless`, `ecmwf_ifs025`, `ecmwf_aifs025_single` | per-model requests; AIFS **must** use the `_single` suffix (§5) |
| `forecast_days` | `7` | matches the current live pull and covers +120 h with margin |
| `timezone` | `GMT` | keeps hour indexing aligned with the pipeline |
| `cell_selection` | leave default (`land`) unless grid fidelity matters, then `nearest` | document the choice; it changes which native cell is sampled |

### 2.3 Domain and sampling geometry

The synoptic domain is **0–40°N, 60–100°E**. Three sampling strategies, in
increasing fidelity and cost:

1. **Representative points (current pattern).** 3–6 points per pilot zone →
   18 points total. Captures the *local* column but cannot resolve a trough
   axis or a height gradient — regime discrimination is weak.
2. **Coarse synoptic grid (recommended first step).** 2.5° spacing over the
   domain → 17 × 17 = **289 points**. This resolves the monsoon trough
   position, the WD height anomaly and depression centres, at a measured cost
   of **1.74 MB and ~1.5 s per model per request** (verified 2026-10-01).
3. **Fine grid (later).** 1.0° spacing → 41 × 41 = **1,681 points**;
   extrapolating the measured 12.6 bytes/value gives ≈ 10 MB and ≈ 5–9 s per
   model, with a query string of ~24,000 characters. This will need chunking
   (see §4.3) and is not recommended for the first iteration.

---

## 3. Payload size increase — measured, not guessed

Measured on 2026-10-01 against `api.open-meteo.com`, `forecast_days=7`,
`timezone=GMT`, three variables:

| Request shape | Payload | Latency |
|---|---|---|
| 18 points × 3 surface vars × 7 d (today's live pull) | **104 KB** | 1.02 s |
| 18 points × 3 upper-air vars × 7 d | **111 KB** | 0.92 s |
| 289 points × 3 upper-air vars × 7 d (2.5° grid) | **1.74 MB** | 1.5 s |

Cost model derived from the measurements: **≈ 12.6 bytes per value**, i.e.
**≈ 2.1 KB per point per variable per 7-day request**.

Implications:

* At representative points, adding upper-air to the existing request costs
  **≈ +7 %** payload — essentially free. This is the cheap first step.
* At the 2.5° grid, one model costs ≈ 1.7 MB; **three models ≈ 5.2 MB per
  refresh cycle**. At the current 15-minute cadence that is ≈ 500 MB/day of
  transfer. That is acceptable for a server but not for a metered mobile
  client, so upper-air must stay server-side and never be proxied raw to the
  browser.
* Response compression: Open-Meteo serves gzip when the client sends
  `Accept-Encoding: gzip`; the JSON above is highly repetitive and compresses
  roughly 4–5×, so wire cost is materially lower than the figures above. The
  existing `urllib` calls do not request gzip — enabling it is a one-line
  change worth doing alongside this work.

---

## 4. Latency and operational implications

### 4.1 Measured latency

Single request, warm connection: **0.9–1.5 s** for all shapes tested (the cost
is dominated by server-side extraction, not transfer). A full refresh
(3 models × 2.5° grid) is therefore ≈ **5 s of wall clock** if run in
parallel, or ≈ 15 s sequentially.

### 4.2 Cadence and rate limits

The live refresh runs every `SYNOPTIQ_LIVE_REFRESH_MINUTES` (default 15) →
96 cycles/day. At three upper-air requests per cycle that is ≈ 288 calls/day
against Open-Meteo's free-tier allowance (≈ 10,000 calls/day, ≈ 600/min).
**No rate-limit problem**, with ample headroom for retries and backfill.

### 4.3 Where latency actually bites

* **Chunking the fine grid.** ~24,000-character query strings risk proxy and
  URL-length failures. Chunk the 1° grid into 4–6 requests of ≤ 400 points.
* **Failure isolation.** Upper-air must be fetched *after* the surface cycle
  is already validated and persisted. If the upper-air call fails, the system
  must keep serving forecasts with surface-derived regimes (see §6) rather
  than failing the refresh. This mirrors the existing `fetch_provider`
  primary/fallback structure.
* **Backfill.** The `previous-runs` endpoint covers only recent runs, so
  historical upper-air for *training* must come from a different source —
  ERA5 pressure levels via CDS (already partially wired: `fetch_era5.py`,
  `ECMWF_API_*` in `.env`) or the provider archives. This is the single
  biggest schedule risk in the whole scope and should be decided before
  implementation starts.

---

## 5. Model-specific availability (verified)

All three models serve the requested pressure-level variables today:

| Model id | 850 hPa wind | 500 hPa height | 850 hPa temp | 500 hPa ω |
|---|---|---|---|---|
| `gfs_seamless` | 48/48 values | 48/48 | 48/48 | 48/48 |
| `ecmwf_ifs025` | 48/48 values | 48/48 | 48/48 | 48/48 |
| `ecmwf_aifs025_single` | 48/48 values | 48/48 | 48/48 | 48/48 |

(48 hourly values = 2 forecast days; all non-null.)

**Caveat carried from the live-data fix:** AIFS must be requested as
`ecmwf_aifs025_single`. The bare `ecmwf_aifs025` id returns HTTP 200 with an
**all-null** array — it fails silently rather than erroring, which is exactly
how AIFS was kept out of LIVE mode until now. Any new upper-air code must
assert non-null values and name the model id in the failure message.

**Second caveat:** GFS upper-air on Open-Meteo is `gfs_seamless`, a blend of
the 0.25° and 0.13° GFS products; IFS/AIFS are native 0.25°. The grids are
therefore not identical in effective resolution. That is acceptable for
regime classification (which is a large-scale pattern problem) but must be
stated wherever the upper-air features are used as if they were homogeneous.

---

## 6. Reshaping the 3-D slices for the ML pipeline

### 6.1 Raw shape

One request returns, per point, three parallel hourly arrays. Reshape to:

```
(n_points, n_hours, n_variables)   e.g. (289, 168, 3)
```

### 6.2 Reduce to the pipeline's context grid

1. **Time.** Bin hours into the pipeline's lead windows (24, 48, 72, 96, 120)
   and take window means. → `(n_points, 5, n_variables)`.
2. **Space.** Two parallel reductions, both needed:
   * *Zone means* — average over the points belonging to each pilot zone →
     `(n_zones, 5, n_variables)`. Directly comparable with existing features.
   * *Synoptic pattern* — flatten the point dimension and reduce with EOF /
     PCA to the **top 10 principal components** per lead, fitted on the
     **training split only** (fitting on the full record leaks the test
     period into the features and invalidates every verification number).
     → `(n_zones, 5, 10)`.
3. **Derived synoptic indices** (computed from the grid, more interpretable
   than raw PCs and useful as regime rules):
   * Low-level jet strength: mean 850 hPa `u` over 10–20°N, 70–85°E.
   * Trough latitude: latitude of the minimum 500 hPa height along 75–85°E.
   * Monsoon index: 500 hPa height difference between 5–15°N and 25–35°N.
   * WD index: mean 500 hPa height anomaly over 30–35°N, 60–75°E.
   * Depression index: minimum 850 hPa height / maximum low-level vorticity
     over the Bay of Bengal and Arabian Sea boxes.

### 6.3 Storage contract

One parquet row per `(zone, valid_time, lead_hours)`:

```
zone, valid_time, lead_hours, source_model,
u850_mean, v850_mean, z500_mean, w500_mean, t850_mean,     # zone means
u850_jet, trough_lat, monsoon_index, wd_index, dep_index,   # indices
pc01..pc10,                                                 # pattern PCs
feature_schema_version                                      # bump on change
```

Stored alongside the existing live parquet files, one file per model per
cycle, written atomically (the existing `_atomic_write_parquet` helper).

### 6.4 Integration points

* **Meta-model features.** Append the new columns to `FEATURE_NAMES` in
  `backend/app/blending/features.py` and bump a feature-schema version. This
  is a *retraining* change, not a live patch: the LightGBM skill models must
  be refit, and the manifest must record the new schema so an old model can
  never be loaded against new features.
* **Regime detector.** Add a synoptic path: rule thresholds over the indices
  above (e.g. `u850_jet > threshold and trough_lat ≈ 21 → active_monsoon`),
  keeping the current surface rules as the fallback branch. The detector
  should return which path fired so the dashboard can say so.
* **Graceful degradation.** If upper-air is missing for a cycle, the detector
  falls back to surface rules and the response must state
  `regime_basis: "surface_fallback"`. Never silently blend a stale
  upper-air pattern into a fresh forecast.

---

## 7. Work plan and acceptance criteria

| Step | Work | Estimate |
|---|---|---|
| 1 | Extend `fetch_openmeteo.py` with an upper-air fetch at representative points; unit-test the u/v decomposition | 0.5 day |
| 2 | Move to the 2.5° grid; store the raw grid parquet; verify payload/latency against §3 | 1 day |
| 3 | Derive zone means + indices; PCA fitted on train only; schema version | 1.5 days |
| 4 | Wire into `FEATURE_NAMES`; refit models; regenerate evaluation artifacts | 1 day |
| 5 | Add the synoptic path to the regime detector with fallback + `regime_basis` reporting | 1 day |
| 6 | Decide and implement the historical upper-air source for training (ERA5/CDS) | 2–3 days |
| 7 | Verify: regime labels stable across a known monsoon / break / WD episode; no leakage (PCA and thresholds fitted on train only) | 1 day |

**Acceptance criteria**

1. Regime classification for a hand-labelled set of known days (active
   monsoon, break monsoon, western disturbance, depression) matches the
   synoptic reality, and the detector reports which path produced the label.
2. Every existing verification number is recomputed after the feature change;
   the comparison is published whether it improves or not.
3. A cycle with upper-air unavailable still serves a forecast, labelled
   `regime_basis: "surface_fallback"`.
4. Payload and latency per refresh stay within the measured envelope in §3,
   or the deviation is explained.

---

## 8. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| u/v sign convention wrong | Silently mirrored circulation; classifier looks plausible and is wrong | Hand-worked unit test before anything else |
| Wrong AIFS model id | All-null response, AIFS silently absent | Assert non-null values; name the id in the error (already done for surface) |
| PCA / thresholds fitted on the full record | Leakage; every verification number becomes invalid | Fit on train only; add a leakage test |
| Historical upper-air source unresolved | Blocks the retrain | Decide in step 1, not step 6 |
| Grid heterogeneity between GFS (seamless) and IFS/AIFS (native 0.25°) | Features not strictly comparable | State it; prefer indices and PCs over raw point values |
| 15-minute cadence × 3 models × grid | ≈ 500 MB/day egress | Server-side only; enable gzip; consider hourly upper-air cadence since synoptic patterns change slowly |
