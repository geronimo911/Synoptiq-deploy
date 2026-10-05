# Event Calibration Report

Model version: synoptiq-real-12m-20260930-aifs-unitfix

Manifest validation period: {'start': '2025-10-03', 'end': '2025-11-25'}
Manifest TEST period: {'start': '2025-11-26', 'end': '2026-02-25'}

Database split boundaries: {'val_start': '2025-10-06 00:00:00', 'test_start': '2025-11-30 00:00:00'}

TEST observations are excluded from fitting. Isotonic fitting requires at least 5 positive and 5 negative real events.

Manifest-window validation event counts:

| Variable | Global + | Global - | KWG + / - | BOB + / - | IGP + / - |
|---|---:|---:|---:|---:|---:|
| temperature | 0 | 810 | 0 / 270 | 0 / 270 | 0 / 270 |
| wind_speed | 0 | 810 | 0 / 270 | 0 / 270 | 0 / 270 |

| Variable | Event | Threshold | Scope | Fit + | Fit - | Validation + | Validation - | Source | OOF | Status |
|---|---|---:|---|---:|---:|---:|---:|---|---|---|
| temperature | heatwave | 40 | global | 0 | 2220 | 0 | 825 | time_ordered_oof_train | true | withheld |
| temperature | heatwave | 40 | kerala_western_ghats | 0 | 740 | 0 | 275 | time_ordered_oof_train | true | withheld |
| temperature | heatwave | 40 | bay_of_bengal_east_coast | 0 | 740 | 0 | 275 | time_ordered_oof_train | true | withheld |
| temperature | heatwave | 40 | indo_gangetic_plains | 0 | 740 | 0 | 275 | time_ordered_oof_train | true | withheld |
| wind_speed | gale | 62 | global | 0 | 2220 | 0 | 825 | time_ordered_oof_train | true | withheld |
| wind_speed | gale | 62 | kerala_western_ghats | 0 | 740 | 0 | 275 | time_ordered_oof_train | true | withheld |
| wind_speed | gale | 62 | bay_of_bengal_east_coast | 0 | 740 | 0 | 275 | time_ordered_oof_train | true | withheld |
| wind_speed | gale | 62 | indo_gangetic_plains | 0 | 740 | 0 | 275 | time_ordered_oof_train | true | withheld |

No fit-set Brier/reliability metrics are reported because the same observations are used to fit the calibrator.

## Untouched TEST Evaluation

TEST starts at 2025-11-30 00:00:00; calibrators were frozen before scoring.

| Region | Variable | Event | N | Brier | Event frequency | Mean predicted | ECE | Status |
|---|---|---|---:|---:|---:|---:|---:|---|
| kerala_western_ghats | temperature | heatwave | 440 | n/a | 0.0 | n/a | n/a | No fitted real calibrator; probability metrics unavailable. |
| kerala_western_ghats | wind_speed | gale | 440 | n/a | 0.0 | n/a | n/a | No fitted real calibrator; probability metrics unavailable. |
| bay_of_bengal_east_coast | temperature | heatwave | 440 | n/a | 0.0 | n/a | n/a | No fitted real calibrator; probability metrics unavailable. |
| bay_of_bengal_east_coast | wind_speed | gale | 440 | n/a | 0.0 | n/a | n/a | No fitted real calibrator; probability metrics unavailable. |
| indo_gangetic_plains | temperature | heatwave | 440 | n/a | 0.0 | n/a | n/a | No fitted real calibrator; probability metrics unavailable. |
| indo_gangetic_plains | wind_speed | gale | 440 | n/a | 0.0 | n/a | n/a | No fitted real calibrator; probability metrics unavailable. |
