# SafeOpsAI - Local Safe Shutdown Utility
# Stops ONLY the processes launched and tracked by start-safeopsai.ps1.
# PowerShell 5.1 Compatible. Does not terminate unrelated Python instances or system services.

param(
    [switch]$Force
)

$ErrorActionPreference = "Continue"

$REPO_ROOT = Split-Path -Parent $MyInvocation.MyCommand.Path
$RUN_DIR = Join-Path $REPO_ROOT ".run"
$PID_FILE = Join-Path $RUN_DIR "services.json"

Write-Host ""
Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "  SafeOpsAI - Safe Local Shutdown" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host ""

if (-not (Test-Path $PID_FILE)) {
    Write-Host "No active service records found at $PID_FILE." -ForegroundColor Yellow
    Write-Host "Checking if any platform ports are still listening..." -ForegroundColor DarkGray
    $ports = @(8000, 8001, 8002, 8003, 8004, 8005, 8006, 3000)
    foreach ($p in $ports) {
        $bound = Get-NetTCPConnection -LocalPort $p -ErrorAction SilentlyContinue | Where-Object { $_.State -eq "Listen" }
        if ($bound) {
            Write-Host " - Port $p is bound to PID $($bound[0].OwningProcess)" -ForegroundColor DarkYellow
        }
    }
    exit 0
}

try {
    $servicesData = Get-Content $PID_FILE -Raw | ConvertFrom-Json
} catch {
    Write-Host ("Failed to parse service records from " + $PID_FILE + ": " + $_) -ForegroundColor Red
    exit 1
}

$stoppedCount = 0
$failedCount = 0

foreach ($prop in $servicesData.psobject.Properties) {
    $svc = $prop.Value
    $pidToStop = $svc.pid
    $svcName = $svc.name

    $proc = Get-Process -Id $pidToStop -ErrorAction SilentlyContinue
    if ($proc) {
        # Safety check: confirm process is a python process
        if ($proc.ProcessName -like "*python*" -or $proc.ProcessName -like "*uvicorn*") {
            try {
                Write-Host "Stopping $svcName (PID $pidToStop)..." -NoNewline
                $proc.CloseMainWindow() | Out-Null
                Start-Sleep -Milliseconds 400
                if (-not $proc.HasExited) {
                    $proc.Kill()
                }
                Write-Host " [STOPPED]" -ForegroundColor Green
                $stoppedCount++
            } catch {
                Write-Host " [FAILED: $_]" -ForegroundColor Red
                $failedCount++
            }
        } else {
            Write-Host " [SKIPPED] PID $pidToStop ($($proc.ProcessName)) does not match expected Python process for $svcName." -ForegroundColor DarkYellow
        }
    } else {
        Write-Host " - $svcName (PID $pidToStop) already stopped." -ForegroundColor DarkGray
    }
}

# Clean up PID file
Remove-Item $PID_FILE -Force -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "Shutdown Summary: $stoppedCount service(s) stopped cleanly." -ForegroundColor Green
if ($failedCount -gt 0) {
    Write-Host "Warning: $failedCount service(s) could not be stopped." -ForegroundColor Yellow
}
Write-Host "SafeOpsAI local processes have been terminated." -ForegroundColor Cyan
Write-Host ""
