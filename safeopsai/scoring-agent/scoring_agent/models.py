"""
SafeOpsAI — Scoring Agent: Domain Models
==========================================
Data structures for the Cost / Reliability / Security scoring pipeline.

Pure Python stdlib — no external dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class CandidateContext:
    """
    Incident + diagnosis context used by the scorers.
    This is exactly what the coordinator (Phase 3) will need later.
    """
    incident_id:        int
    incident_type:      str = ""
    service:            str = ""
    severity:           str = ""
    fault_type:         str = ""
    root_cause_service: str = ""
    cause_type:         str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "incident_id":        self.incident_id,
            "incident_type":      self.incident_type,
            "service":            self.service,
            "severity":           self.severity,
            "fault_type":         self.fault_type,
            "root_cause_service": self.root_cause_service,
            "cause_type":         self.cause_type,
        }


@dataclass
class Candidate:
    """A single remediation candidate (mirrors the RCA's remediation_candidates)."""
    action:                   str
    target:                   str = "backend"
    description:              str = ""
    priority:                 int = 1
    estimated_downtime_seconds: int = 0
    reversible:               bool = True
    proposed_by_rca:          bool = True   # True = LLM-proposed, False = canonical set

    @classmethod
    def from_dict(cls, d: dict[str, Any], proposed_by_rca: bool = True) -> "Candidate":
        return cls(
            action                    = str(d.get("action", "investigate")),
            target                    = str(d.get("target", "backend")),
            description               = str(d.get("description", "")),
            priority                  = int(d.get("priority", 1) or 1),
            estimated_downtime_seconds = int(d.get("estimated_downtime_seconds", 0) or 0),
            reversible                = bool(d.get("reversible", True)),
            proposed_by_rca           = proposed_by_rca,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "action":                     self.action,
            "target":                     self.target,
            "description":                self.description,
            "priority":                   self.priority,
            "estimated_downtime_seconds": self.estimated_downtime_seconds,
            "reversible":                 self.reversible,
            "proposed_by_rca":            self.proposed_by_rca,
        }


@dataclass
class ScoredDimension:
    """
    One criterion's score for one candidate.
    `components` holds the full arithmetic breakdown so the score is auditable.
    """
    criterion:     str
    score:         float                       # 0–10, higher = better
    justification: str
    components:    dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "criterion":     self.criterion,
            "score":         round(self.score, 2),
            "justification": self.justification,
            "components":    self.components,
        }


@dataclass
class ScoredCandidate:
    """A candidate with all three criterion scores attached."""
    candidate: Candidate
    scores:    dict[str, ScoredDimension] = field(default_factory=dict)  # criterion → dimension

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate": self.candidate.to_dict(),
            "scores":    {k: v.to_dict() for k, v in self.scores.items()},
        }