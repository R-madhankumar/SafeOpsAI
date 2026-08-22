"""
SafeOpsAI — Coordinator Agent: Domain Models
==============================================
Thin models for the coordinator — the MCDM math itself lives in mcdm.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .mcdm import CandidateRow


@dataclass
class CoordinatorConfig:
    """Current runtime weights + method (mirrors coordinator_config table)."""
    cost_weight:        float = 0.3
    reliability_weight: float = 0.5
    security_weight:    float = 0.2
    method:             str = "weighted_sum"
    note:               str = ""

    def weights(self) -> dict[str, float]:
        return {
            "cost":        self.cost_weight,
            "reliability": self.reliability_weight,
            "security":    self.security_weight,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "cost_weight":        self.cost_weight,
            "reliability_weight": self.reliability_weight,
            "security_weight":    self.security_weight,
            "method":             self.method,
            "note":               self.note,
        }