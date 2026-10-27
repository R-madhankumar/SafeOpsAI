# SafeOpsAI - Local Service and Readiness Inspector
# Read-Only Inspection for Windows (PowerShell 5.1 Compatible)
# Does NOT modify incidents, trigger faults, or invoke remediation.

param(
    [switch]$Json,
    [string]$ConfigPath = "safeopsai\.env"
)

$ErrorActionPreference = "Continue"

$REPO_ROOT = Split-Path -Parent $MyInvocation.MyCommand.Path
$SAFEOPS_DIR = Join-Path $REPO_ROOT "safeopsai"
$RUN_DIR = Join-Path $REPO_ROOT ".run"

if (-not $Json) {
    Write-Host ""
    Write-Host "==========================================================" -ForegroundColor Cyan
    Write-Host "  SafeOpsAI - Demo Readiness Gate and Service Inspector" -ForegroundColor Cyan
    Write-Host "==========================================================" -ForegroundColor Cyan
    Write-Host ""
}

$results = [ordered]@{
    timestamp = (Get-Date -Format "yyyy-MM-ddTHH:mm:ssZ")
    prerequisites = [ordered]@{}
    services = [ordered]@{}
    overall_status = "UNKNOWN"
    summary = [ordered]@{}
}

# 1. Check Python
$pythonCmd = "python"
$pyCheck = & $pythonCmd --version 2>&1
if ($LASTEXITCODE -eq 0) {
    $results.prerequisites["python"] = [ordered]@{
        status = "READY"
        version = "$pyCheck"
        details = "Python executable available in PATH"
    }
} else {
    $results.prerequisites["python"] = [ordered]@{
        status = "DEPENDENCY MISSING"
        version = $null
        details = "Python 3.10+ is required in PATH"
    }
}

# 2. Check PostgreSQL
$pgCheck = & $pythonCmd -c "import asyncpg, asyncio; asyncio.run(asyncpg.connect('postgresql://safeops:safeops123@localhost:5432/safeopsdb'))" 2>&1
if ($LASTEXITCODE -eq 0) {
    $results.prerequisites["postgresql"] = [ordered]@{
        status = "READY"
        host = "localhost:5432"
        database = "safeopsdb"
        details = "Database connected and reachable"
    }
} else {
    $results.prerequisites["postgresql"] = [ordered]@{
        status = "DEPENDENCY MISSING"
        host = "localhost:5432"
        database = "safeopsdb"
        details = "Cannot connect to safeopsdb (safeops:safeops123). Please ensure PostgreSQL is running."
    }
}

# 3. Check Ollama (Optional for RCA, fallback available)
try {
    $ollamaResp = Invoke-WebRequest -Uri "http://localhost:11434/api/tags" -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
    $results.prerequisites["ollama"] = [ordered]@{
        status = "READY"
        host = "http://localhost:11434"
        details = "Ollama is running; local LLM inference available"
    }
} catch {
    $results.prerequisites["ollama"] = [ordered]@{
        status = "OPTIONAL SERVICE UNAVAILABLE"
        host = "http://localhost:11434"
        details = "Ollama not reachable; Root Cause Agent will automatically use rule-based fallback"
    }
}

# 4. Check Prometheus (Optional for live metrics scrape)
try {
    $promResp = Invoke-WebRequest -Uri "http://localhost:9090/-/healthy" -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
    $results.prerequisites["prometheus"] = [ordered]@{
        status = "READY"
        host = "http://localhost:9090"
        details = "Prometheus server reachable"
    }
} catch {
    $results.prerequisites["prometheus"] = [ordered]@{
        status = "OPTIONAL SERVICE UNAVAILABLE"
        host = "http://localhost:9090"
        details = "Prometheus not reachable; core pipeline and decision lab operate independently"
    }
}

# Function to check HTTP endpoints
function Test-HttpEndpoint {
    param(
        [string]$Name,
        [string]$Url,
        [int]$Port,
        [string]$Category = "MANDATORY"
    )
    $entry = [ordered]@{
        name = $Name
        port = $Port
        url = $Url
        category = $Category
        status = "NOT RUNNING"
        http_code = $null
        details = ""
    }

    try {
        $resp = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
        $entry.http_code = $resp.StatusCode
        if ($resp.StatusCode -ge 200 -and $resp.StatusCode -lt 400) {
            $entry.status = "READY"
            $entry.details = "Service healthy and responding"
        } else {
            $entry.status = "CONFIGURATION ERROR"
            $entry.details = "HTTP " + $resp.StatusCode + " returned"
        }
    } catch {
        $portBound = Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue | Where-Object { $_.State -eq "Listen" }
        if ($portBound) {
            $entry.status = "PORT CONFLICT"
            $entry.details = "Port " + $Port + " is occupied by PID " + $portBound[0].OwningProcess + ", but endpoint did not respond"
        } else {
            $entry.status = "NOT RUNNING"
            $entry.details = "Service process is not listening on port " + $Port
        }
    }
    return $entry
}

# Services check
$servicesToCheck = @(
    @{ Name="FastAPI Backend"; Url="http://localhost:8000/health"; Port=8000; Category="MANDATORY" },
    @{ Name="Incident Agent"; Url="http://localhost:8001/health"; Port=8001; Category="MANDATORY" },
    @{ Name="Root Cause Agent"; Url="http://localhost:8002/health"; Port=8002; Category="MANDATORY" },
    @{ Name="Scoring Agent"; Url="http://localhost:8003/health"; Port=8003; Category="MANDATORY" },
    @{ Name="Coordinator Agent"; Url="http://localhost:8004/health"; Port=8004; Category="MANDATORY" },
    @{ Name="Sandbox Agent"; Url="http://localhost:8005/health"; Port=8005; Category="MANDATORY" },
    @{ Name="Remediation Agent"; Url="http://localhost:8006/health"; Port=8006; Category="MANDATORY" },
    @{ Name="Frontend UI"; Url="http://localhost:3000"; Port=3000; Category="MANDATORY" }
)

$mandatoryTotal = 0
$mandatoryReady = 0

foreach ($s in $servicesToCheck) {
    $res = Test-HttpEndpoint -Name $s.Name -Url $s.Url -Port $s.Port -Category $s.Category
    $results.services[$s.Name] = $res
    if ($s.Category -eq "MANDATORY") {
        $mandatoryTotal++
        if ($res.status -eq "READY") { $mandatoryReady++ }
    }
}

if ($mandatoryReady -eq $mandatoryTotal -and $results.prerequisites["postgresql"].status -eq "READY") {
    $results.overall_status = "READY"
} elseif ($mandatoryReady -gt 0) {
    $results.overall_status = "PARTIALLY RUNNING"
} else {
    $results.overall_status = "NOT RUNNING"
}

$results.summary = [ordered]@{
    mandatory_ready = "$mandatoryReady / $mandatoryTotal"
    dashboard_url = "http://localhost:3000"
    logs_directory = (Join-Path $REPO_ROOT "logs")
    run_directory = $RUN_DIR
}

if ($Json) {
    $results | ConvertTo-Json -Depth 4
} else {
    Write-Host "1. PREREQUISITES AND DEPENDENCIES:" -ForegroundColor Yellow
    foreach ($k in $results.prerequisites.Keys) {
        $item = $results.prerequisites[$k]
        $color = if ($item.status -eq "READY") { "Green" } elseif ($item.status -like "*OPTIONAL*") { "DarkYellow" } else { "Red" }
        Write-Host "  - $k : [$($item.status)]" -ForegroundColor $color -NoNewline
        Write-Host " - $($item.details)"
    }

    Write-Host ""
    Write-Host "2. SERVICE READINESS GATES:" -ForegroundColor Yellow
    foreach ($k in $results.services.Keys) {
        $s = $results.services[$k]
        $color = switch ($s.status) {
            "READY" { "Green" }
            "NOT RUNNING" { "DarkGray" }
            "PORT CONFLICT" { "Red" }
            default { "Yellow" }
        }
        Write-Host "  - $($s.name) (: $($s.port)): [$($s.status)]" -ForegroundColor $color -NoNewline
        if ($s.details) { Write-Host " - $($s.details)" -ForegroundColor DarkGray } else { Write-Host "" }
    }

    Write-Host ""
    Write-Host "3. DEMO READINESS SUMMARY:" -ForegroundColor Yellow
    $statusColor = if ($results.overall_status -eq "READY") { "Green" } else { "Yellow" }
    Write-Host "  Overall Readiness: " -NoNewline
    Write-Host "[$($results.overall_status)]" -ForegroundColor $statusColor
    Write-Host "  Mandatory Services Online: $($results.summary.mandatory_ready)"
    Write-Host "  Dashboard URL: $($results.summary.dashboard_url)"
    Write-Host ""

    if ($results.overall_status -ne "READY") {
        Write-Host "NEXT ACTIONS TO LAUNCH:" -ForegroundColor Cyan
        if ($results.prerequisites["postgresql"].status -ne "READY") {
            Write-Host "  1. Start PostgreSQL on localhost:5432 with database safeopsdb" -ForegroundColor White
        }
        Write-Host "  2. Execute .\start-safeopsai.ps1 to start the platform" -ForegroundColor White
        Write-Host ""
    }
}
