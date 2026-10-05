$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    $Python = Join-Path (Split-Path $Root -Parent) ".venv\Scripts\python.exe"
}
$ArtifactRoot = Join-Path $Root "artifacts\real_12m_aifs_corrected"
$ManifestRoot = Join-Path $ArtifactRoot "manifests"
$RuntimeDir = Join-Path $Root "data\real\runtime"

if (-not (Test-Path $Python)) {
    throw "Repository Python environment is missing: $Python"
}
& $Python -c "import cfgrib, eccodes"
if ($LASTEXITCODE -ne 0) {
    throw "The repo .venv must have the GRIB decoder installed. Run: .\.venv\Scripts\python.exe -m pip install -r training\requirements-realdata.txt"
}

$ActiveManifest = Get-ChildItem -Path $ManifestRoot -Filter "manifest.json" -Recurse -File |
    ForEach-Object {
        $manifest = Get-Content $_.FullName -Raw | ConvertFrom-Json
        if ($manifest.data_mode -eq "real" -and $manifest.synthetic_data_used -eq $false) {
            [pscustomobject]@{ Path = $_.FullName; Data = $manifest }
        }
    } |
    Sort-Object { $_.Data.created_at } -Descending |
    Select-Object -First 1

if (-not $ActiveManifest) {
    throw "No validated non-synthetic REAL_12M manifest was found under $ManifestRoot"
}

New-Item -ItemType Directory -Path $RuntimeDir -Force | Out-Null
foreach ($port in @(8000, 5173)) {
    if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) {
        throw "Port $port is already in use. Stop its current process before running this launcher."
    }
}

$env:SYNOPTIQ_MODE = "real"
$env:SYNOPTIQ_MODEL_VERSION = [string]$ActiveManifest.Data.model_version
$env:SYNOPTIQ_ARTIFACTS_DIR = $ArtifactRoot
$env:SYNOPTIQ_MODELS_DIR = Join-Path $ArtifactRoot "models"
$env:SYNOPTIQ_CALIBRATION_DIR = Join-Path $ArtifactRoot "calibration"
$env:SYNOPTIQ_METRICS_DIR = Join-Path $ArtifactRoot "metrics"
$env:SYNOPTIQ_LIVE_DATA_DIR = Join-Path $Root "data\real\live_aifs_unitfix"
$env:SYNOPTIQ_CACHE = Join-Path $Root "training\realdata\cache\12m_aifs_corrected"
$env:SYNOPTIQ_DATABASE_ROLE = "TRAINING"
$env:TRAINING_DATABASE_URL = "sqlite:///./data/real/training_aifs_corrected.sqlite3"
$env:DATABASE_URL = "postgresql+psycopg://disabled:disabled@127.0.0.1:1/synoptiq"
$env:PYTHONPATH = Join-Path $Root "backend"
$env:VITE_API_BASE_URL = "/api/v1"
$env:SYNOPTIQ_API_URL = "http://127.0.0.1:8000"
$env:VITE_APP_MODE = "LIVE"
$env:SYNOPTIQ_LIVE_REFRESH_MINUTES = "15"
$env:SYNOPTIQ_LIVE_FRESHNESS_MINUTES = "45"
$env:SYNOPTIQ_AUTO_REFRESH = "0"

$backend = Start-Process -FilePath $Python `
    -ArgumentList @("-m", "uvicorn", "app.main:app", "--app-dir", "backend", "--host", "127.0.0.1", "--port", "8000") `
    -WorkingDirectory $Root -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $RuntimeDir "backend.log") `
    -RedirectStandardError (Join-Path $RuntimeDir "backend-error.log")
$backend.Id | Set-Content (Join-Path $RuntimeDir "backend.pid")

$ready = $false
for ($attempt = 0; $attempt -lt 60; $attempt++) {
    if ($backend.HasExited) {
        Get-Content (Join-Path $RuntimeDir "backend-error.log") -Tail 40 -ErrorAction SilentlyContinue
        throw "Backend exited during startup. See $RuntimeDir\backend-error.log"
    }
    try {
        $health = Invoke-RestMethod "http://127.0.0.1:8000/health" -TimeoutSec 2
        if ($health.status -eq "ok") {
            $ready = $true
            break
        }
    } catch {
        Start-Sleep -Milliseconds 500
    }
}
if (-not $ready) {
    throw "Backend did not become healthy. See $RuntimeDir\backend-error.log"
}

$refresh = Start-Process -FilePath $Python `
    -ArgumentList @("training/realdata/live_refresh.py") `
    -WorkingDirectory $Root -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $RuntimeDir "live-refresh.log") `
    -RedirectStandardError (Join-Path $RuntimeDir "live-refresh-error.log")
$refresh.Id | Set-Content (Join-Path $RuntimeDir "live-refresh.pid")

$frontendCommand = "npm --prefix frontend run dev -- --host 127.0.0.1 --port 5173"
$frontend = Start-Process -FilePath $env:ComSpec `
    -ArgumentList @("/c", $frontendCommand) `
    -WorkingDirectory $Root -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $RuntimeDir "frontend.log") `
    -RedirectStandardError (Join-Path $RuntimeDir "frontend-error.log")
$frontend.Id | Set-Content (Join-Path $RuntimeDir "frontend.pid")

$frontendReady = $false
for ($attempt = 0; $attempt -lt 60; $attempt++) {
    try {
        $null = Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:5173/" -TimeoutSec 2
        $frontendReady = $true
        break
    } catch {
        Start-Sleep -Milliseconds 500
    }
}
if (-not $frontendReady) {
    throw "Frontend did not become available. See $RuntimeDir\frontend-error.log"
}

Write-Output "Backend:  http://127.0.0.1:8000 (PID $($backend.Id))"
Write-Output "Frontend: http://127.0.0.1:5173 (PID $($frontend.Id))"
Write-Output "Refresh:  PID $($refresh.Id)"
Write-Output "Model:    $($ActiveManifest.Data.model_version)"
Write-Output "Logs:     $RuntimeDir"
