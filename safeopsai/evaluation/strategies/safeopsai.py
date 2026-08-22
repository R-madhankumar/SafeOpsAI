"""
SafeOpsAI Evaluation — Full SafeOpsAI Pipeline Adapter
======================================================
Executes the full SafeOpsAI autonomous operational pipeline:
RCA -> Cost/Reliability/Security Scorers -> MCDM Coordinator -> Sandbox Validation -> Production Remediation -> Monitoring & Rollback.
Treats SafeOpsAI as a black-box system.
"""

import asyncio
import datetime
import time
import logging
import httpx
from typing import Dict, Any

from .base import BaseStrategy, StrategyResult

log = logging.getLogger("safeopsai.evaluation.safeopsai")


class SafeOpsAIStrategy(BaseStrategy):
    """Full SafeOpsAI strategy adapter."""

    def __init__(
        self,
        rca_url: str = "http://localhost:8002",
        scoring_url: str = "http://localhost:8003",
        coordinator_url: str = "http://localhost:8004",
        sandbox_url: str = "http://localhost:8005",
        remediation_url: str = "http://localhost:8006",
        backend_url: str = "http://localhost:8000",
    ):
        super().__init__(name="safeopsai")
        self.rca_url = rca_url
        self.scoring_url = scoring_url
        self.coordinator_url = coordinator_url
        self.sandbox_url = sandbox_url
        self.remediation_url = remediation_url
        self.backend_url = backend_url

    async def execute(
        self,
        incident_id: int,
        scenario_id: str,
        target_service: str,
        fault_type: str,
        mock_mode: bool = False,
    ) -> StrategyResult:
        t0 = time.monotonic()
        decision_start = datetime.datetime.now(datetime.timezone.utc).isoformat()

        if mock_mode:
            await asyncio.sleep(0.3)
            # In mock mode, SafeOpsAI evaluates root cause, scores, MCDM, sandbox passes winner action, executes remediation
            selected_action = "clear_fault" if fault_type in ("slow_queries", "high_error_rate", "db_unavailable") else "restart_service"
            decision_lat = round(time.monotonic() - t0, 3)
            rem_start = datetime.datetime.now(datetime.timezone.utc).isoformat()
            t_rem0 = time.monotonic()

            await asyncio.sleep(0.2)
            rem_lat = round(time.monotonic() - t_rem0, 3)
            recovered_time = datetime.datetime.now(datetime.timezone.utc).isoformat()

            return StrategyResult(
                strategy_name=self.name,
                incident_id=incident_id,
                success=True,
                decision_at=decision_start,
                remediation_started_at=rem_start,
                recovered_at=recovered_time,
                decision_latency_seconds=decision_lat,
                remediation_latency_seconds=rem_lat,
                selected_action=selected_action,
                top_ranked_candidate=selected_action,
                sandbox_pass=True,
                candidate_fallback_count=0,
                remediation_attempts=1,
                rollback_performed=False,
                recovery_score=0.98,
                escalated=False,
                final_outcome="SUCCESS",
                notes="Full SafeOpsAI pipeline completed successfully with sandbox authorization",
            )

        # Live stack execution
        async with httpx.AsyncClient(timeout=30.0) as client:
            # 1. Trigger or poll sandbox validation
            sb_res = await client.post(f"{self.sandbox_url}/sandbox/validate", json={"incident_id": incident_id})
            decision_lat = round(time.monotonic() - t0, 3)
            sb_data = sb_res.json() if sb_res.status_code == 200 else {}
            winner = sb_data.get("winner") or {}
            selected_action = winner.get("action", "clear_fault")
            sandbox_passed = sb_data.get("status") == "PASS"

            rem_start = datetime.datetime.now(datetime.timezone.utc).isoformat()
            t_rem0 = time.monotonic()

            # 2. Trigger production remediation execution
            rem_res = await client.post(
                f"{self.remediation_url}/remediation/execute",
                json={"incident_id": incident_id, "action": selected_action, "target_service": target_service},
            )
            rem_lat = round(time.monotonic() - t_rem0, 3)
            rem_data = rem_res.json() if rem_res.status_code == 200 else {}

            status = rem_data.get("status", "FAIL")
            success = status == "SUCCESS"
            rollback = rem_data.get("rollback", {}).get("performed", False)
            rec_score = float(rem_data.get("recovery_score", 0.0))
            recovered_time = datetime.datetime.now(datetime.timezone.utc).isoformat() if success else None

            return StrategyResult(
                strategy_name=self.name,
                incident_id=incident_id,
                success=success,
                decision_at=decision_start,
                remediation_started_at=rem_start,
                recovered_at=recovered_time,
                decision_latency_seconds=decision_lat,
                remediation_latency_seconds=rem_lat,
                selected_action=selected_action,
                top_ranked_candidate=selected_action,
                sandbox_pass=sandbox_passed,
                candidate_fallback_count=len(sb_data.get("attempts", [])) - 1 if sb_data.get("attempts") else 0,
                remediation_attempts=1,
                rollback_performed=rollback,
                recovery_score=rec_score,
                escalated=status == "ESCALATED",
                final_outcome=status,
                notes=f"SafeOpsAI live execution: status={status} sandbox={sandbox_passed} score={rec_score}",
            )
