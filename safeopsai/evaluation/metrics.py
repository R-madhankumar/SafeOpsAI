"""
SafeOpsAI Evaluation — Metrics Collector & Computations
=========================================================
Computes MTTR, Downtime, Latency breakdowns, Rollback/Success/Failure/Escalation rates,
and Decision Quality metrics for individual runs and experiment campaigns.
"""

import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel


class ExperimentRunRecord(BaseModel):
    experiment_run_id: str
    experiment_id: str
    scenario_id: str
    strategy: str
    repetition: int
    is_warmup: bool = False
    status: str = "COMPLETED"

    started_at: str
    fault_injected_at: Optional[str] = None
    incident_detected_at: Optional[str] = None
    decision_at: Optional[str] = None
    remediation_started_at: Optional[str] = None
    recovered_at: Optional[str] = None

    mttr_seconds: float = 0.0
    downtime_seconds: float = 0.0
    detection_latency_seconds: float = 0.0
    decision_latency_seconds: float = 0.0
    remediation_latency_seconds: float = 0.0

    rollback: bool = False
    success: bool = False
    escalated: bool = False

    selected_remediation: str = ""
    top_ranked_candidate: str = ""
    sandbox_pass: Optional[bool] = None
    candidate_fallback_count: int = 0
    remediation_attempts: int = 1
    recovery_score: float = 0.0
    final_outcome: str = "COMPLETED"
    error_message: str = ""
    raw_logs: Dict[str, Any] = {}
    system_config: Dict[str, Any] = {}
    random_seed: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()


def calculate_latencies(
    fault_injected_at: str,
    incident_detected_at: Optional[str],
    decision_at: Optional[str],
    remediation_started_at: Optional[str],
    recovered_at: Optional[str],
    timeout_seconds: float = 120.0,
) -> Dict[str, float]:
    """Calculate latency metrics from timestamps."""
    def parse_ts(ts: Optional[str]) -> Optional[datetime.datetime]:
        if not ts:
            return None
        try:
            return datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except Exception:
            return None

    t_fault = parse_ts(fault_injected_at) or datetime.datetime.now(datetime.timezone.utc)
    t_det = parse_ts(incident_detected_at)
    t_dec = parse_ts(decision_at)
    t_rem = parse_ts(remediation_started_at)
    t_rec = parse_ts(recovered_at)

    det_lat = (t_det - t_fault).total_seconds() if t_det else 5.0
    dec_lat = (t_dec - t_det).total_seconds() if (t_dec and t_det) else 1.5
    rem_lat = (t_rec - t_rem).total_seconds() if (t_rec and t_rem) else 2.0

    if t_rec and t_det:
        mttr = max(0.0, (t_rec - t_det).total_seconds())
    else:
        mttr = timeout_seconds

    if t_rec and t_fault:
        downtime = max(0.0, (t_rec - t_fault).total_seconds())
    else:
        downtime = timeout_seconds

    return {
        "detection_latency_seconds": round(max(0.0, det_lat), 3),
        "decision_latency_seconds": round(max(0.0, dec_lat), 3),
        "remediation_latency_seconds": round(max(0.0, rem_lat), 3),
        "mttr_seconds": round(mttr, 3),
        "downtime_seconds": round(downtime, 3),
    }
