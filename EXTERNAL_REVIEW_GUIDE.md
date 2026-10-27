# SafeOpsAI — External Reviewer & Examiner Technical Guide

> **Project:** SafeOpsAI: A Multi-Agent Autonomous Cloud Operations Platform for Intelligent Incident Detection and Self-Healing  
> **Audience:** External Reviewers, Technical Examiners, Academic Mentors  
> **Core Architecture:** 6 Specialized Microservice Agents + Deterministic MCDM + Adaptive Sandbox + Operations Control Center UI  

---

## 1. What problem does SafeOpsAI solve?

Modern cloud-native and microservice architectures generate massive volumes of metrics, logs, and alerts. When incidents occur in production (e.g., database connection pool exhaustion, cascading HTTP 5xx errors, latency degradation), human on-call engineers face three major challenges:
1. **High Mean Time to Recovery (MTTR)**: Manually correlating Prometheus metrics and identifying root cause takes tens of minutes or hours.
2. **Risky Trial-and-Error Fixes**: Engineers often apply naive actions (such as blunt container restarts or blind scale-ups) that can worsen outages or create data corruption.
3. **Lack of Explainability & Auditability**: "Black-box" automated scripts execute changes without auditable justification, safety pre-checks, or automatic rollback mechanisms.

SafeOpsAI automates the entire incident lifecycle—from anomaly detection to verified self-healing—under the core philosophy:
> *"Every remediation decision is explainable, every safety check is visible, every action is traceable, and every recovery claim is supported by measured evidence."*

---

## 2. Why is SafeOpsAI an AIOps and cloud operations project?

SafeOpsAI operates directly on cloud observability data (Prometheus instant-queries, histogram quantiles, and system telemetry) and automates standard SRE (Site Reliability Engineering) workflows:
- **Continuous Observability**: Polls live system metrics (P95 latencies, error rates, container status).
- **Incident Lifecycle Management**: Tracks incidents through formal states (`INCIDENT` $\rightarrow$ `ROOT CAUSE` $\rightarrow$ `SCORING` $\rightarrow$ `MCDM` $\rightarrow$ `SANDBOX` $\rightarrow$ `PRODUCTION` $\rightarrow$ `RECOVERY`).
- **Closed-Loop Feedback**: Verifies production health post-remediation using a multi-signal recovery health score.

---

## 3. What makes the multi-agent architecture useful?

Rather than relying on a single monolithic script or an unconstrained LLM prompt, SafeOpsAI decomposes operations into specialized, bounded microservice agents:
1. **Incident Detection Agent (`:8001`)**: Continuously monitors Prometheus metrics against SLO alert rules; creates deduplicated incidents in PostgreSQL.
2. **Root Cause Agent (`:8002`)**: Ingests metrics snapshots, queries local Ollama (`llama3.2`), and identifies the fault hypothesis with structured confidence scores.
3. **Scoring Agents (`:8003`)**: Three independent domain evaluators (Cost, Reliability, Security) that score candidate remediations.
4. **MCDM Coordinator (`:8004`)**: A deterministic mathematical optimization engine that reconciles multi-agent trade-offs without LLM hallucinations.
5. **Sandbox Validation Agent (`:8005`)**: Pre-tests proposed candidate actions in an isolated environment against 6 health signals before any production modification.
6. **Autonomous Remediation & Rollback Agent (`:8006`)**: Executes actions in production with safety gates, snapshots state, monitors progressive stabilization, and triggers auto-rollback if health degrades.

---

## 4. Why are cost, reliability, and security evaluated separately?

Remediation in production is inherently multi-objective. A single action rarely optimizes all dimensions:
- **Cost Scorer**: Penalizes expensive operations (e.g., spinning up multi-node replicas, over-provisioning memory). High score (10.0) = zero financial impact (e.g., resetting a soft fault flag or clearing dead cache).
- **Reliability Scorer**: Evaluates downtime risk, reversibility, and system stability. A service restart causes brief downtime, whereas a surgical config rollback does not.
- **Security Scorer**: Assesses privilege exposure, credential exposure risk, and attack surface.

Evaluating these separately prevents blind trade-offs and allows operators to adjust organizational policies dynamically.

---

## 5. How does the coordinator select an action?

The Coordinator Agent uses **Multi-Criteria Decision Making (MCDM)**—specifically **Weighted Sum Model (WSM)** as default and **TOPSIS** (Technique for Order of Preference by Similarity to Ideal Solution) for ablation:

$$\text{Final Score} = (w_{\text{cost}} \times \text{Cost}) + (w_{\text{rel}} \times \text{Reliability}) + (w_{\text{sec}} \times \text{Security})$$
*Default baseline weights:* Cost = 0.30, Reliability = 0.50, Security = 0.20.

**Crucial Examiner Point:** The Coordinator does **NOT** use an LLM. Its ranking is 100% deterministic, reproducible mathematics. Every intermediate score, normalized weight, and ranking position is persisted in PostgreSQL `agent_decisions.raw_output`.

---

## 6. Why is sandbox validation necessary?

Even a mathematically optimal remediation action can fail in practice due to runtime conditions (e.g., locked database sockets, dependent services being unresponsive).
The Sandbox Agent:
- Isolates candidate testing without touching live production traffic.
- Executes an **adaptive fallback cascade**: If Candidate #1 fails sandbox checks, Candidate #2 is automatically validated.
- Ensures production is only touched when an action has passed health, readiness, and connectivity probes.

---

## 7. How does the system prevent unsafe remediation?

SafeOpsAI implements multi-tiered safety gates:
1. **Pre-Remediation Safety Gate**: 8-point check verifying database connectivity, container health, and authorization tokens.
2. **Execution Authorization Flag**: Remediation actions in the database require `execution_authorized = TRUE` from the sandbox agent.
3. **Environment Isolation Guard**: Evaluation and simulation scripts enforce `ENVIRONMENT=simulation` checks in `safety.py` to prevent running synthetic fault injections in live production.
4. **Bounded Retries**: Maximum of 2 remediation attempts per incident before escalating to human SREs.

---

## 8. How does automatic rollback work?

The Remediation Agent maintains a 12-state lifecycle state machine (`PENDING` $\rightarrow$ `PRECHECK` $\rightarrow$ `SNAPSHOT_CREATED` $\rightarrow$ `EXECUTING` $\rightarrow$ `STABILIZING` $\rightarrow$ `OBSERVING` $\rightarrow$ `SUCCESS` / `ROLLING_BACK`).

1. **Snapshot Capture**: Captures service configuration and state snapshot before applying any change.
2. **Post-Action Observation Window**: Waits 5s for stabilization, then continuously evaluates a multi-signal **Recovery Health Score**:
   $$\text{Recovery Score} = 0.30 \times \text{Availability} + 0.25 \times \text{ErrorRateScore} + 0.25 \times \text{LatencyScore} + 0.20 \times \text{DependencyScore}$$
3. **Rollback Decision Thresholds**:
   - $\ge 0.85$: **SUCCESS** (incident marked resolved, MTTR recorded).
   - $0.60 - 0.85$: **DEGRADED** (continues observation).
   - $< 0.60$: **AUTOMATIC ROLLBACK** triggered immediately to restore the Last-Known-Good snapshot.

---

## 9. What is the role of the LLM vs. Rule-based Fallback?

- **LLM Role**: Used exclusively in the Root Cause Agent (`:8002`) to synthesize multi-dimensional metric anomalies and cascade patterns into human-readable diagnostic explanations and remediation candidate proposals.
- **Direct HTTP Integration**: Connects directly to local Ollama via `httpx` (no heavy LangChain/LangGraph abstractions).
- **Rule-based Fallback**: If Ollama is offline or times out (CPU load), the RCA agent automatically falls back to deterministic rule-based symptom matching. The system remains 100% operational and transparently records `diagnosis_method = 'fallback'`.

---

## 10. What is the difference between Replay and Counterfactual Analysis?

Both features are available in the Operations Control Center (`http://localhost:3000`):

| Dimension | Incident Replay | Counterfactual Decision Lab |
|---|---|---|
| **Purpose** | Forensic timeline walkthrough | "What-if" policy exploration |
| **Data Source** | Read-only historical events in PostgreSQL | Live parameter sweep over candidate scores |
| **Interactive Controls**| Play, Pause, Step, Speed (1x, 2x, 4x) | Cost/Reliability/Security weight sliders |
| **Mutation Risk** | **Zero** — strictly read-only | **Zero** — preview only; never touches DB or production |
| **Examiner Value** | Proves incident lifecycle progression | Demonstrates sensitivity and policy trade-offs |

---

## 11. Current System Implementation Status

| Component | Status | Implementation Details |
|---|---|---|
| Infrastructure & FastAPI Backend | ✅ Complete | PostgreSQL 16, asyncpg, Prometheus client, REST endpoints |
| Incident Detection Agent | ✅ Complete | Polls Prometheus every 5s; rules in `rules.yml`; dedup via partial index |
| Root Cause Agent (RCA) | ✅ Complete | Ollama `llama3.2` + automated rule-based fallback |
| Scoring Agents (Cost/Rel/Sec) | ✅ Complete | 3 deterministic scorers, JSONB component breakdowns |
| Deterministic MCDM Coordinator | ✅ Complete | Weighted Sum Model + TOPSIS ablation |
| Adaptive Sandbox Engine | ✅ Complete | Multi-signal validation + adaptive candidate fallback |
| Risk-Aware Remediation & Rollback| ✅ Complete | 12-state state machine, last-known-good snapshot, health score rollback |
| Operations Control Center UI | ✅ Complete | Dark dashboard, live pipeline tracker, evidence viewer, replay, decision lab |
| Experimental Evaluation Harness | ✅ Complete | 6 fault scenarios, 4 comparison strategies, 90 trials, Wilcoxon test |
| Windows Native Orchestration | ✅ Complete | `start-safeopsai.ps1`, `check-safeopsai.ps1`, `stop-safeopsai.ps1` (No Docker required) |
