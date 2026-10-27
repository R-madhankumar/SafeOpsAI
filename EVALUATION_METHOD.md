# SafeOpsAI — Scientific Evaluation & Experimental Methodology

> **Target:** Verification of Research Claims, Statistical Testing, and Benchmark Reproducibility  
> **Source Code:** `safeopsai/evaluation/`  
> **Raw Dataset:** `results/raw/experiment_runs.csv` (150 trial records)  
> **Summary Statistics:** `results/processed/summary.csv`  

---

## 1. Experimental Design & Objectives

The SafeOpsAI evaluation harness answers five core Research Questions (RQs):

- **RQ1 (MTTR Reduction)**: Does SafeOpsAI significantly reduce Mean Time to Recovery compared to conventional naive recovery strategies?
- **RQ2 (Multi-Agent Advantage)**: Does multi-agent domain scoring and MCDM coordination choose superior actions compared to static, single-objective heuristics?
- **RQ3 (Sandbox Impact)**: Does pre-remediation sandbox validation reduce failed production changes?
- **RQ4 (Rollback Reliability)**: Does automatic rollback prevent prolonged service degradation when remediation fails?
- **RQ5 (Decision Latency Overhead)**: Is the computational latency of the multi-agent decision pipeline negligible compared to cloud outage durations?

---

## 2. Comparison Strategies

The evaluation compares SafeOpsAI against a baseline and three ablation variants:

| Strategy | Description | Key Characteristic |
|---|---|---|
| `safeopsai` | Full closed-loop pipeline | RCA $\rightarrow$ 3 Scoring Agents $\rightarrow$ MCDM $\rightarrow$ Sandbox $\rightarrow$ Remediation $\rightarrow$ Rollback |
| `naive_restart` | Standard cloud heuristic | Detect $\rightarrow$ Restart container $\rightarrow$ Check health. No RCA, no MCDM, no sandbox |
| `no_sandbox` | Ablation variant | Full pipeline but skips sandbox validation; executes top candidate directly |
| `no_multi_agent` | Ablation variant | Single static rule: always restart regardless of root cause or domain costs |
| `no_rollback` | Ablation variant | Full pipeline but disables automatic rollback on post-remediation degradation |

---

## 3. Standardized Fault Scenarios

Six controlled fault scenarios evaluate diverse failure modes:

| Scenario ID | Name | Target Service | Failure Mode |
|---|---|---|---|
| `SCENARIO-01` | Backend Service Down | `backend` | HTTP 500, process exit, unreachable container |
| `SCENARIO-02` | Slow Database Queries | `database` | P95 DB query latency $> 1.0\text{s}$, connection backlog |
| `SCENARIO-03` | High Error Rate | `backend` | Application error rate $> 0.5\text{ errors/sec}$, HTTP 5xx spike |
| `SCENARIO-04` | Database Unavailable | `database` | Connection refused, DB health check failing, HTTP 503 |
| `SCENARIO-05` | CPU Resource Stress | `backend` | High CPU consumption, request queuing, latency degradation |
| `SCENARIO-06` | Bad Configuration | `backend` | Invalid environment variable, startup crash loop |

---

## 4. Benchmark Results & Findings

### Summary Across 90 Measured Trials (Excluding Warmup Runs):

| Strategy | Total Runs | Success Rate (%) | Mean MTTR (s) | Median MTTR (s) | Mean Downtime (s) | Rollback Rate (%) |
|---|---|---|---|---|---|---|
| **`safeopsai`** | **18** | **100.0%** | **0.52s** | **0.51s** | **0.83s** | **0.0%** |
| `naive_restart` | 18 | 33.3% | 80.04s | 120.00s | 80.20s | 0.0% |
| `no_multi_agent` | 18 | 33.3% | 80.00s | 120.00s | 80.21s | 0.0% |
| `no_sandbox` | 18 | 100.0% | 0.00s | 0.00s | 0.31s | 0.0% |
| `no_rollback` | 18 | 100.0% | 0.00s | 0.00s | 0.31s | 0.0% |

### Statistical Verification:
- **Wilcoxon Signed-Rank Test (`safeopsai` vs `naive_restart`)**: $p = 0.0049$ ($p < 0.01$). Demonstrates statistically significant MTTR reduction.
- **Decision Latency Overhead**: SafeOpsAI adds only $\approx 0.14\text{s}$ decision overhead (MCDM + scoring) while saving over $79\text{s}$ of downtime compared to repeated naive restarts.

---

## 5. Scope & Limitations of the Reported Evaluation

1. **Simulation vs. Live Physical Cloud**:
   - The reported benchmark runs were executed using the controlled simulation harness (`ENVIRONMENT=simulation`).
   - In simulation mode, transient faults (like container exits) are cleared by restarts, whereas persistent soft faults (such as artificially set latency flags) fail under naive restart but succeed under SafeOpsAI's root-cause-aware `clear_fault` action.
2. **Deterministic Mock Fallback in Tests**:
   - Unit tests use fast mock execution (`mock_mode=True`) to verify data pipelines and statistical algorithms without requiring hours of physical soak testing.
3. **Reproducibility Command**:
   To re-run the evaluation calculations and statistical test verification:
   ```powershell
   pytest safeopsai/evaluation/tests/test_evaluation.py -v
   ```
