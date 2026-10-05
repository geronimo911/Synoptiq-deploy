# Real training

Use the existing environment at `D:\SIH2026\.venv`:

```powershell
. D:\SIH2026\.venv\Scripts\Activate.ps1
D:\SIH2026\.venv\Scripts\python.exe training\real_data_smoke_test.py --start 2025-02-25 --end YYYY-MM-DD
D:\SIH2026\.venv\Scripts\python.exe training\run_real_training.py --end YYYY-MM-DD
```

The entrypoints are fail-closed. They require Earthdata and CDS credentials and do not generate replacement data. NASA IMERG uses the V07 Final product and an Earthaccess redirect-aware session backed by a temporary `_netrc`; the Earthdata account must authorize GES DISC. ECMWF Open Data is available through AWS, Azure, Google Cloud, and the ECMWF endpoint; MARS is optional and only selected with `--ecmwf-source mars`. Standard training starts no earlier than 2025-02-25 and uses 00Z so its daily windows match IMERG and ERA5. AIFS 00Z is unavailable on the first date; those records remain absent, and complete three-model 00Z contexts begin when all sources exist. ECMWF GRIB `tp` values in `kg m**-2` must be normalized to metres before daily differencing. Training requests the same date range from every provider and keeps aligned data under `data/real/cache/`, models under `models/real/`, and versioned artifacts under `artifacts/real/`.

The active local serving bundle is `synoptiq-real-12m-20260930-aifs-unitfix` under `artifacts/real_12m_aifs_corrected/`. The earlier `synoptiq-real-12m-20260929` bundle is superseded because its AIFS precipitation rows had the GRIB millimetre-to-metre unit error.

AIFS source rows record `AIFS Single v1` before 2026-05-12 and `AIFS Single v2` from that operational transition date. The selected generation is persisted with training forecasts and included in the production manifest.

`--smoke-only` performs download, decoding, alignment, and table validation without replacing artifacts. A full run trains the nine source-skill models, three bust classifiers, validation calibration, and untouched test evaluation before writing a versioned manifest. The fixed prototype remains 2025-02-26 through 2025-03-10; its manifest must retain `REAL_PROTOTYPE` and `LIMITED_HISTORY`. Fake/demo material is retained and never participates in real training.
