"""
Root Cause Agent — Domain Models
===================================
All data structures that flow through:
  incident (DB row) → evidence → prompt → LLM → DiagnosisOutput → DB

No external dependencies — pure Python stdlib.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


# ── Enums ──────────────────────────────────────────────────────────────────

class RCAStatus(str, Enum):
    PENDING    = "pending"     # queued, not yet started
    RUNNING    = "running"     # LLM call in flight
    COMPLETED  = "completed"   # diagnosis written to DB
    FAILED     = "failed"      # LLM failed + no fallback
    FALLBACK   = "fallback"    # rule-based fallback used


class CauseType(str, Enum):
    """Top-level categories for root causes — used in structured output."""
    CONTAINER_CRASH      = "container_crash"
    DATABASE_OVERLOAD    = "database_overload"
    DATABASE_UNAVAILABLE = "database_unavailable"
    SLOW_QUERIES         = "slow_queries"
    HIGH_LOAD            = "high_load"
    MEMORY_PRESSURE      = "memory_pressure"
    NETWORK_FAULT        = "network_fault"
    CONFIGURATION_ERROR  = "configuration_error"
    CASCADING_FAILURE    = "cascading_failure"
    UNKNOWN              = "unknown"


class RemediationType(str, Enum):
    """Proposed remediation action types — consumed by Cost/Reliability/Security agents."""
    RESTART_SERVICE    = "restart_service"
    SCALE_UP           = "scale_up"
    ROLLBACK_CONFIG    = "rollback_config"
    CLEAR_FAULT        = "clear_fault"       # call /admin/fault/reset on backend
    INVESTIGATE        = "investigate"       # no automated fix; manual review needed
    RESTART_DATABASE   = "restart_database"
    REDEPLOY           = "redeploy"


# ── Evidence ──────────────────────────────────────────────────────────────

@dataclass
class MetricPoint:
    """A single metric value with label context."""
    name:  str
    value: float | None
    unit:  str = ""
    note:  str = ""


@dataclass
class MetricHistory:
    """A time-series window summary for the prompt."""
    metric_name: str
    window_minutes: int
    min_val:    float | None = None
    max_val:    float | None = None
    avg_val:    float | None = None
    current:    float | None = None

    def to_prompt_str(self) -> str:
        parts = []
        if self.current  is not None: parts.append(f"current={self.current:.3f}")
        if self.max_val  is not None: parts.append(f"max={self.max_val:.3f}")
        if self.avg_val  is not None: parts.append(f"avg={self.avg_val:.3f}")
        if self.min_val  is not None: parts.append(f"min={self.min_val:.3f}")
        summary = ", ".join(parts) if parts else "no data"
        return f"{self.metric_name} [{self.window_minutes}m]: {summary}"


@dataclass
class Evidence:
    """All observability data collected for a specific incident."""
    incident_id:       int
    incident_type:     str
    service:           str
    collected_at:      str                = field(
        default_factory=lambda: datetime.utcnow().isoformat()
    )
    # Current snapshot
    current_metrics:   list[MetricPoint]  = field(default_factory=list)
    # Historical windows
    metric_history:    list[MetricHistory] = field(default_factory=list)
    # Topology context
    service_deps:      list[str]          = field(default_factory=list)
    cascade_patterns:  list[str]          = field(default_factory=list)
    # Stored snapshot from the incident record
    detection_snapshot: dict[str, Any]    = field(default_factory=dict)


# ── Diagnosis ────────────────────────────────────────────────────────────

@dataclass
class RemediationCandidate:
    """
    A single proposed fix — structured so downstream agents can score it.
    The RCA agent proposes; Cost/Reliability/Security agents evaluate.
    """
    action:      RemediationType
    target:      str                # e.g. "backend", "database"
    description: str                # human-readable explanation
    priority:    int = 1            # 1=highest; used to order candidates
    estimated_downtime_seconds: int = 0
    reversible:  bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "action":                     self.action.value,
            "target":                     self.target,
            "description":                self.description,
            "priority":                   self.priority,
            "estimated_downtime_seconds": self.estimated_downtime_seconds,
            "reversible":                 self.reversible,
        }


@dataclass
class DiagnosisOutput:
    """
    Structured output from the Root Cause Agent.
    This is what gets stored in agent_decisions.root_cause_output (JSONB).
    All fields must be JSON-serialisable — no Python-specific types.
    """
    incident_id:           int
    root_cause_service:    str                       # "backend" | "database" | ...
    cause_type:            str                       # CauseType value
    confidence:            float                     # 0.0–1.0
    evidence_summary:      str                       # 1–3 sentences
    reasoning:             str                       # full LLM reasoning
    remediation_candidates: list[dict[str, Any]]    # list of RemediationCandidate.to_dict()
    diagnosis_method:      str = "llm"               # "llm" | "fallback"
    llm_model:             str = ""
    execution_time_ms:     int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "incident_id":            self.incident_id,
            "root_cause_service":     self.root_cause_service,
            "cause_type":             self.cause_type,
            "confidence":             round(self.confidence, 3),
            "evidence_summary":       self.evidence_summary,
            "reasoning":              self.reasoning,
            "remediation_candidates": self.remediation_candidates,
            "diagnosis_method":       self.diagnosis_method,
            "llm_model":              self.llm_model,
            "execution_time_ms":      self.execution_time_ms,
        }

    # Confidence is used as the score column in agent_decisions (0–10 scale)
    @property
    def score_0_10(self) -> float:
        return round(self.confidence * 10, 2)


@dataclass
class RCARequest:
    """A work item passed from the polling loop to the analyzer."""
    incident_id:       int
    incident_type:     str
    service:           str
    severity:          str
    fault_type:        str
    fingerprint:       str
    description:       str
    detected_at:       str
    metrics_snapshot:  dict[str, Any]


@dataclass
class RCAResult:
    """Return value from Analyzer.run() — success or failure."""
    request:  RCARequest
    status:   RCAStatus
    output:   DiagnosisOutput | None = None
    error:    str = ""
