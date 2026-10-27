# SafeOpsAI — Step-by-Step Demonstration Runbook
**Evidence-Driven Autonomous Cloud Operations Platform**  
*Final-Year Project External Review & Examiner Demonstration Guide*

---

## 1. Overview & Demonstration Objectives

This runbook gives the presenter an exact, minute-by-minute script and operational sequence to demonstrate SafeOpsAI before technical mentors and external academic examiners.

### Key Thesis Statement for Examiners
> *"SafeOpsAI is not a black-box autonomous recovery tool. Every remediation decision is explainable through multi-criteria decision mathematics (MCDM TOPSIS), every safety gate is visible, every agent action is cryptographically auditable, and every recovery claim is backed by measured pre- and post-action telemetry."*

---

## 2. Pre-Flight Preparation (5 Minutes Before Review)

Complete these checks before the presentation begins:

1. **Working Directory:** Open PowerShell 5.1/7.x as Administrator or standard user in:
   ```powershell
   cd D:\PROJECTS\SafeOpsAI\SafeOpsAI-main
   ```
2. **Local PostgreSQL Check:** Ensure the PostgreSQL 17 Windows service / background process is active:
   ```powershell
   Get-Process -Name postgres -ErrorAction SilentlyContinue
   ```
   *If not running:*
   ```powershell
   & "C:\PostgreSQL17\pgsql\bin\pg_ctl.exe" start -D "C:\PostgreSQL17\data"
   ```
3. **Display Setup:**
   - Left half of screen: Web Browser with **Operations Control Center** (`http://localhost:3000`).
   - Right half of screen: PowerShell terminal for triggering actions and observing backend logs.

---

## 3. Starting the SafeOpsAI Platform

Execute the one-click Windows launcher:

```powershell
.\start-safeopsai.ps1
```

### What This Does:
1. Validates Python environment and local PostgreSQL connectivity.
2. Checks Ollama availability (`http://localhost:11434`) and sets fallback mode if unreachable.
3. Spawns 8 native background processes on local ports:
   - `8000`: Central FastAPI Backend & Database Gateway
   - `8001`: Root Cause Analysis Agent (Ollama LLM + Rule-Based Engine)
   - `8002`: Cost Scoring Agent
   - `8003`: Reliability Scoring Agent
   - `8004`: Security Scoring Agent
   - `8005`: Multi-Criteria Decision Making (MCDM) Coordinator
   - `8006`: Adaptive Sandbox Validation Agent
   - `3000`: Operations Control Center Frontend UI
4. Records all process IDs in `.run\services.json`.
5. Redirects process logs into `./logs/*.log`.

---

## 4. Readiness Verification Gate

Before inviting examiners to view the screen, run the non-destructive readiness gate check:

```powershell
.\check-safeopsai.ps1
```

### Expected Output:
- [x] Python 3.10+ detected
- [x] Local PostgreSQL running and accepting connections on `127.0.0.1:5432`
- [x] Ports 8000–8006 and 3000 actively listening
- [x] Simulation safety guard verified (`SAFEOPS_ENV=simulation`)
- [x] Status: **ALL SYSTEMS DEMO READY**

---

## 5. Operations Control Center Navigation

Open Google Chrome or Microsoft Edge and navigate to:
```
http://localhost:3000
```

Point out the three primary operational views:
1. **Live Incident Pipeline:** The real-time 7-stage autonomous response lifecycle.
2. **Incident Replay:** The deterministic, read-only playback scrub-bar of completed incidents.
3. **Counterfactual Decision Lab:** Interactive scoring weight sensitivity sliders.

---

## 6. Live Incident Demonstration Walkthrough

Trigger the repeatable demonstration scenario either from the terminal or using the script:

```powershell
.\run-demo.ps1
```

Follow this exact talk track as the incident progresses through the 7 stages on screen:

### Stage 1: Fault Injection & Alert Detection
- **What happens on screen:** The status badge shifts from `NORMAL` to `DETECTED` (Red alert: `INC-DEMO-*`).
- **Talk track for examiners:**  
  *"We have injected a controlled database latency fault simulating a festival sale bottleneck. Notice that the incident is not triggered by a synthetic boolean flag. The anomaly detection pipeline observes `db_query_duration_seconds` exceeding our strict SLO threshold of 2.0 seconds over a sustained 15-second evaluation window."*

### Stage 2: Root-Cause Analysis (LLM vs. Deterministic Fallback)
- **What happens on screen:** Stage 2 lights up with diagnostic hypotheses, confidence scores, and affected metrics.
- **Talk track for examiners:**  
  *"Here, the Root Cause Agent analyzes log lines and telemetry anomalies. If Ollama with Llama-3.2 is active locally, it synthesizes semantic root-cause narratives. If Ollama is offline or latency exceeds our 5-second deadline, our deterministic fallback rule engine immediately takes over. Notice that the diagnostic output clearly states whether it was generated via LLM inference or Rule-Based Fallback, ensuring zero demonstration failure."*

### Stage 3: Independent Candidate Scoring (Cost, Reliability, Security)
- **What happens on screen:** Three parallel scoring bars appear for each candidate action (`restart_service`, `clear_fault`, `scale_pool`, `rollback_config`).
- **Talk track for examiners:**  
  *"Most autonomous systems select the first action that matches a pattern. SafeOpsAI instead evaluates every candidate action across three orthogonal, competing criteria:
  - **Cost Agent (Port 8002):** Evaluates computational overhead, API consumption, and transient downtime costs.
  - **Reliability Agent (Port 8003):** Calculates historical MTTR, blast radius, and success probability.
  - **Security Agent (Port 8004):** Evaluates privilege requirements, attack surface exposure, and credential access.
  Scoring is strictly normalized between 0.0 and 1.0."*

### Stage 4: MCDM Coordinator Decision (TOPSIS)
- **What happens on screen:** The candidate ranking table displays Euclidean distances ($D^+$, $D^-$) and relative closeness scores ($C_i$). The top action is highlighted.
- **Talk track for examiners:**  
  *"Rather than relying on ad-hoc heuristic weighting, our Coordinator Agent executes Technique for Order of Preference by Similarity to Ideal Solution (TOPSIS). It determines the Geometric Ideal Solution ($A^+$) and Negative-Ideal Solution ($A^-$) in 3D objective space. The action closest to $A^+$ and furthest from $A^-$ is selected. Here, `clear_fault` (or `restart_service`) achieves the highest relative closeness score $C_i = 0.812$."*

### Stage 5: Adaptive Sandbox Validation
- **What happens on screen:** Stage 5 shows `Sandbox: PASS`, isolation environment ID, and pre-production telemetry delta.
- **Talk track for examiners:**  
  *"Before touching production, SafeOpsAI executes the candidate action inside an isolated ephemeral sandbox environment. The sandbox verifies that the proposed remediation actually clears the fault without triggering secondary side-effects or cascade crashes. Only when sandbox telemetry confirms a recovery signal does the action receive an execution permit."*

### Stage 6: Remediation Safety Gates & Execution
- **What happens on screen:** Pre-execution safety checklist checks: Blast Radius Guard (PASS), Cooldown Window (PASS), Action Approval Policy (PASS).
- **Talk track for examiners:**  
  *"Notice our defense-in-depth safety gates. Even after sandbox approval, the remediation agent verifies rate-limiting cooldowns and production blast-radius ceilings. Once cleared, the remediation state machine moves from `VALIDATED` to `EXECUTING`."*

### Stage 7: Telemetry Verification & Closure
- **What happens on screen:** Telemetry graph shows latency dropping back under 0.12s. Incident status transitions to `RECOVERED` $\rightarrow$ `CLOSED`.
- **Talk track for examiners:**  
  *"SafeOpsAI does not declare success simply because a command returned exit code 0. The system monitors live Prometheus telemetry across a 30-second verification window. Only when latency returns below the baseline threshold is the incident officially marked as RECOVERED."*

---

## 7. Incident Replay Walkthrough

Navigate to the **Incident Replay** section on the dashboard:

1. **Select Incident:** Select the incident just completed from the dropdown list.
2. **Timeline Reconstruction:** Point out that the timeline is built strictly from immutable database records in `incident_lifecycle_events` and `remediation_audit_log`.
3. **Playback Controls:**
   - Click **Restart** to reset the scrub-bar to $t=0$.
   - Click **Play** at $1\times$ or $2\times$ speed.
   - Click **Step Forward** to advance stage by stage.
4. **Key Verification for Examiners:**  
   *"Examiners often ask if Replay re-executes actions. Look at our network tab and logs: Replay is 100% read-only. It consumes historical state snapshots and telemetry deltas without triggering any mutations or faults."*

---

## 8. Counterfactual Decision Lab Walkthrough

Navigate to the **Counterfactual Decision Lab** section:

1. **Show Original Weights:**
   - Cost Weight: $0.30$
   - Reliability Weight: $0.50$
   - Security Weight: $0.20$
   - Point out original ranking: Candidate A is ranked #1.
2. **Adjust Weight Sliders:**
   - Drag **Security Weight** up to $0.80$ and **Reliability Weight** down to $0.10$.
   - Notice the automatic normalization: weights always sum to $1.00$.
3. **Observe Recalculation:**
   - The lab recalculates the TOPSIS decision matrix in memory.
   - Point out: Candidate rankings shift dynamically. Under extreme security constraints, a safer, lower-privilege candidate becomes #1.
4. **Emphasize Academic Rigor:**  
   *"The Counterfactual Lab demonstrates why deterministic multi-agent scoring is superior to monolithic heuristics. Operations engineers can audit 'What would the system have decided if security had been prioritized over MTTR?' without altering past records."*

---

## 9. Evaluation Results & Statistical Verification Walkthrough

Open the generated evaluation artifacts in `results/reports/` and `EVALUATION_METHOD.md`:

1. **Methodology Overview:**
   - 6 standardized fault scenarios (backend crashes, DB slow queries, thread exhaustion, disk fill, network partition, memory leak).
   - 4 comparison strategies:
     1. `safeopsai`: Full multi-agent pipeline with TOPSIS and sandbox.
     2. `naive_restart`: Industry standard default (blind restart).
     3. `no_sandbox`: Multi-agent without pre-production validation.
     4. `no_multi_agent`: Single-agent heuristic decision maker.
2. **Empirical Results (90 Measured Runs):**
   - **MTTR Reduction:** SafeOpsAI achieved an average MTTR of **28.4s** vs. **94.2s** for Naive Restart (a **69.8% reduction**).
   - **Rollback Rate:** SafeOpsAI had a **0.0% rollback rate** vs. **43.3%** for Naive Restart and **26.7%** for No-Sandbox.
3. **Statistical Significance:**
   - Highlight the non-parametric **Wilcoxon Signed-Rank Test**:
     $$W = 0.0, \quad p = 0.0049 \quad (p < 0.01)$$
   - Point out that because $p < 0.01$, the null hypothesis (that performance difference is due to chance) is rejected with 99% statistical confidence.

---

## 10. Contingency & Fallback Plan

| Potential Issue During Review | Instant Remediation | Presenter Talking Point |
|:---|:---|:---|
| **Ollama is slow or offline** | Automatic rule-based engine triggers seamlessly. | *"Notice our graceful degradation: when LLM latency exceeds bounds, the deterministic rule engine guarantees immediate diagnosis."* |
| **Port conflict on startup** | Run `.\stop-safeopsai.ps1` to clear old processes, then restart. | *"Our launcher tracks PIDs in `.run/services.json` ensuring clean shutdown and port release."* |
| **Examiner asks for raw data** | Open `results/raw/experiment_runs.csv` in Excel or VS Code. | *"Every timestamp, latency measurement, and sandbox signal across all 90 runs is recorded in immutable CSV format."* |
| **Examiner asks about Docker** | Show `start-safeopsai.ps1` running native Windows processes. | *"The platform runs natively on Windows using local PostgreSQL and background services, removing container virtualization overhead."* |

---

## 11. Clean Shutdown Procedure

When the review concludes, terminate all background processes cleanly:

```powershell
.\stop-safeopsai.ps1
```

### What This Does:
1. Reads all registered PIDs from `.run\services.json`.
2. Sends graceful stop signals to backend, agent microservices, and frontend.
3. Clears PID tracking records while preserving audit logs in `./logs/`.
4. Verifies ports 8000–8006 and 3000 are freed.
