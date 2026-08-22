"""
SafeOpsAI — Sandbox Agent: Data Models
========================================
Pydantic schemas for multi-signal validation requests and responses.
"""

from typing import Any, Dict, Optional
from pydantic import BaseModel, Field


class CheckDetails(BaseModel):
    health: bool = False
    readiness: bool = False
    container_running: bool = False
    database_available: bool = False
    error_rate_improved: bool = False
    latency_improved: bool = False


class BaselineMetrics(BaseModel):
    error_rate: float = 0.0
    p95_latency: float = 0.0
    health: bool = False
    readiness: bool = False
    database_available: bool = False


class AfterMetrics(BaseModel):
    error_rate: float = 0.0
    p95_latency: float = 0.0
    health: bool = False
    readiness: bool = False
    database_available: bool = False


class ValidationAttemptResult(BaseModel):
    validation_id: Optional[int] = None
    incident_id: int
    action: str
    target_service: str
    candidate_rank: int = 1
    status: str = "FAIL"  # PASS | FAIL
    validation_score: float = 0.0
    checks: CheckDetails = Field(default_factory=CheckDetails)
    baseline: BaselineMetrics = Field(default_factory=BaselineMetrics)
    after: AfterMetrics = Field(default_factory=AfterMetrics)
    reason: str = ""
    sandbox_started_at: str = ""
    sandbox_ended_at: str = ""
    selection_status: str = "rejected"  # selected | rejected | failed_sandbox
    execution_authorized: bool = False
    rollback_available: bool = True


class ValidationRequest(BaseModel):
    incident_id: int
    action: Optional[str] = None
    target_service: Optional[str] = None
