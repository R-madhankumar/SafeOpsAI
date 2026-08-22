"""
SafeOpsAI Evaluation — Base Strategy Abstract Class
====================================================
Defines the uniform interface for all evaluated remediation strategies.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional
from pydantic import BaseModel


class StrategyResult(BaseModel):
    strategy_name: str
    incident_id: int
    success: bool
    decision_at: Optional[str] = None
    remediation_started_at: Optional[str] = None
    recovered_at: Optional[str] = None
    decision_latency_seconds: float = 0.0
    remediation_latency_seconds: float = 0.0
    selected_action: str = ""
    top_ranked_candidate: str = ""
    sandbox_pass: Optional[bool] = None
    candidate_fallback_count: int = 0
    remediation_attempts: int = 1
    rollback_performed: bool = False
    recovery_score: float = 0.0
    escalated: bool = False
    final_outcome: str = "COMPLETED"
    notes: str = ""


class BaseStrategy(ABC):
    """Abstract base strategy for evaluation harness."""

    def __init__(self, name: str):
        self.name = name

    @abstractmethod
    async def execute(
        self,
        incident_id: int,
        scenario_id: str,
        target_service: str,
        fault_type: str,
        mock_mode: bool = False,
    ) -> StrategyResult:
        """Execute remediation strategy for an incident."""
        pass
