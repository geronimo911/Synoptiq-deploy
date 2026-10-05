# Synoptiq integration status

## Verified in the build environment

- Python backend test suite: 19 passed, 1 warning.
- Python compileall: passed for backend and real-data training code.
- API smoke test: 10/10 `/api/v1` endpoints returned HTTP 200 against the bundled prototype database.
- Verification evaluation script now emits precipitation CSI plus temperature/wind RMSE results for all three pilot regions.
- Weight-map API works for requested season/regime overrides.
- Real-data training orchestrator is Windows-native and uses `D:\\SIH2026\\.venv` without creating another environment.
- Real-data training protects the current state with an automatic backup and restores it if real training fails.

## Not completed in this environment

- IMERG was not downloaded because NASA Earthdata credentials were not available.
- ERA5 was not downloaded because CDS credentials were not available.
- Real-data model training and untouched real-data evaluation therefore have not been run here.
- The bundled artifact directory still contains the previous prototype artifacts until the real-data smoke gate passes; the training orchestrator promotes real artifacts only after successful training/evaluation and removes the previous state.
- A frontend production build was not completed in this environment because the supplied workspace did not contain a usable npm binary cache and offline dependency installation could not fetch missing packages. Run `npm install` on the Windows project machine, then `npm run build`.
