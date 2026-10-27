# SafeOpsAI — Local Windows Shutdown Script
# Stops only the processes started by start-local.ps1

$PIDS_FILE = Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) "safeopsai-pids.txt"

Write-Host ""
Write-Host "Stopping SafeOpsAI services..." -ForegroundColor Yellow

if (-not (Test-Path $PIDS_FILE)) {
    Write-Host "No PID file found ($PIDS_FILE). Nothing to stop." -ForegroundColor Yellow
    exit 0
}

$lines = Get-Content $PIDS_FILE
foreach ($line in $lines) {
    if ($line -match "^(\d+)\s+(.+)$") {
        $pid_ = [int]$Matches[1]
        $name = $Matches[2]
        try {
            Stop-Process -Id $pid_ -Force -ErrorAction Stop
            Write-Host "  ✓ Stopped $name (PID $pid_)" -ForegroundColor Green
        } catch {
            Write-Host "  - $name (PID $pid_) already stopped or not found" -ForegroundColor DarkGray
        }
    }
}

Remove-Item $PIDS_FILE -Force -ErrorAction SilentlyContinue
Write-Host ""
Write-Host "All SafeOpsAI services stopped." -ForegroundColor Green
Write-Host ""
