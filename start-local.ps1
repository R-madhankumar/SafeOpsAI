# SafeOpsAI — Local Windows Startup Script (No Docker Required)
# Run this from the repo root: .\start-local.ps1
# Stop everything cleanly: .\stop-local.ps1
#
# What this script starts:
#   [1] FastAPI Backend      http://localhost:8000
#   [2] Incident Agent       http://localhost:8001
#   [3] Root Cause Agent     http://localhost:8002
#   [4] Scoring Agent        http://localhost:8003
#   [5] Coordinator Agent    http://localhost:8004
#   [6] Sandbox Agent        http://localhost:8005
#   [7] Remediation Agent    http://localhost:8006
#   [8] Frontend (Python HTTP server) http://localhost:3000
#
# Prerequisites (checked automatically):
#   - Python 3.10+ in PATH
#   - PostgreSQL running on localhost:5432 (DB: safeopsdb, User: safeops)
#   - Ollama running on localhost:11434 with llama3.2 pulled
#   - All Python packages installed (pip install -r requirements.txt per agent)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$REPO   = Split-Path -Parent $MyInvocation.MyCommand.Path
$SAFEOPS = Join-Path $REPO "safeopsai"
$PIDS_FILE = Join-Path $REPO "safeopsai-pids.txt"

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  SafeOpsAI — Local Startup" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# ── Helper: check a URL is reachable ──────────────────────────────────────────
function Wait-URL {
    param([string]$Url, [string]$Name, [int]$MaxSec = 30)
    $elapsed = 0
    while ($elapsed -lt $MaxSec) {
        try {
            $resp = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2 -ErrorAction SilentlyContinue
            if ($resp.StatusCode -ge 200 -and $resp.StatusCode -lt 400) {
                Write-Host "  ✓ $Name is ready" -ForegroundColor Green
                return $true
            }
        } catch {}
        Start-Sleep -Seconds 2
        $elapsed += 2
    }
    Write-Host "  ✗ $Name did not become ready within ${MaxSec}s" -ForegroundColor Red
    return $false
}

# ── Prerequisite checks ────────────────────────────────────────────────────────
Write-Host "Checking prerequisites..." -ForegroundColor Yellow

# Python
try {
    $pyVer = python --version 2>&1
    Write-Host "  ✓ Python: $pyVer" -ForegroundColor Green
} catch {
    Write-Host "  ✗ Python not found in PATH. Install Python 3.10+ and try again." -ForegroundColor Red
    exit 1
}

# PostgreSQL connectivity
Write-Host "  Checking PostgreSQL (localhost:5432)..." -NoNewline
try {
    $pgCheck = python -c "import asyncpg, asyncio; asyncio.run(asyncpg.connect('postgresql://safeops:safeops123@localhost:5432/safeopsdb'))" 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host " ✓ Connected" -ForegroundColor Green
    } else {
        Write-Host " ✗ Cannot connect. Ensure PostgreSQL is running with DB=safeopsdb, User=safeops" -ForegroundColor Red
        Write-Host "    Error: $pgCheck" -ForegroundColor DarkRed
        Write-Host "    Create DB: psql -U postgres -c `"CREATE USER safeops WITH PASSWORD 'safeops123'; CREATE DATABASE safeopsdb OWNER safeops;`"" -ForegroundColor Yellow
        exit 1
    }
} catch {
    Write-Host " ✗ asyncpg not installed or PostgreSQL unreachable" -ForegroundColor Red
    exit 1
}

# Ollama
Write-Host "  Checking Ollama (localhost:11434)..." -NoNewline
try {
    $ollamaCheck = Invoke-WebRequest -Uri "http://localhost:11434/api/tags" -UseBasicParsing -TimeoutSec 3 -ErrorAction SilentlyContinue
    Write-Host " ✓ Running" -ForegroundColor Green
} catch {
    Write-Host " ⚠ Ollama not reachable. Root Cause Agent will use rule-based fallback." -ForegroundColor Yellow
    Write-Host "    To enable LLM: install Ollama from https://ollama.com and run: ollama pull llama3.2" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Starting services..." -ForegroundColor Yellow

# Clear previous PID file
if (Test-Path $PIDS_FILE) { Remove-Item $PIDS_FILE -Force }

# ── Environment variables for all processes ────────────────────────────────────
$env:DB_HOST        = "localhost"
$env:DB_PORT        = "5432"
$env:DB_NAME        = "safeopsdb"
$env:DB_USER        = "safeops"
$env:DB_PASS        = "safeops123"
$env:PROMETHEUS_URL = "http://localhost:9090"
$env:OLLAMA_URL     = "http://localhost:11434"
$env:OLLAMA_HOST    = "http://localhost:11434"
$env:OLLAMA_MODEL   = "llama3.2"
$env:LOG_LEVEL      = "INFO"

# ── Start function ─────────────────────────────────────────────────────────────
function Start-Agent {
    param(
        [string]$Name,
        [string]$Dir,
        [string]$Module,
        [int]$Port
    )
    $fullDir = Join-Path $SAFEOPS $Dir
    Write-Host "  Starting $Name (port $Port)..." -NoNewline
    $proc = Start-Process python `
        -ArgumentList "-m", "uvicorn", "$Module`:app", "--host", "0.0.0.0", "--port", "$Port", "--log-level", "warning" `
        -WorkingDirectory $fullDir `
        -PassThru `
        -WindowStyle Hidden
    Add-Content $PIDS_FILE "$($proc.Id) $Name"
    Write-Host " PID $($proc.Id)" -ForegroundColor Cyan
    return $proc
}

# [1] Backend
$backendDir = Join-Path $SAFEOPS "backend"
Write-Host "  Starting Backend (port 8000)..." -NoNewline
$p1 = Start-Process python `
    -ArgumentList "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--log-level", "warning" `
    -WorkingDirectory $backendDir `
    -PassThru -WindowStyle Hidden
Add-Content $PIDS_FILE "$($p1.Id) Backend"
Write-Host " PID $($p1.Id)" -ForegroundColor Cyan

Start-Sleep -Seconds 3

# Wait for backend to be ready before starting agents (they need the DB migrations)
$backendReady = Wait-URL "http://localhost:8000/health" "Backend" 30
if (-not $backendReady) {
    Write-Host "Backend failed to start. Aborting." -ForegroundColor Red
    Stop-Process -Id $p1.Id -Force -ErrorAction SilentlyContinue
    exit 1
}

# [2] Incident Agent
$p2 = Start-Agent "Incident Agent"     "incident-agent"    "incident_agent.main"    8001
Start-Sleep -Seconds 1

# [3] Root Cause Agent
$p3 = Start-Agent "Root Cause Agent"   "root-cause-agent"  "root_cause_agent.main"  8002
Start-Sleep -Seconds 1

# [4] Scoring Agent
$p4 = Start-Agent "Scoring Agent"      "scoring-agent"     "scoring_agent.main"     8003
Start-Sleep -Seconds 1

# [5] Coordinator Agent
$p5 = Start-Agent "Coordinator Agent"  "coordinator-agent" "coordinator_agent.main" 8004
Start-Sleep -Seconds 1

# [6] Sandbox Agent
$p6 = Start-Agent "Sandbox Agent"      "sandbox-agent"     "sandbox_agent.main"     8005
Start-Sleep -Seconds 1

# [7] Remediation Agent
$p7 = Start-Agent "Remediation Agent"  "remediation-agent" "remediation_agent.main" 8006
Start-Sleep -Seconds 1

# [8] Frontend (serve index.html via Python's built-in HTTP server on port 3000)
$frontendDir = Join-Path $SAFEOPS "frontend"
Write-Host "  Starting Frontend (port 3000)..." -NoNewline
$p8 = Start-Process python `
    -ArgumentList "-m", "http.server", "3000", "--directory", $frontendDir `
    -PassThru -WindowStyle Hidden
Add-Content $PIDS_FILE "$($p8.Id) Frontend"
Write-Host " PID $($p8.Id)" -ForegroundColor Cyan

Write-Host ""
Write-Host "Waiting for agents to become ready..." -ForegroundColor Yellow
Start-Sleep -Seconds 5

# ── Health check all services ──────────────────────────────────────────────────
Write-Host ""
Write-Host "Service Health Check:" -ForegroundColor Cyan
Wait-URL "http://localhost:8000/health" "Backend        :8000" 15 | Out-Null
Wait-URL "http://localhost:8001/health" "Incident Agent :8001" 15 | Out-Null
Wait-URL "http://localhost:8002/health" "Root Cause Agent:8002" 15 | Out-Null
Wait-URL "http://localhost:8003/health" "Scoring Agent  :8003" 15 | Out-Null
Wait-URL "http://localhost:8004/health" "Coordinator    :8004" 15 | Out-Null
Wait-URL "http://localhost:8005/health" "Sandbox Agent  :8005" 15 | Out-Null
Wait-URL "http://localhost:8006/health" "Remediation    :8006" 15 | Out-Null

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  SafeOpsAI is running!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Dashboard:   http://localhost:3000" -ForegroundColor Cyan
Write-Host "  Backend API: http://localhost:8000/docs" -ForegroundColor Cyan
Write-Host ""
Write-Host "  To inject a fault (slow DB queries):" -ForegroundColor Yellow
Write-Host "    Invoke-WebRequest -Uri http://localhost:8000/admin/fault -Method POST ``" -ForegroundColor DarkYellow
Write-Host "      -ContentType application/json ``" -ForegroundColor DarkYellow
Write-Host "      -Body '{""slow_queries"":true}'" -ForegroundColor DarkYellow
Write-Host ""
Write-Host "  To reset faults:" -ForegroundColor Yellow
Write-Host "    Invoke-WebRequest -Uri http://localhost:8000/admin/fault/reset -Method POST" -ForegroundColor DarkYellow
Write-Host ""
Write-Host "  To stop all services: .\stop-local.ps1" -ForegroundColor Yellow
Write-Host ""
Write-Host "  PIDs saved to: $PIDS_FILE" -ForegroundColor DarkGray
Write-Host ""
