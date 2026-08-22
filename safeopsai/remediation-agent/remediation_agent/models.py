"""
SafeOpsAI — Remediation Agent: Data Models
===========================================
Pydantic schemas for autonomous remediation lifecycle, snapshots, recovery scores, and API requests/responses.
"""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class Snapshot(BaseModel):
    snapshot_id: str
    service: str
    container_identity: str = "safeops-backend"
    image: str = "safeopsai-backend:latest"
    config_state: Dict[str, Any] = Field(default_factory=dict)
    health_state: Dict[str, Any] = Field(default_factory=dict)
    created_at: str = ""
    incident_id: int
    remediation_id: int = 0


class RecoveryScore(BaseModel):
    availability: float = 0.0
    error_rate: float = 0.0
    latency: float = 0.0
    dependency: float = 0.0
    recovery_score: float = 0.0


class RemediationExecuteRequest(BaseModel):
    incident_id: int
    remediation_id: Optional[int] = None
    action: Optional[str] = None
    target_service: Optional[str] = None
    execution_id: Optional[str] = None


class RollbackInfo(BaseModel):
    performed: bool = False
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    reason: Optional[str] = None
    outcome: Optional[str] = None


class TimelineEntry(BaseModel):
    timestamp: str
    state: str
    message: str
    details: Optional[Dict[str, Any]] = None


class RemediationResponse(BaseModel):
    remediation_id: Any
    incident_id: int
    action: str
    service: str
    state: str
    status: str  # SUCCESS / DEGRADED / FAILURE / REJECTED / ESCALATED
    recovery_score: float = 0.0
    recovery: Optional[RecoveryScore] = None
    snapshot_id: Optional[str] = None
    rollback: RollbackInfo = Field(default_factory=RollbackInfo)
    timeline: List[TimelineEntry] = Field(default_factory=list)
    reason: Optional[str] = None
