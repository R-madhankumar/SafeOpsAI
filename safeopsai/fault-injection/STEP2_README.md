# SafeOpsAI — Step 2: Fault Injection Framework

## Why fault injection?

The Incident Agent (Step 3) needs real incidents to detect and diagnose.
Without a controlled way to break things, every test run is different and
non-reproducible — you cannot measure MTTR or compare strategies against
a baseline.

This framework creates **repeatable, structured incidents** in the local
Docker environment. Every fault can be:

- triggered in one command
- held for a configurable duration
- cleared cleanly, returning the system to its baseline state
- logged to a structured JSONL file for the evaluation harness (Step 11)

---

## Prerequisites

```
Python 3.12+
Docker Desktop running
docker compose up (the Step 1 stack must be running)
```

Install dependencies (run once from the `fault-injection/` directory):

```cmd
pip install -r requirements.txt
```

---

## Quick start

```cmd
cd safeopsai/fault-injection

# Check the stack is healthy before injecting anything
python cli.py status

# Run a complete scenario (recommended first test)
python cli.py scenario slow-db --duration 60
```

---

## Available fault scenarios

### 1. `backend-down` — Container crash

Stops the `safeops-backend` Docker container.

| | |
|---|---|
| **Fault type** | `container_down` |
| **Application behaviour** | All HTTP requests fail immediately (connection refused) |
| **Prometheus metric** | `up{job="safeops-backend"}` drops to 0 |
| **Alert** | `BackendDown` fires after 15 s |
| **Recovery** | Container restarted; health probe polled until HTTP 200 |

```cmd
python cli.py backend-down                  # stop now
python cli.py backend-down --restore        # restart
python cli.py backend-down --duration 30    # stop, wait 30 s, auto-restore
python cli.py backend-down --kill           # SIGKILL instead of graceful stop
```

---

### 2. `cpu-stress` — CPU saturation

Runs `stress-ng` inside the `safeops-backend` container.
`stress-ng` is pre-installed in the backend Docker image.

| | |
|---|---|
| **Fault type** | `cpu_stress` |
| **Application behaviour** | Request latency increases as the Python worker competes for CPU |
| **Prometheus metric** | `http_request_duration_seconds` p95 climbs |
| **Alert** | `HighRequestLatency` may fire depending on load |
| **Recovery** | stress-ng exits automatically after `--duration` elapses |

```cmd
python cli.py cpu-stress                            # 2 workers, 60 s (defaults)
python cli.py cpu-stress --workers 4 --duration 90
```

---

### 3. `slow-db` — Slow database queries

Activates the `slow_queries` flag via `POST /admin/fault`.
The backend adds a random 2–5 s delay before every database query.

| | |
|---|---|
| **Fault type** | `slow_queries` |
| **Application behaviour** | All `/items` and `/stats` requests hang for 2–5 s |
| **Prometheus metric** | `db_query_duration_seconds` p95 > 1 s; `http_request_duration_seconds` p95 > 2 s |
| **Alert** | `SlowDatabaseQueries` and `HighRequestLatency` fire after ~30 s |
| **Recovery** | `POST /admin/fault/reset` — immediate, no container restart |

```cmd
python cli.py slow-db                  # enable (stays on until you clear it)
python cli.py slow-db --restore        # clear immediately
python cli.py slow-db --duration 45    # enable, wait 45 s, auto-clear
```

---

### 4. `high-errors` — High HTTP error rate

Activates the `high_error_rate` flag. The backend middleware randomly
returns HTTP 500 on ~50% of all incoming requests.

| | |
|---|---|
| **Fault type** | `high_error_rate` |
| **Application behaviour** | ~50% of requests return HTTP 500 |
| **Prometheus metric** | `application_errors_total` rises; `http_requests_total{status_code="500"}` rises |
| **Alert** | `HighErrorRate` (errors/s > 0.5) and `High5xxRate` (5xx ratio > 10%) fire after ~30 s |
| **Recovery** | `POST /admin/fault/reset` — immediate |

```cmd
python cli.py high-errors                  # enable
python cli.py high-errors --restore        # clear
python cli.py high-errors --duration 30    # enable + auto-clear after 30 s
```

---

### 5. `db-down` — Database unavailable

Two modes:

**Soft** (default) — API flag. The backend rejects all DB calls with 503.
Fast and fully reversible without Docker operations.

**Hard** — Stops the `safeops-database` container. The backend loses its
connection pool and returns errors. More realistic.

| | |
|---|---|
| **Fault type** | `db_unavailable` |
| **Application behaviour** | `/items`, `/stats`, `/ready` return HTTP 503 |
| **Prometheus metric (soft)** | `application_errors_total{error_type="db_unavailable_fault"}` rises |
| **Prometheus metric (hard)** | `up{job="postgres"}` drops to 0 |
| **Alert (soft)** | `HighErrorRate`, `High5xxRate` |
| **Alert (hard)** | `DatabaseDown` (15 s) + above |
| **Recovery** | Soft: `POST /admin/fault/reset`. Hard: container restarted automatically |

```cmd
python cli.py db-down                          # soft mode, enable
python cli.py db-down --restore                # soft mode, restore
python cli.py db-down --mode hard              # stop database container
python cli.py db-down --mode hard --restore    # restart database container
python cli.py db-down --duration 30            # auto-restore after 30 s
```

> **Safety**: The PostgreSQL data volume (`postgres-data`) is never deleted.
> Hard mode only stops and starts the container.

---

### 6. `bad-db-config` — Database misconfiguration

Restarts the backend container with `DB_HOST=invalid-db-host`.
The backend starts, cannot connect to the database, and begins returning
errors on all DB-touching endpoints. The original Compose configuration
is fully restored by the `--restore` command.

| | |
|---|---|
| **Fault type** | `bad_db_config` |
| **Application behaviour** | Connection pool retries on startup; all DB endpoints return 500/503 |
| **Prometheus metric** | `application_errors_total` rises |
| **Alert** | `HighErrorRate`, `High5xxRate` |
| **Recovery** | `docker compose up -d backend` restores the original env |

```cmd
python cli.py bad-db-config                # inject
python cli.py bad-db-config --restore      # restore correct config
python cli.py bad-db-config --duration 30  # auto-restore after 30 s
```

> **Safety**: Source files are never modified. Only the running container's
> environment is changed. `--restore` always runs `docker compose up -d backend`
> to put the container back under Compose management with the correct config.

---

## Traffic generator

Some Prometheus alerts require sustained traffic to fire (rate functions
need data points over a time window). Use the traffic generator to ensure
alerts trigger during fault windows.

```cmd
python cli.py traffic                              # 120 req @ 0.5s interval (defaults)
python cli.py traffic --requests 200 --interval 1
python cli.py traffic --verbose                    # print every request

# Or run directly:
python traffic/generator.py --requests 60 --interval 0.5
```

Example output:

```
────────────────────────────────────────────────────
  Traffic Generator Summary
────────────────────────────────────────────────────
  Total requests  : 120
  Successful      : 82
  Failed          : 38
  Error rate      : 31.7%
  Avg latency     : 1.42s
  P50 latency     : 0.58s
  P95 latency     : 4.91s
  Max latency     : 5.12s
  Wall time       : 63.40s
────────────────────────────────────────────────────
```

---

## Scenario mode (recommended for evaluation runs)

The `scenario` command runs a full end-to-end test:

1. Verify backend and Prometheus are reachable
2. Start background traffic generation
3. Inject the fault
4. Hold for `--duration` seconds
5. Clear the fault
6. Verify recovery
7. Print summary + metrics snapshot

```cmd
python cli.py scenario slow-db
python cli.py scenario slow-db --duration 90
python cli.py scenario high-errors --duration 60 --requests 200
python cli.py scenario db-down --mode hard --duration 45
python cli.py scenario backend-down --duration 30 --skip-traffic
python cli.py scenario cpu-stress --workers 4 --duration 60
python cli.py scenario bad-db-config --duration 30
```

Example output:

```
════════════════════════════════════════════════════
  SafeOpsAI Fault Injection — SLOW-DB
════════════════════════════════════════════════════
  Scenario : slow-db
  Duration : 60s
  Backend  : http://localhost:8000

  [1/7] Checking backend (HTTP 200 — service='backend') ... PASS
  [2/7] Checking Prometheus (HTTP 200 — Prometheus healthy) .. PASS
  [3/7] Starting traffic (120 req @ 0.5s) ................... PASS
  [4/7] Injecting 'slow-db' fault ........................... PASS
  [5/7] Holding fault for 60s ..........
  [6/7] Clearing fault .............................. PASS
  [7/7] Verifying recovery ..................... PASS

  Scenario 'slow-db' complete
  Status     : completed
  ...
  Next step: Check Prometheus/Grafana for the incident.
  Grafana   : http://localhost:3001  (admin / safeops123)
  Prometheus: http://localhost:9090/alerts
```

---

## Running as a Docker container

The fault injector can also run as a container inside the Compose network,
which is useful when you want scenario automation without a local Python
install.

```cmd
# One-off scenario run
docker compose --profile fault run --rm fault-injector scenario slow-db --duration 60

# Interactive shell
docker compose --profile fault run --rm --entrypoint bash fault-injector
```

The `fault` profile keeps this container out of the normal `docker compose up`
so it does not start automatically.

---

## Utility commands

```cmd
# Show backend health, current fault state, Prometheus status, and event log
python cli.py status

# Clear all active soft faults immediately (safe recovery command)
python cli.py reset-all
```

---

## Event log

Every fault inject, clear, and scenario completion is appended to:

```
fault-injection/logs/fault_events.jsonl
```

Each line is a JSON object:

```json
{
  "event":            "scenario_completed",
  "scenario":         "slow-db",
  "fault_type":       "slow_queries",
  "started_at":       "2026-08-13T10:00:00.000000+00:00",
  "ended_at":         "2026-08-13T10:01:02.123456+00:00",
  "duration_seconds": 60,
  "status":           "completed",
  "details":          { ... }
}
```

The evaluation harness (Step 11) reads this log to calculate MTTR,
downtime, and decision latency across multiple runs.

> The injector does **not** write rows to the `incidents` database table.
> That is the Incident Agent's responsibility (Step 3).

---

## What Prometheus should show

After running any scenario, check these Grafana panels or Prometheus queries:

| Scenario | Query to verify |
|---|---|
| `slow-db` | `histogram_quantile(0.95, rate(db_query_duration_seconds_bucket[2m]))` |
| `high-errors` | `rate(application_errors_total[1m])` |
| `db-down` | `rate(http_requests_total{status_code="503"}[1m])` |
| `backend-down` | `up{job="safeops-backend"}` |
| `cpu-stress` | `histogram_quantile(0.95, rate(http_request_duration_seconds_bucket[2m]))` |
| `bad-db-config` | `rate(application_errors_total[1m])` |

Firing alerts are visible at: **http://localhost:9090/alerts**

---

## How these scenarios feed into later steps

| Step | Uses fault injection for |
|---|---|
| **Step 3 — Incident Agent** | Polls Prometheus for firing alerts produced by these scenarios |
| **Step 4 — Root Cause Agent** | Reads logs + metrics produced during fault windows |
| **Step 7 — Coordinator** | Chooses remediation actions; the injector's `restore()` methods are the templates for those actions |
| **Step 11 — Evaluation Harness** | Replays the full fault suite 20–30 times; reads `fault_events.jsonl` to compute MTTR, downtime, rollback rate |

---

## Restoring the system manually

If a fault injector process is killed mid-run and leaves a fault active:

```cmd
# Clear all API-based faults (slow_queries, high_error_rate, db_unavailable)
python cli.py reset-all

# If the backend container was stopped
docker compose up -d backend

# If the database container was stopped
docker compose up -d database

# If bad-db-config left a misconfigured container
docker compose up -d --force-recreate backend

# Nuclear option — rebuild everything from scratch (data is preserved)
docker compose down
docker compose up -d
```
