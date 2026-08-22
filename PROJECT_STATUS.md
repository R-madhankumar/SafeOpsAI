# SafeOpsAI — Project Status

Status record as of **2026-08-20**.

---

## Project Overview

SafeOpsAI is a multi-agent AIOps system designed to:

1. detect cloud incidents
2. diagnose root cause
3. score remediation candidates (cost / reliability / security)
4. select the best remediation using deterministic MCDM
5. validate the remediation in a sandbox
6. remediate production
7. rollback if remediation fails
8. evaluate the system through controlled fault injection

This is a **team / final-year project**. The repository is
`https://github.com/R-madhankumar/SafeOpsAI.git`. This document is a permanent
status record; it reflects the **actual verified state of the working tree and
the live stack**, not the plan described in the project brief.

> **Important correction to the original task brief.** The brief assumed
> Phases 2 and 3 were not started. Verification of the actual repository and
> live runs shows that Phase 2 (scoring agents, Step 5) and Phase 3
> (deterministic MCDM coordinator, Step 6) **are implemented and were tested
> live today**. Phases 4–7 are **not implemented**. Nothing below claims work
> that was not verified from the code or live behaviour.

---

## Existing System Before Today's Work

Components that existed in the team repository baseline (commit `96072e3`,
`Add project summary and tech stack justification for SafeOpsAI`) and were
preserved today:

| Component | Purpose (verified) |
|---|---|
| `frontend` | Nginx serving static HTML + Chart.js dashboard (port 3000 → 80) |
| `backend` | FastAPI business app; endpoints `/health`, `/ready`, `/metrics`, `/items`, `/admin/fault` (port 8000) |
| `database` | PostgreSQL 16, DB `safeopsdb`; tables `items`, `incidents`, `agent_decisions`, `remediation_actions` (from `database/init.sql`) |
| `prometheus` | Prometheus v2.52; scrape configs + `monitoring/alert_rules.yml` |
| `grafana` | Grafana 10.4, provisioned dashboards (port 3001) |
| `postgres-exporter` | Exposes DB metrics to Prometheus |
| `incident-agent` | Step 3; polls Prometheus every 5 s, applies `rules.yml`, creates `incidents` rows (port 8001) |
| `fault-injector` | Step 2; profile-gated scenarios (`slow-db`, `high-errors`, `backend-down`, `cpu-stress`, `db-down`, `bad-db-config`); uses docker.sock |
| `root-cause-agent` source | Step 4 code (agent, analyzer, evidence, llm_client, prompt_builder, db, api, models) **existed as source only** — no `Dockerfile`, not wired into `docker-compose.yml`, not runnable via compose |

Existing database migrations before today: `database/init.sql`,
`database/migrate_step3.sql`, `database/migrate_step4.sql`.

---

# Today's Work — Phase 1

## Root Cause Agent

Verified changes made today:

* **`root-cause-agent/Dockerfile`** — new. `python:3.12-slim`, installs `curl`,
  copies `root_cause_agent/`, creates `/app/database`, runs as non-root user,
  healthcheck on `/health`, entrypoint `python -m root_cause_agent.main`.
* **Docker Compose integration** — two new services in `docker-compose.yml`:
  * `ollama` (`ollama/ollama:latest`, container `safeops-ollama`, port 11434,
    volume `ollama-models` for model persistence).
  * `root-cause-agent` (container `safeops-root-cause-agent`, port 8002,
    env `OLLAMA_URL`, `OLLAMA_MODEL`, `PROMETHEUS_URL`, DB credentials; mounts
    `migrate_step4.sql`, `config.yml`, shared `topology.yml`; healthcheck on
    `/health`).
* **Selected Ollama model**: `llama3.2` (pulled into the `ollama-models`
  volume).
* **LangChain / LangGraph**: **NOT used.** Verified — `llm_client.py` calls
  Ollama directly over HTTP via `httpx`. The brief's plan mentioned
  LangChain/LangGraph; that is not implemented.
* **How incidents are consumed**: polls the `rca_queue` view (open incidents
  without a `root_cause` decision).
* **How Prometheus metric context is obtained**: queries Prometheus
  (`PROMETHEUS_URL`) for the incident's service metrics and stores a
  `metrics_snapshot`.
* **How root cause is generated**: builds a prompt (prompt_builder) from the
  incident + metric snapshot, sends it to Ollama (`llm_client`), parses
  structured output; falls back to rule-based diagnosis if Ollama is
  unavailable or returns invalid JSON.
* **How confidence is generated**: the LLM emits a `confidence` value; stored
  with the diagnosis. It is **not yet calibrated** (see limitations).
* **How the decision is stored**: one `agent_decisions` row per incident with
  `agent_name='root_cause'`, `reasoning`, and `raw_output` JSONB containing the
  diagnosis + `remediation_candidates`.
* **RCA DB views added today**: `rca_queue` (migration `migrate_step4.sql`,
  pre-existing) and `rca_results` (added today) — plus `scoring_queue` /
  `scoring_results` (Step 5) and `coordinator_queue` (Step 6).
* **Bug fixes in shared RCA/incident code**:
  * Migration path resolution: `Path(__file__).parent.parent.parent` →
    `.parent.parent` in `root-cause-agent/root_cause_agent/db.py` and
    `incident-agent/incident_agent/db.py` (the container layout is
    `/app/database/`, not `/app/root_cause_agent/database`).
  * `_as_dict()` JSONB normaliser in RCA `db.py` (asyncpg returns jsonb as a
    string; the API was returning raw strings).
* **Environment / configuration added**: `OLLAMA_URL`, `OLLAMA_MODEL`,
  `PROMETHEUS_URL`, DB env vars for the RCA; compose host port for Postgres
  moved **5432 → 5433** (host 5432 is used by an unrelated project).
* **Prometheus**: `monitoring/prometheus.yml` gained scrape jobs for
  `root-cause-agent`, `scoring-agent`, `coordinator-agent`.
* **Health checks / logging / dependencies**: Dockerfile HEALTHCHECK on
  `/health`; structured logging via `logging`; dependencies pinned in
  `root-cause-agent/requirements.txt` (fastapi, uvicorn, asyncpg, httpx,
  pyyaml, prometheus-client, pydantic).

---

# Phase 1 Testing

Tests run live today against the running stack. `PASS` = verified in this
session; nothing is marked PASS without evidence.

| # | Test | Result |
|---|---|---|
| 1 | `docker compose up -d --build` starts the stack | **PASS** |
| 2 | Ollama container starts (`safeops-ollama`) | **PASS** |
| 3 | Ollama model `llama3.2` available | **PASS** |
| 4 | Existing backend starts and serves `/health` | **PASS** |
| 5 | Existing incident-agent starts (port 8001) | **PASS** |
| 6 | `slow_queries` fault injected via `POST /admin/fault` | **PASS** |
| 7 | Traffic generated against `/items` (background loop) | **PASS** |
| 8 | Prometheus scrapes the abnormal condition | **PASS** (incident-agent detects from Prometheus data) |
| 9 | incident-agent created an `incidents` row (`SLOW_DATABASE`) | **PASS** |
| 10 | root-cause-agent processed the incident | **PASS** |
| 11 | Ollama generated the RCA output | **PASS** |
| 12 | RCA decision stored in `agent_decisions` | **PASS** (`cause=slow_queries`, `root=database`, `confidence=0.80`) |

Example commands used during testing:

```powershell
docker compose up -d --build
docker exec safeops-ollama ollama pull llama3.2
docker exec safeops-database psql -U safeops -d safeopsdb -c "SELECT id, status FROM incidents ORDER BY id DESC;"
docker exec safeops-database psql -U safeops -d safeopsdb -c "SELECT agent_name, score, reasoning FROM agent_decisions WHERE incident_id=<id>;"
Invoke-RestMethod -Method Post -Uri http://localhost:8000/admin/fault -ContentType 'application/json' -Body '{"slow_queries": true}'
```

---

# Current Phase 1 Result

* **What works**: full detect → diagnose → persist chain for the `slow_queries`
  fault; RCA service runs in compose; Ollama integration works; fallback to
  rule-based diagnosis if Ollama is down; JSONB parsing fixed.
* **What does not work / partially works**:
  * RCA latency is variable: ~50 s warm, ~120 s after Ollama reloads an
    unloaded model (model unloads after ~5 min idle). Not a failure, but
    relevant for MTTR.
  * The `high_error_rate` fault also breaks Prometheus `/metrics` scraping,
    producing `BACKEND_DOWN` incidents, and the LLM sometimes predicts
    `database_unavailable` for a `high_error_rate` ground truth. Useful
    mismatch data, but a known quirk.
* **Known limitations / bugs / warnings**:
  * `Calibration evaluation is NOT YET COMPLETED.` The RCA `confidence` is
    stored but not yet calibrated against ground truth.
  * Ground-truth comparison is NOT implemented yet (no evaluation harness).
  * Pre-existing: the frontend container reports `unhealthy` because
    `nginx:alpine` lacks `wget` for its healthcheck, yet serves correctly
    (`/` and `/health` return 200).
* **Configuration that still needs attention**: confidence calibration
  methodology (pending design decision); candidate set policy was resolved
  during Phase 3 (see below).

---

# Today's Work — Phase 2 (Scoring Agents, Step 5)

* **Implemented today** (verified). New `scoring-agent/` service, port 8003,
  with three deterministic, rule-based scorers — cost, reliability, security —
  each scoring every candidate 0–10 (**higher is better on all criteria**;
  cost 10 = cheapest).
* Transparency: every score row carries a `reasoning` string and a
  `components` breakdown in `raw_output` (e.g. base score, downtime penalty,
  target-match bonus, reversibility bonus).
* **Candidate remediation action list**: the RCA's `remediation_candidates`
  plus a **canonical action universe per root-cause service** (database →
  `clear_fault / restart_service / restart_database / scale_up`; backend →
  `clear_fault / restart_service / scale_up / redeploy`). RCA-proposed
  candidates are flagged `proposed_by_rca=true` in the stored evidence.
* **Migration**: `database/migrate_step5.sql` adds `scoring_at`,
  `scoring_queue`, `scoring_results`.
* **Verified live**: incident 10 scored in **12 ms** — cost 9.50, reliability
  9.00, security 9.50, three auditable rows written.

---

# Today's Work — Phase 3 (Deterministic MCDM Coordinator, Step 6)

* **Implemented today** (verified). New `coordinator-agent/` service, port
  8004. **Pure deterministic Python — no LLM.** Core in `mcdm.py`:
  `weighted_sum()` (default) and `topsis()` behind a method flag.
* **Configurable weights**: stored in DB table `coordinator_config`, seeded
  cost 0.3 / reliability 0.5 / security 0.2 (reliability-favouring).
* **Runtime weight changes**: `POST /weights` on :8004 swaps weights without
  restart; every decision snapshots the `weights_used`.
* **Candidate ranking / winner selection**: `weighted_sum` (default) and
  `topsis` via `GET /simulate` (read-only sweeps/ablation).
* **Audit logging**: one `agent_decisions` row (`agent_name='coordinator'`)
  per incident with `raw_output` containing `weights_used`, `ranking`,
  `winner`, `formula`.
* **Migration**: `database/migrate_step6.sql` adds `coordinated_at`,
  `coordinator_config`, `coordinator_queue`.
* **Verified live**:
  * Incident 11 (`SLOW_DATABASE`): winner `clear_fault`, metric 9.25.
  * Incident 14 (`HIGH_5XX_RATIO`): 4-candidate ranking —
    `clear_fault 9.75 > restart_service 8.625 > scale_up 6.45 > redeploy 4.60`
    (2 s end-to-end after scoring).
  * TOPSIS ablation on incident 14: closeness `1.0 / 0.7496 / 0.3291 / 0.0`.
  * Weight-sensitivity sweeps via `/simulate` change margins (cost-heavy,
    reliability-heavy) with stable ordering.
  * One coordinator bug found and fixed today: `_candidate_rows(conn, ...)`
    was called after the `pool.acquire()` block released the connection.

---

# What Has NOT Been Implemented Yet

## Phase 2 — Cost/Reliability/Security Scoring

* Cost scorer — **DONE (implemented + verified today)**
* Reliability scorer — **DONE (implemented + verified today)**
* Security scorer — **DONE (implemented + verified today)**
* Candidate remediation action list — **DONE (canonical set, verified today)**
* Score logging — **DONE (verified today)**

## Phase 3 — Deterministic MCDM Coordinator

* weighted-sum coordinator — **DONE (verified today)**
* configurable weights — **DONE (verified today)**
* runtime weight changes — **DONE (verified today)**
* candidate ranking — **DONE (verified today)**
* winner selection — **DONE (verified today)**
* audit logging — **DONE (verified today)**
* TOPSIS alternative — **DONE (verified today)**

## Phase 4 — Sandbox Validator — **NOT STARTED**

* isolated service replica — NOT implemented
* remediation execution in sandbox — NOT implemented
* health check — NOT implemented
* PASS/FAIL result — NOT implemented
* next-best candidate fallback — NOT implemented
* no-safe-remediation escalation — NOT implemented
* `remediation_actions` logging — table exists, no writes yet

## Phase 5 — Automatic Remediation and Rollback — **NOT STARTED**

* production remediation — NOT implemented
* post-remediation monitoring — NOT implemented
* regression detection — NOT implemented
* automatic rollback — NOT implemented
* last-known-good state — NOT implemented
* remediation outcome logging — NOT implemented

## Phase 6 — Dashboard — **NOT STARTED**

* services status / active incidents / root cause / confidence / agent scores /
  coordinator score / selected fix / sandbox result / remediation result /
  MTTR / recovery rate — NOT implemented (frontend is the pre-existing basic
  dashboard only)

## Phase 7 — Evaluation Harness — **NOT STARTED**

* repeated fault injection — NOT implemented
* naive baseline — NOT implemented
* SafeOpsAI comparison — NOT implemented
* MTTR / downtime / recovery success rate / rollback rate / decision latency /
  wrong-remediation rate — NOT implemented
* CSV export — NOT implemented
* calibration curves — NOT implemented
* weight sensitivity — partially possible via `coordinator-agent /simulate`,
  no automated harness yet
* statistical testing — NOT implemented

---

# Project Architecture — Current State

```
Frontend (nginx, :3000)
   │
   ▼
FastAPI Backend (:8000)
   │
   ▼
PostgreSQL (safeopsdb, host :5433)
   ▲              ▲              ▲
   │              │              │
incident-agent   root-cause     scoring-agent    coordinator-agent
(:8001)          agent (:8002)  (:8003)          (:8004)
   │              │              │
   ▼              ▼              ▼
Prometheus (:9090) ──► Grafana (:3001)
   ▲
Ollama (llama3.2, :11434)  ◄── root-cause-agent (direct HTTP, no LangChain)
```

Only components that exist are shown. `fault-injector` (docker.sock) is
profile-gated and not part of normal `compose up`.

---

# Technology Stack

Technologies actually present (verified from the repository):

* Python 3.12 (agent containers) — 3.14 used only on the host for local unit
  checks
* FastAPI + uvicorn (backend and all agents)
* asyncpg / PostgreSQL 16
* Docker + Docker Compose
* Prometheus v2.52 + Grafana 10.4 + postgres-exporter
* Ollama with **llama3.2** (local LLM, served in a container)
* httpx (RCA → Ollama). **No LangChain, no LangGraph.**

---

# How to Run Current Project

All commands below were verified against the repository / live stack.

```powershell
# Start the full stack (fault-injector is profile-gated, not included)
docker compose up -d --build

# Check containers
docker ps --format "table {{.Names}}\t{{.Status}}"

# Backend health
Invoke-RestMethod http://localhost:8000/health

# Root Cause Agent
Invoke-RestMethod http://localhost:8002/health
Invoke-RestMethod http://localhost:8002/status

# Scoring Agent
Invoke-RestMethod http://localhost:8003/health
Invoke-RestMethod http://localhost:8003/status

# Coordinator Agent
Invoke-RestMethod http://localhost:8004/health
Invoke-RestMethod http://localhost:8004/status
Invoke-RestMethod http://localhost:8004/weights

# Ollama
docker exec safeops-ollama ollama list
docker exec safeops-ollama ollama pull llama3.2

# PostgreSQL
docker exec safeops-database psql -U safeops -d safeopsdb -c "SELECT id, status FROM incidents ORDER BY id DESC;"

# Inject a test fault
Invoke-RestMethod -Method Post -Uri http://localhost:8000/admin/fault -ContentType 'application/json' -Body '{"slow_queries": true}'

# Generate test traffic (background loop)
Start-Process powershell -ArgumentList '-NoProfile','-Command','for($i=0;$i -lt 700;$i++){ try { Invoke-WebRequest -Uri http://localhost:8000/items -UseBasicParsing -TimeoutSec 8 | Out-Null } catch {}; Start-Sleep -Milliseconds 300 }'

# Clear the fault
Invoke-RestMethod -Method Post -Uri http://localhost:8000/admin/fault/reset

# Check RCA output
docker exec safeops-database psql -U safeops -d safeopsdb -c "SELECT agent_name, score, reasoning FROM agent_decisions WHERE agent_name='root_cause' ORDER BY id DESC;"
```

---

# Contribution Record

## My Contribution — Logesh-bruce

* Name: `Logesh-bruce`
* Email: `logeshmg432006@gmail.com`

Work completed and verified in today's session (this author):

* **Phase 1 — Root Cause Agent integration**: `root-cause-agent/Dockerfile`;
  `ollama` + `root-cause-agent` services in `docker-compose.yml`; Ollama
  `llama3.2` setup; Prometheus scrape config for the RCA; fixed the migration
  path bug in RCA and incident-agent `db.py`; added the `_as_dict()` JSONB
  normaliser; ran the full Phase 1 fault-injection test and verified the RCA
  decision persisted in `agent_decisions`.
* **Phase 2 — Scoring Agent** (`scoring-agent/`, Step 5): cost / reliability /
  security rule-based scorers, canonical candidate set with `proposed_by_rca`
  audit flag, `database/migrate_step5.sql`, compose + Prometheus wiring, live
  verification.
* **Phase 3 — Coordinator Agent** (`coordinator-agent/`, Step 6): pure-Python
  MCDM (`weighted_sum` + `topsis`), runtime-swappable weights,
  `database/migrate_step6.sql`, compose + Prometheus wiring, unit tests of the
  MCDM math, and live verification including a 4-candidate ranking, TOPSIS
  ablation and weight sweeps.

**This is a collaborative, team-developed project.** The pre-existing system
(backend, frontend, monitoring, database schema, incident-agent,
fault-injector, and the original root-cause-agent source logic) was built by
other team members and is preserved unchanged except for the two
documented `db.py` fixes above.

---

# Next Development Session

1. Review Phase 1 (RCA) — decide on confidence-calibration methodology
2. Review Phase 2 (scoring) and Phase 3 (coordinator) behaviour on more faults
3. Implement Phase 4 — Sandbox Validator (pending the sandbox-isolation design
   decision)
4. Test Phase 4
5. Implement Phase 5 — Auto-remediation + rollback
6. Test Phase 5
7. Implement Phase 6 — Dashboard
8. Implement Phase 7 — Evaluation harness (baseline, CSV export, calibration,
   statistical testing)
9. Continue sequentially through the phases

> **Do not begin the next phase until the previous phase has been verified.**

---

# Known Risks / Design Decisions

| Decision | Status |
|---|---|
| Candidate remediation actions | **DECIDED** — canonical per-service action set merged with RCA candidates; `proposed_by_rca` flag recorded |
| Default MCDM weights | **DECIDED** — cost 0.3 / reliability 0.5 / security 0.2 (weighted_sum); TOPSIS available via `/simulate` + `POST /weights` |
| Sandbox isolation method | **PENDING** — options: throwaway replica, live apply + rollback, or simulated only |
| Rollback mechanism | **PENDING** |
| Confidence calibration methodology | **PENDING** (RCA stores confidence; not yet calibrated) |
| Evaluation trial count / baseline definition | **PENDING** |
| Ollama model idle-unload | Known — inflates RCA latency after ~5 min idle |
| `high_error_rate` vs `/metrics` scrape breakage | Known — produces `BACKEND_DOWN` incidents and occasional `database_unavailable` mismatches |
| Frontend healthcheck (`wget` missing in nginx:alpine) | Known pre-existing — shows `unhealthy`, serves correctly |
| Host port 5432 conflict | DECIDED — DB exposed on host 5433 |

---

# Final Status

| Phase | Status | Verified? |
|---|---|---|
| Existing infrastructure | Implemented (team baseline) | Yes |
| Phase 1 — Root Cause Agent | Implemented (integrated today) | Yes |
| Phase 2 — Scoring Agents | Implemented (today) | Yes |
| Phase 3 — MCDM Coordinator | Implemented (today) | Yes |
| Phase 4 — Sandbox Validator | Not Started | No |
| Phase 5 — Remediation/Rollback | Not Started | No |
| Phase 6 — Dashboard | Not Started | No |
| Phase 7 — Evaluation | Not Started | No |

*Status reflects the actual working tree and live tests, not the original
brief's assumptions. Phases 1–3 were completed and verified on 2026-08-20;
Phases 4–7 remain unimplemented.*