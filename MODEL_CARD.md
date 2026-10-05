# Model card

Synoptiq is a meta-layer over external GFS, IFS, and AIFS forecasts. It does not replace those forecast systems. The intended production model set is nine LightGBM source-skill models: one per source and variable for precipitation, temperature, and wind, plus three calibrated bust-risk models.

Training must use real aligned forecast/verification pairs, chronological train/validation/test partitions, and an untouched test period. Reported metrics must come from the machine-readable real evaluation report. No target improvement is asserted by this repository until that report exists.

The current corrected bundle is `synoptiq-real-12m-20260930-aifs-unitfix`, trained on the real 2025-02-26 through 2026-02-25 window (49,275 forecast rows, 3,285 truth rows). The prior `synoptiq-real-12m-20260929` bundle is superseded: ECMWF Open Data exposes accumulated AIFS precipitation as `kg m**-2`, which the prior decoder treated as metres. Corrected AIFS precipitation is normalized before daily aggregation and all model/calibration/evaluation/inference artifacts were regenerated from the real training/validation/test split. The original artifacts remain available for audit and are not the active local model.

ECMWF IFS/AIFS retrieval uses the official Open Data Azure, Google Cloud, ECMWF, and AWS mirrors; mirror identity is recorded. AIFS source provenance identifies Single v1 before 2026-05-12 and Single v2 from 2026-05-12 onward. Its precipitation is unit-normalized from `kg m**-2` (numerically mm) before conversion to the pipeline's cumulative-metre contract.

The production regime detector remains transparent and rule-based, using real meteorological context. Trust scoring, abstention, calibration, bias correction, dynamic weighting, and explanations are operational outputs only when the corresponding validated artifacts are available.

## Untouched-test performance (auto-generated)

| Region | Variable | Blend MAE | Best single | Best-single MAE | Blend wins? |
|---|---|---|---|---|---|
| bay of bengal east coast | precipitation | 0.6654 | IFS | 0.6514 | no |
| bay of bengal east coast | temperature | 0.3700 | GFS | 0.2674 | no |
| bay of bengal east coast | wind_speed | 1.7460 | AIFS | 2.0658 | yes |
| indo gangetic plains | precipitation | 0.4678 | IFS | 0.4881 | yes |
| indo gangetic plains | temperature | 1.6450 | AIFS | 0.6729 | no |
| indo gangetic plains | wind_speed | 1.0580 | GFS | 1.6223 | yes |
| kerala western ghats | precipitation | 0.3786 | AIFS | 0.4781 | yes |
| kerala western ghats | temperature | 0.2740 | AIFS | 0.7186 | yes |
| kerala western ghats | wind_speed | 1.3610 | AIFS | 2.2243 | yes |

Every number above is read from `artifacts/real_12m_aifs_corrected/metrics/synoptiq-real-12m-20260930-aifs-unitfix/blend_eval_*.json` at generation time.

## Research-suite results (auto-generated)

- **Oracle regret**: the blend recovers **51.66%** of the
  hindsight-oracle's MAE advantage over the best fixed single model
  (oracle 0.7641, best fixed 1.0493, blend 0.902;
  averaged over all nine region-variable cells).
- **AI value**: adding AIFS to the GFS+IFS physics pair improves test MAE in
  **8/9** region-variable cells (per-cell numbers in the research suite JSON).
- **Bias correction**: the calibration step reduced test MAE for every variable —
  precipitation 7.07%, temperature
  24.36%, wind 37.69%.
- **Trust audit**: Spearman rank correlation between issued trust and realised
  absolute error is -0.1301 (negative = higher trust, lower error). The audit is
  published even where it is unflattering; see the research suite for tier-by-tier
  honesty.

## Intended use and limits

- Zone-mean daily guidance for three Indian pilot regions; not a point forecast and not a nowcast.
- Rainfall truth is IMERG Late V07; temperature/wind truth is NASA POWER (MERRA-2). Swapping in IMERG Final or CDS ERA5 will shift verification numbers.
- The blend is strongest where models disagree but calibrated; it can lose to the best single model in cells where one model dominates — the table above shows exactly where.

*Regenerated automatically by `backend/scripts/generate_model_card.py` at
2026-10-01T06:19:21Z. Do not hand-edit the
numbers; edit the script.*
