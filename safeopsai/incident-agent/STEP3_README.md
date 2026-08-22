# SafeOpsAI — Step 3: Incident Agent

## What the Incident Agent does

The Incident Agent is a continuously-running Python service that:

1. Polls Prometheus every 5 seconds (configurable)
2. Evaluates 6 deterministic detection rules against the metrics
3. Requires a condition to be **continuously true** for a configured duration before opening an incident (prevents noisy false positives from single spikes)
4. Deduplicates: only one `OPEN` incident per condition/service exists at any time
5. Writes structured incident records to PostgreSQL
6. Automatically resolves incidents when the condition clears
7. Exposes its own `/health`, `/status`, `/incidents`, and `/metrics` endpoints
8. Emits Prometheus metrics about its own health

It is intentionally **rule-based and LLM-free** at this stage. The purpose is to produce reliable, reproducible incident records that the Root Cause Agent (Step 4) can later consume.

---

## Architecture

```
Fault Injection (Step 2)
        ↓
   Application (backend FastAPI)
        ↓
   Prometheus (scrapes /metrics every 5s)
        ↓
   Incident Agent  ←──── rules.yml (thresholds)
        ↓
   PostgreSQL (incidents table)
        ↓
   Root Cause Agent (Step 4) ← not built yet
```

The agent runs two concurrent asyncio tasks inside one process:

- **Polling loop** (`agent.py`) — Prometheus → detector → DB
- **FastAPI server** (`api.py`) — HTTP endpoints for health/status/incidents

---

## Files

```
incident-agent/
├── Dockerfile
├── requirements.txt
├── STEP3_README.md
├── incident_agent/
│   ├── __init__.py
│   ├── config.py          ← env vars + rules.yml loader
│   ├── rules.yml          ← all thresholds and for_seconds values
│   ├── models.py          ← IncidentType, Severity, MetricsSnapshot, Incident
│   ├── prometheus_client.py  ← async httpx wrapper for all 6 metric queries
│   ├── detector.py        ← state-machine rule evaluator
│   ├── db.py              ← asyncpg CRUD for incidents table
│   ├── agent.py           ← polling loop + self-metrics
│   ├── api.py             ← FastAPI app
│   └── main.py            ← entry point (asyncio.run)
└── tests/
    └── test_detector.py   ← 36 unit tests (no network, no DB)
```

---

## Detection rules

All rules are in `incident_agent/rules.yml`. Edit this file and restart the service — no rebuild needed (it is volume-mounted in compose).

| Rule | Condition | for_seconds | Severity | Incident type |
|---|---|---|---|---|
| `backend_down` | `up{job="safeops-backend"} == 0` | 15 | CRITICAL | `BACKEND_DOWN` |
| `database_down` | `up{job="postgres"} == 0` | 15 | CRITICAL | `DATABASE_DOWN` |
| `high_error_rate` | `rate(application_errors_total[1m]) > 0.5` | 30 | CRITICAL | `HIGH_ERROR_RATE` |
| `high_5xx_ratio` | `5xx ratio > 0.10` | 30 | HIGH | `HIGH_5XX_RATIO` |
| `high_latency` | `p95 request latency > 2.0s` | 30 | HIGH | `HIGH_LATENCY` |
| `slow_database` | `p95 DB query latency > 1.0s` | 30 | HIGH | `SLOW_DATABASE` |

### Sustained-condition logic

A condition must be **continuously true** for `for_seconds` before an incident opens. If the condition clears before `for_seconds` elapses, the timer resets.

```
t=0   backend_up=0  →  timer starts
t=10  backend_up=0  →  timer at 10s (< 15s, no incident)
t=14  backend_up=1  →  timer RESET (false positive prevented)
t=20  backend_up=0  →  NEW timer starts
t=36  backend_up=0  →  timer at 16s (>= 15s) → INCIDENT OPENED
```

### Deduplication

Only one `OPEN` incident exists per fingerprint (`<INCIDENT_TYPE>:<service>`). If the backend stays down for 10 minutes, exactly **one** incident is created, not 120.

```
fingerprint: BACKEND_DOWN:backend
```

---

## Configuration

### Environment variables

| Variable | Default | Description |
|---|---|---|
| `PROMETHEUS_URL` | `http://localhost:9090` | Prometheus base URL |
| `DB_HOST` | `localhost` | PostgreSQL host |
| `DB_PORT` | `5432` | PostgreSQL port |
| `DB_NAME` | `safeopsdb` | Database name |
| `DB_USER` | `safeops` | Database user |
| `DB_PASS` | `safeops123` | Database password |
| `API_PORT` | `8001` | Incident Agent API port |
| `LOG_LEVEL` | `INFO` | Logging level |
| `RULES_FILE` | `/app/incident_agent/rules.yml` | Path to rules file |

### rules.yml excerpt

```yaml
polling:
  poll_interval_seconds: 5
  prometheus_timeout_seconds: 5

incident_rules:
  backend_down:
    enabled: true
    for_seconds: 15
    severity: critical

  high_error_rate:
    enabled: true
    threshold: 0.5
    for_seconds: 30
    severity: critical
```

---

## How to start

```cmd
# Start the full stack including the Incident Agent
cd safeopsai
docker compose up -d

# Check the agent is running
curl http://localhost:8001/health

# View agent status
curl http://localhost:8001/status

# View open incidents
curl http://localhost:8001/incidents
```

---

## How to test each incident

### 1. Backend down

```cmd
cd safeopsai/fault-injection
python cli.py scenario backend-down --duration 30
```

Expected:
- Prometheus: `up{job="safeops-backend"}` → 0
- After 15s: `BackendDown` alert fires
- Incident Agent opens `BACKEND_DOWN:backend` (CRITICAL)
- After restore: incident resolved

### 2. Database down (soft)

```cmd
python cli.py scenario db-down --duration 60
```

Expected:
- `application_errors_total{error_type="db_unavailable_fault"}` rises
- After 30s: `HighErrorRate` alert fires
- Incident Agent opens `HIGH_ERROR_RATE:backend` (CRITICAL)

### 3. Database down (hard)

```cmd
python cli.py scenario db-down --mode hard --duration 45
```

Expected:
- `up{job="postgres"}` → 0
- After 15s: `DatabaseDown` alert fires
- Incident Agent opens `DATABASE_DOWN:database` (CRITICAL)

### 4. Slow database queries

```cmd
python cli.py scenario slow-db --duration 90
```

Expected:
- `db_query_duration_seconds` p95 > 1s
- After 30s: `SlowDatabaseQueries` alert fires
- Incident Agent opens `SLOW_DATABASE:database` (HIGH)

### 5. High error rate

```cmd
python cli.py scenario high-errors --duration 60
```

Expected:
- `application_errors_total` rate > 0.5/s
- After 30s: `HighErrorRate` alert fires
- Incident Agent opens `HIGH_ERROR_RATE:backend` (CRITICAL)

### 6. High request latency

The slow-db fault also raises P95 request latency. Run with traffic:

```cmd
python cli.py scenario slow-db --duration 90 --requests 200
```

Expected:
- `http_request_duration_seconds` p95 > 2s
- After 30s: `HighRequestLatency` alert fires
- Incident Agent opens `HIGH_LATENCY:backend` (HIGH)

---

## Querying incidents in PostgreSQL

```sql
-- All incidents
SELECT id, incident_type, service, severity, status,
       detected_at, resolved_at, mttr_seconds, fingerprint
FROM   incidents
ORDER  BY detected_at DESC;

-- Open incidents only
SELECT * FROM incidents WHERE status = 'open';

-- Metrics snapshot for a specific incident
SELECT metrics_snapshot FROM incidents WHERE id = 1;

-- Average MTTR by incident type
SELECT incident_type, AVG(mttr_seconds) AS avg_mttr_s
FROM   incidents
WHERE  status = 'resolved'
GROUP  BY incident_type;
```

---

## Example incident record

```json
{
  "id": 1,
  "incident_type": "SLOW_DATABASE",
  "service": "database",
  "fault_type": "slow_queries",
  "severity": "high",
  "status": "resolved",
  "fingerprint": "SLOW_DATABASE:database",
  "description": "P95 database query latency exceeded 1 second",
  "detection_source": "prometheus_rule",
  "detected_at": "2026-08-13T10:02:15.123Z",
  "resolved_at": "2026-08-13T10:03:48.456Z",
  "mttr_seconds": 93,
  "metrics_snapshot": {
    "backend_up": 1.0,
    "database_up": 1.0,
    "error_rate": 0.08,
    "request_rate": 4.2,
    "p95_request_latency": 4.81,
    "p95_db_latency": 3.72,
    "ratio_5xx": 0.02,
    "sampled_at": "2026-08-13T10:02:15.000Z"
  }
}
```

---

## Example log output

```
10:02:00  INFO      incident_agent.agent — Incident Agent starting…
10:02:01  INFO      incident_agent.db    — Database pool established
10:02:01  INFO      incident_agent.db    — Step 3 migration applied
10:02:01  INFO      incident_agent.agent — Polling Prometheus every 5s — 6 rules loaded

10:02:15  INFO      incident_agent.detector — Incident detected:
                    type=SLOW_DATABASE service=database severity=high
                    (condition true for 31.2s >= 30.0s)
10:02:15  INFO      incident_agent.db       — Incident inserted: id=1 type=SLOW_DATABASE

10:03:48  INFO      incident_agent.detector — Incident resolved:
                    type=SLOW_DATABASE service=database (condition false after 93.1s)
10:03:48  INFO      incident_agent.db       — Incident resolved by fingerprint:
                    id=1 fingerprint=SLOW_DATABASE:database mttr=93s
```

---

## Agent self-metrics (Prometheus)

The agent exposes its own metrics at `http://localhost:8001/metrics`:

| Metric | Type | Description |
|---|---|---|
| `incident_agent_polls_total` | Counter | Total polling cycles |
| `incident_agent_poll_errors_total` | Counter | Failed polling cycles |
| `incidents_detected_total` | Counter | Incidents opened (labels: incident_type, severity) |
| `incidents_resolved_total` | Counter | Incidents resolved (label: incident_type) |
| `active_incidents` | Gauge | Currently open incidents |
| `incident_detection_latency_seconds` | Histogram | Time from condition first seen to incident opened |

---

## Running unit tests

```cmd
cd safeopsai/incident-agent
pip install -r requirements.txt pytest
python -m pytest tests/test_detector.py -v
```

**36 tests, 0 failures.** Tests are pure unit tests — no Docker, no network, no database required.

### Test coverage

| Test class | Scenario |
|---|---|
| `TestBackendDownDetection` | Rule 1 fires/not-fires |
| `TestDatabaseDownDetection` | Rule 2 fires/not-fires |
| `TestHighErrorRateDetection` | Rule 3 — threshold boundary |
| `TestHigh5xxRatioDetection` | Rule 4 — threshold boundary |
| `TestHighLatencyDetection` | Rule 5 — threshold boundary |
| `TestSlowDatabaseDetection` | Rule 6 — threshold boundary |
| `TestSustainedCondition` | Timer reset, boundary, independent timers |
| `TestIncidentDeduplication` | No duplicate opens |
| `TestIncidentResolution` | Resolve on clear, active count |
| `TestPrometheusUnavailable` | None values → no incident |
| `TestInvalidPrometheusResponse` | Partial None values tolerated |
| `TestSeverityClassification` | All 6 severities, dynamic rules.yml |

---

## Database migration

The agent automatically runs `database/migrate_step3.sql` on startup, which adds four columns to the existing `incidents` table:

```sql
fingerprint       VARCHAR(200)   -- dedup key: "INCIDENT_TYPE:service"
incident_type     VARCHAR(100)   -- e.g. BACKEND_DOWN
detection_source  VARCHAR(100)   -- "prometheus_rule"
metrics_snapshot  JSONB          -- Prometheus values at detection time
```

The migration is idempotent — safe to run multiple times.

To apply manually:

```cmd
docker compose exec database psql -U safeops -d safeopsdb -f /dev/stdin < database/migrate_step3.sql
```

---

## Known limitations

1. **No LLM reasoning** — this is intentional. The Root Cause Agent (Step 4) adds LLM diagnosis on top of the incidents this agent creates.

2. **In-memory state** — the `ConditionState` dict is lost on container restart. A restarted agent will re-open any actively-firing incidents after `for_seconds` elapses again. This is acceptable for a local testbed.

3. **Prometheus dependency** — if Prometheus is unavailable for a polling cycle, that cycle is skipped silently. No false positives are generated from absent data.

4. **Single-node** — the agent runs as a single process. For production use, distributed locking around the dedup logic would be needed.

5. **No alertmanager integration** — the agent queries Prometheus metrics directly, not Alertmanager webhook events. This is intentional for simplicity and testability.

---

## What comes next (Step 4)

The Root Cause Agent will:

1. Read `OPEN` incidents from the `incidents` table
2. Fetch the `metrics_snapshot` and recent backend logs
3. Send them to a local LLM (Ollama/Llama 3) with a structured prompt
4. Write a structured JSON diagnosis to `agent_decisions` (already in the schema)
5. Update the incident `status` to `diagnosing`
