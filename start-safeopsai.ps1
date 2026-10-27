# SafeOpsAI - Local Windows Platform Launcher
# One-Click startup with dependency checking, PID tracking, and log redirection.
# PowerShell 5.1 Compatible. Does NOT require Docker or Docker Compose.

param(
    [switch]$SkipBrowser,
    [switch]$Wait,
    [int]$ReadinessTimeoutSec = 60
)

$ErrorActionPreference = "Continue"

$REPO_ROOT = Split-Path -Parent $MyInvocation.MyCommand.Path
$SAFEOPS_DIR = Join-Path $REPO_ROOT "safeopsai"
$RUN_DIR = Join-Path $REPO_ROOT ".run"
$LOGS_DIR = Join-Path $REPO_ROOT "logs"
$PID_FILE = Join-Path $RUN_DIR "services.json"

Write-Host ""
Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "  SafeOpsAI - Local Platform Launcher (No Docker)" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host ""

# Ensure run and logs directories exist
if (-not (Test-Path $RUN_DIR)) { New-Item -ItemType Directory -Path $RUN_DIR -Force | Out-Null }
if (-not (Test-Path $LOGS_DIR)) { New-Item -ItemType Directory -Path $LOGS_DIR -Force | Out-Null }

# 1. Run Readiness Check First
Write-Host "Checking system prerequisites..." -ForegroundColor Yellow
$pythonCmd = "python"
$pyCheck = & $pythonCmd --version 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host " [ERROR] Python 3.10+ is required in PATH." -ForegroundColor Red
    exit 1
}

# Check PostgreSQL
$pgCheck = & $pythonCmd -c "import asyncpg, asyncio; asyncio.run(asyncpg.connect('postgresql://safeops:safeops123@localhost:5432/safeopsdb'))" 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host " [ERROR] PostgreSQL is not running or safeopsdb is inaccessible." -ForegroundColor Red
    Write-Host "         Target: postgresql://safeops:safeops123@localhost:5432/safeopsdb" -ForegroundColor DarkRed
    Write-Host "         Please start PostgreSQL or run the init.sql schema before starting." -ForegroundColor Yellow
    exit 1
}
Write-Host " [OK] PostgreSQL database is connected." -ForegroundColor Green

# Check Ollama status
$ollamaAvailable = $false
try {
    $resp = Invoke-WebRequest -Uri "http://localhost:11434/api/tags" -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
    $ollamaAvailable = $true
    Write-Host " [OK] Ollama is running on localhost:11434 (LLM available)." -ForegroundColor Green
} catch {
    Write-Host " [WARN] Ollama not detected. Root Cause Agent will use rule-based fallback." -ForegroundColor DarkYellow
}

# 2. Check for stale PID file or already running services
$runningProcesses = @{}
if (Test-Path $PID_FILE) {
    try {
        $existing = Get-Content $PID_FILE -Raw | ConvertFrom-Json
        foreach ($prop in $existing.psobject.Properties) {
            $entry = $prop.Value
            $p = Get-Process -Id $entry.pid -ErrorAction SilentlyContinue
            if ($p -and $p.ProcessName -like "*python*") {
                $runningProcesses[$prop.Name] = $entry
            }
        }
    } catch {}
}

# 3. Environment variables configuration
$env:DB_HOST = "localhost"
$env:DB_PORT = "5432"
$env:DB_NAME = "safeopsdb"
$env:DB_USER = "safeops"
$env:DB_PASS = "safeops123"
$env:PROMETHEUS_URL = "http://localhost:9090"
$env:OLLAMA_URL = "http://localhost:11434"
$env:OLLAMA_HOST = "http://localhost:11434"
$env:OLLAMA_MODEL = "llama3.2"
$env:LOG_LEVEL = "INFO"
$env:BACKEND_URL = "http://localhost:8000"
$env:SAFEOPS_ENV = "simulation"
$env:ENVIRONMENT = "simulation"

# Service Definitions
$services = @(
    @{
        Id = "backend"
        Name = "FastAPI Backend"
        Port = 8000
        HealthUrl = "http://127.0.0.1:8000/health"
        Dir = (Join-Path $SAFEOPS_DIR "backend")
        Command = "python -m uvicorn main:app --host 0.0.0.0 --port 8000 --log-level warning"
    },
    @{
        Id = "incident-agent"
        Name = "Incident Agent"
        Port = 8001
        HealthUrl = "http://127.0.0.1:8001/health"
        Dir = (Join-Path $SAFEOPS_DIR "incident-agent")
        Command = "python -m incident_agent.main"
    },
    @{
        Id = "root-cause-agent"
        Name = "Root Cause Agent"
        Port = 8002
        HealthUrl = "http://127.0.0.1:8002/health"
        Dir = (Join-Path $SAFEOPS_DIR "root-cause-agent")
        Command = "python -m root_cause_agent.main"
    },
    @{
        Id = "scoring-agent"
        Name = "Scoring Agent"
        Port = 8003
        HealthUrl = "http://127.0.0.1:8003/health"
        Dir = (Join-Path $SAFEOPS_DIR "scoring-agent")
        Command = "python -m scoring_agent.main"
    },
    @{
        Id = "coordinator-agent"
        Name = "Coordinator Agent"
        Port = 8004
        HealthUrl = "http://127.0.0.1:8004/health"
        Dir = (Join-Path $SAFEOPS_DIR "coordinator-agent")
        Command = "python -m coordinator_agent.main"
    },
    @{
        Id = "sandbox-agent"
        Name = "Sandbox Agent"
        Port = 8005
        HealthUrl = "http://127.0.0.1:8005/health"
        Dir = (Join-Path $SAFEOPS_DIR "sandbox-agent")
        Command = "python -m uvicorn sandbox_agent.main:app --host 0.0.0.0 --port 8005 --log-level warning"
    },
    @{
        Id = "remediation-agent"
        Name = "Remediation Agent"
        Port = 8006
        HealthUrl = "http://127.0.0.1:8006/health"
        Dir = (Join-Path $SAFEOPS_DIR "remediation-agent")
        Command = "python -m uvicorn remediation_agent.main:app --host 0.0.0.0 --port 8006 --log-level warning"
    },
    @{
        Id = "frontend"
        Name = "Operations Control Center UI"
        Port = 3000
        HealthUrl = "http://127.0.0.1:3000"
        Dir = (Join-Path $SAFEOPS_DIR "frontend")
        Command = "python -m http.server 3000 --directory " + (Join-Path $SAFEOPS_DIR "frontend")
    }
)

# Helper function to check health
function Check-Health {
    param([string]$Url)
    try {
        $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
        return ($r.StatusCode -ge 200 -and $r.StatusCode -lt 400)
    } catch {
        return $false
    }
}

$launchedMetadata = [ordered]@{}

Write-Host ""
Write-Host "Starting core services..." -ForegroundColor Yellow

foreach ($svc in $services) {
    # Check if already healthy
    if (Check-Health $svc.HealthUrl) {
        Write-Host " [REUSE] $($svc.Name) is already healthy on port $($svc.Port)." -ForegroundColor Cyan
        if ($runningProcesses.ContainsKey($svc.Id)) {
            $launchedMetadata[$svc.Id] = $runningProcesses[$svc.Id]
        }
        continue
    }

    # Check port conflicts
    $bound = Get-NetTCPConnection -LocalPort $svc.Port -ErrorAction SilentlyContinue | Where-Object { $_.State -eq "Listen" }
    if ($bound) {
        Write-Host " [PORT CONFLICT] Port $($svc.Port) is already in use by PID $($bound[0].OwningProcess)." -ForegroundColor Red
        Write-Host "                 Cannot start $($svc.Name)." -ForegroundColor Red
        continue
    }

    # Start process with log redirection
    $logOut = Join-Path $LOGS_DIR "$($svc.Id).out.log"
    $logErr = Join-Path $LOGS_DIR "$($svc.Id).err.log"

    $parts = $svc.Command -split " "
    $exe = $parts[0]
    $args = $parts[1..($parts.Length - 1)] -join " "

    $proc = Start-Process -FilePath $exe `
        -ArgumentList $args `
        -WorkingDirectory $svc.Dir `
        -RedirectStandardOutput $logOut `
        -RedirectStandardError $logErr `
        -WindowStyle Hidden `
        -PassThru

    $launchedMetadata[$svc.Id] = [ordered]@{
        pid = $proc.Id
        name = $svc.Name
        port = $svc.Port
        health_url = $svc.HealthUrl
        started_at = (Get-Date -Format "yyyy-MM-ddTHH:mm:ssZ")
        out_log = $logOut
        err_log = $logErr
    }

    Write-Host " [STARTED] $($svc.Name) (PID $($proc.Id)) -> Port $($svc.Port)" -ForegroundColor Green

    # Wait briefly for backend first before launching downstream agents
    if ($svc.Id -eq "backend") {
        Start-Sleep -Seconds 2
    }
}

# Save metadata
$launchedMetadata | ConvertTo-Json -Depth 3 | Set-Content $PID_FILE -Force

# 4. Readiness Gate Verification
Write-Host ""
Write-Host "Waiting for service readiness gates..." -ForegroundColor Yellow

$allReady = $true
foreach ($svc in $services) {
    $t0 = [System.Diagnostics.Stopwatch]::StartNew()
    $ready = $false
    while ($t0.Elapsed.TotalSeconds -lt $ReadinessTimeoutSec) {
        if (Check-Health $svc.HealthUrl) {
            $ready = $true
            break
        }
        Start-Sleep -Milliseconds 800
    }
    if ($ready) {
        Write-Host "  [OK] $($svc.Name) (: $($svc.Port)) [READY] in $([math]::Round($t0.Elapsed.TotalSeconds, 1))s" -ForegroundColor Green
    } else {
        Write-Host "  [FAIL] $($svc.Name) (: $($svc.Port)) [TIMEOUT after ${ReadinessTimeoutSec}s]" -ForegroundColor Red
        Write-Host "    Inspect log: $(Join-Path $LOGS_DIR "$($svc.Id).err.log")" -ForegroundColor DarkRed
        $allReady = $false
    }
}

Write-Host ""
Write-Host "==========================================================" -ForegroundColor Cyan
if ($allReady) {
    Write-Host "  SafeOpsAI is fully READY for local demonstration!" -ForegroundColor Green
    Write-Host "  Dashboard: http://localhost:3000" -ForegroundColor Cyan
    Write-Host "  API Docs:  http://localhost:8000/docs" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  To stop the platform safely: .\stop-safeopsai.ps1" -ForegroundColor Yellow
    Write-Host "==========================================================" -ForegroundColor Cyan

    if (-not $SkipBrowser) {
        Start-Process "http://localhost:3000"
    }

    if ($Wait) {
        Write-Host ""
        Write-Host "SafeOpsAI daemon is active. Services running (press Ctrl+C to stop)..." -ForegroundColor Cyan
        while ($true) {
            Start-Sleep -Seconds 2
        }
    }
} else {
    Write-Host "  SafeOpsAI started with errors. Check logs in $LOGS_DIR" -ForegroundColor Red
    Write-Host "==========================================================" -ForegroundColor Cyan
}
