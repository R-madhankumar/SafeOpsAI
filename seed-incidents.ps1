<#
.SYNOPSIS
    Seeds 100+ realistic demo incidents into the SafeOpsAI PostgreSQL database.

.DESCRIPTION
    Runs safeopsai/database/seed_incidents.py to generate deterministic, realistic
    incident histories across 8 services, 8 incident types, and 4 severities.
    Guarantees idempotency and safe demo data isolation without affecting real records.

.PARAMETER Count
    Number of demo incidents to generate (default: 120).

.PARAMETER Seed
    Deterministic random seed for reproducibility (default: 42).

.PARAMETER CleanDemo
    If set, removes existing demo records (WHERE is_demo = TRUE) prior to seeding.
    Never removes real incidents.

.EXAMPLE
    .\seed-incidents.ps1 -Count 120
    .\seed-incidents.ps1 -Count 150 -CleanDemo
#>

[CmdletBinding()]
param(
    [int]$Count = 120,
    [int]$Seed = 42,
    [int]$Days = 30,
    [switch]$CleanDemo
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$seederScript = Join-Path $scriptDir "safeopsai\database\seed_incidents.py"

if (-not (Test-Path $seederScript)) {
    # Check if run directly from within safeopsai/
    $seederScript = Join-Path $scriptDir "database\seed_incidents.py"
}

if (-not (Test-Path $seederScript)) {
    Write-Error "Could not find seed_incidents.py at $seederScript"
    exit 1
}

Write-Host "============================================================" -ForegroundColor Magenta
Write-Host " SafeOpsAI — Incident Seeding Engine" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Magenta
Write-Host " Count:     $Count" -ForegroundColor White
Write-Host " Seed:      $Seed" -ForegroundColor White
Write-Host " Days:      $Days" -ForegroundColor White
Write-Host " CleanDemo: $($CleanDemo.IsPresent)" -ForegroundColor White
Write-Host " Target:    safeopsdb @ localhost:5432" -ForegroundColor White
Write-Host "------------------------------------------------------------" -ForegroundColor DarkGray

$cmdArgs = @($seederScript, "--count", $Count, "--seed", $Seed, "--days", $Days)
if ($CleanDemo) {
    $cmdArgs += "--clean-demo"
}

python @cmdArgs
if ($LASTEXITCODE -ne 0) {
    Write-Error "Seeding failed with exit code $LASTEXITCODE"
    exit $LASTEXITCODE
}

Write-Host "============================================================" -ForegroundColor Green
Write-Host " [SUCCESS] Demo incidents seeded and isolated successfully!" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
