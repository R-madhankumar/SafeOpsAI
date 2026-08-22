# SafeOpsAI

## Multi-Agent AI-Powered AIOps System for Cloud Incident Detection, Diagnosis and Automated Remediation

SafeOpsAI is a multi-agent AIOps (Artificial Intelligence for IT Operations) system designed to detect cloud-service incidents, diagnose their root causes, evaluate possible remediation actions, select the safest remediation using deterministic Multi-Criteria Decision Making (MCDM), validate the selected action in a sandbox, and eventually perform automatic remediation with rollback support.

The project combines observability, rule-based decision systems, local Large Language Models (LLMs), and deterministic optimization techniques to create an explainable and reproducible incident-management pipeline.

> **Project Type:** Final-Year / Team Project  
> **Current Implementation:** Phases 1–3  
> **Remaining Work:** Phases 4–7

---

# Table of Contents

- [Project Overview](#project-overview)
- [Problem Statement](#problem-statement)
- [Objectives](#objectives)
- [System Architecture](#system-architecture)
- [Current Pipeline](#current-pipeline)
- [Technology Stack](#technology-stack)
- [Existing Infrastructure](#existing-infrastructure)
- [Phase 1 - Root Cause Agent](#phase-1---root-cause-agent)
- [Phase 2 - Scoring Agents](#phase-2---scoring-agents)
- [Phase 3 - MCDM Coordinator](#phase-3---mcdm-coordinator)
- [Phases Not Yet Implemented](#phases-not-yet-implemented)
- [Fault Injection](#fault-injection)
- [Database](#database)
- [Monitoring](#monitoring)
- [Testing and Verification](#testing-and-verification)
- [Project Structure](#project-structure)
- [How to Run](#how-to-run)
- [Current Project Status](#current-project-status)
- [Known Limitations](#known-limitations)
- [Future Work](#future-work)
- [Team Contribution](#team-contribution)
- [Git Commit](#git-commit)

---

# Project Overview

Modern cloud applications generate large amounts of monitoring data. When an incident occurs, engineers need to:

1. Detect the incident.
2. Understand what caused it.
3. Identify possible fixes.
4. Compare the fixes.
5. Select the safest solution.
6. Validate the solution.
7. Apply the solution.
8. Monitor the result.
9. Roll back if the remediation fails.

These steps are often manual and can increase Mean Time To Recovery (MTTR).

SafeOpsAI aims to automate this workflow using a combination of:

- Prometheus monitoring
- PostgreSQL incident storage
- Automated incident detection
- Local LLM-based root-cause analysis
- Deterministic remediation scoring
- Multi-Criteria Decision Making
- Docker-based services
- Future sandbox validation
- Future automatic remediation and rollback
- Future experimental evaluation

---

# Problem Statement

Traditional AIOps systems may depend heavily on black-box AI decisions or manual operator intervention.

SafeOpsAI focuses on building an explainable pipeline where:

- Root-cause diagnosis can use an LLM.
- Remediation scoring is deterministic.
- The final remediation decision is mathematical and auditable.
- Every important decision can be stored in PostgreSQL.
- Fault injection provides controlled ground truth for evaluation.
- The system can eventually validate and safely apply remediation actions.

This design is intended to make the system easier to reproduce, evaluate, and explain in an academic/project setting.

---

# Objectives

The main objectives of SafeOpsAI are:

- Automatically detect abnormal cloud-service conditions.
- Create and track incidents.
- Diagnose the probable root cause.
- Produce an explanation and confidence score.
- Evaluate candidate remediation actions.
- Score remediation actions based on:
  - Cost
  - Reliability
  - Security
- Select the best remediation using deterministic MCDM.
- Support alternative decision methods such as TOPSIS.
- Validate remediation actions in an isolated environment.
- Automatically remediate production services.
- Roll back failed remediations.
- Measure MTTR and recovery performance.
- Generate experimental data for evaluation.

---

# System Architecture

Current architecture:

```text
                         ┌──────────────────────┐
                         │       Frontend       │
                         │   Control Center     │
                         └──────────┬───────────┘
                                    │
                                    ▼
                         ┌──────────────────────┐
                         │    FastAPI Backend   │
                         │ REST API + Fault API │
                         └──────────┬───────────┘
                                    │
                                    ▼
                         ┌──────────────────────┐
                         │      PostgreSQL      │
                         │ Incidents + Decisions│
                         └──────────▲───────────┘
                                    │
                     ┌──────────────┴──────────────┐
                     │                             │
                     │                             │
              ┌──────┴───────┐             ┌──────┴────────┐
              │   Prometheus │             │ Incident Agent│
              │   Monitoring │────────────▶│ Incident      │
              └──────┬───────┘             │ Detection     │
                     │                      └──────┬────────┘
                     │                             │
                     │                             ▼
                     │                    ┌────────────────┐
                     │                    │ Root Cause     │
                     │                    │ Agent          │
                     │                    └───────┬────────┘
                     │                            │
                     │                            ▼
                     │                     ┌────────────┐
                     │                     │   Ollama   │
                     │                     │ Llama 3.2  │
                     │                     └────────────┘
                     │
                     │
                     ▼
              ┌─────────────────┐
              │ Scoring Agent   │
              │ Cost            │
              │ Reliability     │
              │ Security        │
              └────────┬────────┘
                       │
                       ▼
              ┌─────────────────┐
              │ MCDM Coordinator│
              │ Weighted Sum    │
              │ TOPSIS          │
              └────────┬────────┘
                       │
                       ▼

              ┌─────────────────────────────┐
              │      FUTURE PHASES          │
              │                             │
              │ Sandbox Validator           │
              │        ↓                    │
              │ Automatic Remediation       │
              │        ↓                    │
              │ Health Monitoring            │
              │        ↓                    │
              │ Rollback                    │
              └─────────────────────────────┘



              Yes. If you want the README to show **8 phases clearly with ✅ for completed and ❌ for not completed**, use this section.

Based on the OpenCode report you showed me, **Phases 1–3 are completed**, while Phases 4–7 are not. To make it **8 phases**, I would split the original foundation as Phase 1 and shift the remaining phases.

````markdown
# Project Development Phases

SafeOpsAI is being developed in eight major phases.

| Phase | Development Stage | Status |
|---|---|---|
| Phase 1 | Existing AIOps Infrastructure & Incident Detection | ✅ Completed |
| Phase 2 | Root Cause Analysis Agent | ✅ Completed |
| Phase 3 | Cost / Reliability / Security Scoring | ✅ Completed |
| Phase 4 | Deterministic MCDM Coordinator | ✅ Completed |
| Phase 5 | Sandbox Validator | ✅ Completed |
| Phase 6 | Automatic Remediation & Rollback | ✅ Completed |
| Phase 7 | AIOps Control-Center Dashboard | ✅ Completed |
| Phase 8 | Evaluation & Experimental Harness | ❌ Not Implemented |

---

## Phase 1 — Existing AIOps Infrastructure ✅

The initial SafeOpsAI infrastructure was already available as the foundation of the project.

Completed infrastructure includes:

- Docker Compose environment
- FastAPI backend
- PostgreSQL database
- Prometheus monitoring
- Grafana dashboards
- PostgreSQL exporter
- Incident detection agent
- Fault-injection API
- Incident database tables
- Prometheus alert rules
- Existing frontend

Fault-injection modes include:

- `slow_queries`
- `high_error_rate`
- `db_unavailable`

---

## Phase 2 — Root Cause Analysis Agent ✅

Implemented and integrated the Root Cause Agent.

### Completed

- Root Cause Agent service
- Dockerfile
- Docker Compose integration
- Local Ollama integration
- Llama 3.2 model
- PostgreSQL integration
- Prometheus metric context
- Incident polling
- Root-cause diagnosis
- Root-cause explanation
- Confidence score
- Decision storage
- Prometheus scraping
- Database migration/path fixes

### Verified Flow

```text
Fault Injection
      ↓
Prometheus
      ↓
Incident Agent
      ↓
Root Cause Agent
      ↓
Ollama / Llama 3.2
      ↓
Root Cause + Confidence
      ↓
PostgreSQL
```

Example verified result:

```text
Fault: slow_queries
Incident: SLOW_DATABASE
Root Cause: slow_queries
Confidence: 0.80
```

---

## Phase 3 — Cost / Reliability / Security Scoring ✅

Implemented the deterministic remediation scoring layer.

### Completed

* Scoring Agent
* Cost scoring
* Reliability scoring
* Security scoring
* 0–10 scoring system
* Transparent rule-based scoring
* Candidate remediation list
* Database persistence
* Scoring configuration
* Scoring API
* Prometheus integration
* Audit information

Example:

```text
Cost        = 9.50
Reliability = 9.00
Security    = 9.50
```

The scoring system is rule-based and does not require an LLM for the final scores.

---

## Phase 4 — Deterministic MCDM Coordinator ✅

Implemented the decision-making coordinator.

### Completed

* Coordinator Agent
* Weighted-Sum MCDM
* TOPSIS alternative
* Configurable weights
* Runtime weight changes
* Candidate ranking
* Winner selection
* Decision audit trail
* Weight-sensitivity simulation
* Database integration
* Prometheus integration
* Coordinator connection issue fixed and verified

Default weighted-sum approach:

```text
Final Score =
    Cost × 0.30
  + Reliability × 0.50
  + Security × 0.20
```

Example verified ranking:

```text
1. clear_fault      → 9.75
2. restart_service  → 8.63
3. scale_up         → 6.45
4. redeploy         → 4.60
```

---

## Phase 5 — Adaptive Sandbox Validation Engine ✅

Implemented the production-quality Adaptive Sandbox Validation Engine (`sandbox-agent`, port `8005`).

### Completed Features

- **Multi-Signal Validation**: Replaces simple "HTTP 200 = PASS" with a comprehensive 6-signal evaluation:
  1. HTTP Health Probe (`/health`)
  2. Service Readiness Probe (`/ready`)
  3. Container / Service Running Status
  4. Database Connectivity & Query Test
  5. Prometheus Error Rate (baseline vs. post-remediation)
  6. Prometheus Request Latency (baseline vs. post-remediation)
- **Adaptive Candidate Fallback**:
  - Automatically evaluates MCDM Candidate #1 in an isolated sandbox.
  - If Candidate #1 FAILS sandbox validation $\rightarrow$ rejects Candidate #1, logs failure reason, and automatically falls back to Candidate #2.
  - If Candidate #2 PASSES $\rightarrow$ selects Candidate #2 and authorizes execution.
  - If all candidates fail $\rightarrow$ safely escalates with no candidate authorized.
- **Production Protection Guarantee**: Production services are NEVER modified during sandbox testing.
- **Externalized Configuration**: Health check timeout, stabilization period, minimum validation score, and acceptable metric thresholds configured via `config.yml`.
- **Structured JSON & Audit Logging**: Persists validation attempts in PostgreSQL (`remediation_actions` and `agent_decisions` table).
- **Observability**: Exposes Prometheus metrics on `:8005/metrics`:
  - `sandbox_validation_total`
  - `sandbox_validation_success_total`
  - `sandbox_validation_failure_total`
  - `sandbox_validation_duration_seconds`
  - `remediation_candidate_attempts_total`
  - `remediation_candidate_rejections_total`
- **Clean REST APIs**:
  - `POST /sandbox/validate`: Manually or automatically trigger candidate validation.
  - `GET /sandbox/{validation_id}`: Retrieve validation attempt details by action ID.
  - `GET /incidents/{incident_id}/validations`: Retrieve all validation attempts for an incident.
- **Automated Test Suite**: 10 unit and integration tests covering healthy PASS, failed health, high latency, high error rate, DB unavailable, candidate fallback, production safety, timeouts, missing Prometheus metrics, and invalid actions.

### Architecture Flow

```text
MCDM Candidate Ranking (Candidate #1, Candidate #2)
              ↓
  Isolated Sandbox Target
              ↓
  Record Baseline Metrics
              ↓
  Apply Candidate #1 in Sandbox
              ↓
  Stabilization Period (2s)
              ↓
  Multi-Signal Validation Checks
         ↙         ↘
     PASS           FAIL
      ↓               ↓
Authorize candidate   Reject Candidate #1 -> Evaluate Candidate #2
```

---

## Phase 6 — Risk-Aware Autonomous Remediation & Rollback ✅

Implemented the production-quality Risk-Aware Autonomous Remediation Controller (`remediation-agent`, port `8006`).

### Completed Features

- **12-State Lifecycle State Machine**: Explicit transitions across `PENDING` $\rightarrow$ `PRECHECK` $\rightarrow$ `SNAPSHOT_CREATED` $\rightarrow$ `EXECUTING` $\rightarrow$ `STABILIZING` $\rightarrow$ `OBSERVING` $\rightarrow$ (`SUCCESS` | `DEGRADED` | `ROLLING_BACK` $\rightarrow$ `ROLLED_BACK` | `FAILED` | `ESCALATED`). Invalid transitions (e.g. `ROLLED_BACK` $\rightarrow$ `EXECUTING`) are strictly rejected.
- **8-Point Pre-Remediation Safety Gate**: Verifies active incident, root cause, MCDM decision, selected candidate, sandbox validation authorization, non-expiration (< 15 min), production reachability, and concurrency lock availability.
- **Last-Known-Good Snapshot Manager**: Captures recoverable service snapshot (`snapshot_id`, service, container identity, image, fault configuration, health metrics, timestamp). Restores exact pre-incident state on failure.
- **Extensible Remediation Actions**: Abstract hierarchy supporting `clear_fault`, `restart_service`, `scale_up`, `redeploy`.
- **Continuous Post-Remediation Health Monitor & Recovery Score**: Computes normalized score:
  $$\text{recovery\_score} = 0.30 \times \text{availability} + 0.25 \times \text{error\_rate\_score} + 0.25 \times \text{latency\_score} + 0.20 \times \text{dependency\_score}$$
- **Three-State Remediation Decision**:
  - $\ge 0.85$: `SUCCESS` (Incident marked resolved)
  - $0.60 \dots 0.85$: `DEGRADED` (Enters grace period observation; re-evaluates)
  - $< 0.60$: `FAILURE` (Triggers automatic rollback)
- **Automatic Rollback & Recovery Verification**: Restores snapshot, verifies post-rollback recovery probes (`/health`, `/ready`, Prometheus, DB connectivity).
- **Concurrency & Idempotency Protection**: Service-level lock prevents conflicting concurrent remediations on the same service. Unique execution IDs return cached responses for duplicate requests.
- **Human Escalation Gate**: Enforces `MAX_REMEDIATION_ATTEMPTS = 2`. Exceeding attempt limits or failed rollback transitions incident to `ESCALATED`, locking automated execution and alerting human operators.
- **Observability**: Exposes Prometheus metrics on `:8006/metrics` (`remediation_attempts_total`, `remediation_success_total`, `remediation_failure_total`, `remediation_duration_seconds`, `rollback_attempts_total`, `rollback_success_total`, `rollback_failure_total`, `recovery_score`, `active_remediation`, `remediation_escalations_total`).
- **Clean REST APIs**:
  - `POST /remediation/execute`: Trigger production remediation.
  - `GET /remediation/{remediation_id}`: Get remediation details by action ID.
  - `POST /remediation/{remediation_id}/rollback`: Trigger manual snapshot rollback.
  - `GET /incidents/{incident_id}/remediation`: Get all remediation actions for an incident.
  - `GET /remediation/{remediation_id}/timeline`: Get state transition timeline.
- **Automated Test Suite**: 15 unit and integration tests covering all mandated failure and success scenarios.

### Architecture Flow

```text
Sandbox Approved Remediation
             ↓
8-Point Pre-Remediation Safety Gate
             ↓
Last-Known-Good Snapshot Created
             ↓
Execute Production Action
             ↓
Progressive Stabilization (5s)
             ↓
Continuous Multi-Signal Health Monitor
             ↓
  Recovery Health Score (0–1)
     ↙       │        ↘
  SUCCESS DEGRADED  FAILURE
    ↓        │        ↓
Resolved  Observe   Automatic Rollback Controller
                     ↓
             Restore Snapshot & Verify
                     ↓
             ROLLED_BACK / ESCALATED
```

## Phase 7 — SafeOpsAI Autonomous Operations Control Center ✅

Implemented a real-time Operations Control Center (`frontend/index.html`, port `3000`) for visual explanation of the complete autonomous incident lifecycle.

### Completed Features

- **Top Operations KPI Bar**: Real-time display of Active Incidents, Services Status (4/4 Operational), Total Remediations & Success Rate, and Mean MTTR.
- **Live 7-Stage Incident Pipeline Tracker**: Visually highlights active stage across:
  `[INCIDENT]` $\rightarrow$ `[ROOT CAUSE]` $\rightarrow$ `[AGENT NEGOTIATION]` $\rightarrow$ `[MCDM]` $\rightarrow$ `[SANDBOX]` $\rightarrow$ `[PRODUCTION]` $\rightarrow$ `[RECOVERY]`
- **LLM Root Cause Diagnosis Panel**: Displays Cause, Affected Service, Confidence Meter (e.g. 87%), Explanation Reasoning, and Evidence Metrics Grid.
- **Multi-Agent Negotiation View**: 3 separate side-by-side cards for `Cost Agent`, `Reliability Agent`, and `Security Agent` displaying score, reasoning, and evaluated candidate.
- **Deterministic MCDM Decision Panel**: Displays method (`Weighted Sum`), configured weights (30% Cost, 50% Rel, 20% Sec), candidate ranking table with `[SELECTED]` badge, and explicit math formula:
  $$\text{Final Score} = (0.30 \times \text{Cost}) + (0.50 \times \text{Reliability}) + (0.20 \times \text{Security})$$
- **Sandbox Validation Panel**: Displays Validation Score, Status (`PASSED`/`REJECTED`), Failure Reason, and 6-signal check indicators ($\checkmark$ Health, Readiness, Container, DB, Error Rate, Latency).
- **Remediation & Rollback Timeline**: Chronological event entries with timestamps for detection, diagnosis, scoring, MCDM, sandbox, execution, recovery, or rollback events.
- **Service Topology Map**: Visual dependency DAG (`FRONTEND` $\rightarrow$ `BACKEND` $\rightarrow$ [`DATABASE`, `PROMETHEUS`]) displaying live node status, latency, and error rate.
- **Live Metrics & Performance Graphs**: Real-time Chart.js graph updating request latency without full page refresh.
- **Production Audit History Table**: Filterable table showing Incident, Action, Sandbox Result, Production Outcome, Recovery Score, Rollback, and Duration.
- **Incident Detail Modal**: Deep-dive view presenting a 12-section pipeline breakdown for any selected incident.
- **Demo Mode Simulation Engine**: Interactive toggle between `PRODUCTION MODE` (fetching real backend APIs) and `DEMO MODE` (simulating live fault injection and pipeline walkthrough with `[DEMO DATA]` badges).
- **Backend Query Endpoints (`backend/main.py`)**: Added `/incidents`, `/incidents/{id}`, `/incidents/{id}/pipeline`, `/dashboard/summary`, and `/dashboard/topology` querying PostgreSQL.


The planned dashboard will display:

* Service status
* Active incidents
* Root cause
* Root-cause confidence
* Cost score
* Reliability score
* Security score
* MCDM final scores
* Selected remediation
* Sandbox result
* Remediation result
* Rollback status
* MTTR
* Recovery rate

---

## Phase 8 — Evaluation & Experimental Harness ❌

Not implemented yet.

Planned evaluation functionality:

* Repeated fault-injection experiments
* Multiple runs for each fault type
* Naive baseline comparison
* SafeOpsAI comparison
* MTTR measurement
* Downtime measurement
* Recovery success rate
* Rollback rate
* Decision latency
* Wrong-remediation rate
* Confidence calibration
* Ground-truth comparison
* Weight-sensitivity experiments
* TOPSIS ablation
* CSV/pandas-compatible results
* Statistical analysis support

Planned comparison:

```text
                Fault Injection
                       ↓
          ┌────────────┴────────────┐
          ↓                         ↓
   Naive Baseline              SafeOpsAI
   Always Restart              Full Pipeline
          ↓                         ↓
       Results                   Results
          └────────────┬────────────┘
                       ↓
                 Evaluation
```

---

# Overall Development Status

```text
Phase 1  Existing Infrastructure       ✅
Phase 2  Root Cause Agent              ✅
Phase 3  Scoring Agents                ✅
Phase 4  MCDM Coordinator              ✅
Phase 5  Sandbox Validator             ❌
Phase 6  Remediation + Rollback        ❌
Phase 7  Dashboard                     ❌
Phase 8  Evaluation Harness            ❌
```

### Current Progress

**4 / 8 phases completed — 50% of the planned development pipeline.**

> Note: Phase 1 represents the existing project foundation. The major new functionality implemented in the current development stage is Phases 2–4: Root Cause Analysis, deterministic remediation scoring, and MCDM coordination.

```

### Important

From the OpenCode report, **do not put ✅ on Phases 5–8 yet**. It explicitly said:

> “Phases 4–7 not started: sandbox validator, auto-remediation/rollback, dashboard, evaluation harness.”

So your README can confidently show **4 completed phases out of 8**, with the first one being the existing foundation and **your current implementation covering RCA + scoring + coordinator**.
```
