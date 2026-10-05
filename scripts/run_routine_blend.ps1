# Synoptiq routine operational blend — Windows Task Scheduler entrypoint.
# Mirrors scripts/run_routine_blend.sh step for step.
#
# Register as a daily 06:20 IST job:
#   schtasks /Create /TN "Synoptiq routine blend" /SC DAILY /ST 06:20 ^
#     /TR "powershell -ExecutionPolicy Bypass -File C:\path\Synoptiq\scripts\run_routine_blend.ps1"
param([string]$Day = "")
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
Write-Host "=== Synoptiq routine blend run @ $(Get-Date -Format o) ==="

if (-not $env:SYNOPTIQ_MODE) { $env:SYNOPTIQ_MODE = "real" }
if (-not $env:SYNOPTIQ_ARTIFACTS_DIR) { $env:SYNOPTIQ_ARTIFACTS_DIR = "$PWD\artifacts\real_12m_aifs_corrected" }
if (-not $env:DATABASE_URL) { $env:DATABASE_URL = "sqlite:///./data/real/prod.sqlite3" }
$Blends   = "$env:SYNOPTIQ_ARTIFACTS_DIR\inference\historical_blends.jsonl"
$Research = "$env:SYNOPTIQ_ARTIFACTS_DIR\research"

Write-Host "`n--- 1/4 ingest latest real provider cycle"
if ($env:SKIP_INGEST -ne "1") {
  try { python training/realdata/ingest_latest_cycle.py; Write-Host "ingest: OK" }
  catch { Write-Host "ingest: UNAVAILABLE — continuing on the archived record (stated in the bulletin)." }
} else { Write-Host "ingest skipped (SKIP_INGEST=1)" }

Write-Host "`n--- 2/4 rebuild research + operational artifacts"
python backend/scripts/research_suite.py --blends $Blends --out "$Research\research_suite.json"
python backend/scripts/build_advanced_artifacts.py --blends $Blends --out-dir $Research
try { python backend/scripts/generate_model_card.py } catch { Write-Host "model card: skipped" }

Write-Host "`n--- 3/4 export bulletin + CAP alerts"
if ($Day) { python backend/scripts/export_bulletin.py --date $Day }
else { python backend/scripts/export_bulletin.py }

Write-Host "`n--- 4/4 done"
Get-Content ops\run_log.jsonl -Tail 1
Write-Host "=== routine blend complete ==="
