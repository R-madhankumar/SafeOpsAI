"""
Incident Agent — Domain Models
================================
Enums and dataclasses that flow through detector → db → api.
No external dependencies — pure Python stdlib.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


# ── Enums ─────────────────────────────────────────────────────────────────

class IncidentType(str, Enum):
    BACKEND_DOWN    = "BACKEND_DOWN"
    DATABASE_DOWN   = "DATABASE_DOWN"
    HIGH_ERROR_RATE = "HIGH_ERROR_RATE"
    HIGH_5XX_RATIO  = "HIGH_5XX_RATIO"
    HIGH_LATENCY    = "HIGH_LATENCY"
    SLOW_DATABASE   = "SLOW_DATABASE"


class Severity(str, Enum):
    LOW      = "low"
    MEDIUM   = "medium"
    HIGH     = "high"
    CRITICAL = "critical"


class IncidentStatus(str, Enum):
    OPEN       = "open"
    RESOLVED   = "resolved"


# Map rule name → IncidentType
RULE_TO_INCIDENT_TYPE: dict[str, IncidentType] = {
    "backend_down":    IncidentType.BACKEND_DOWN,
    "database_down":   IncidentType.DATABASE_DOWN,
    "high_error_rate": IncidentType.HIGH_ERROR_RATE,
    "high_5xx_ratio":  IncidentType.HIGH_5XX_RATIO,
    "high_latency":    IncidentType.HIGH_LATENCY,
    "slow_database":   IncidentType.SLOW_DATABASE,
}

# Map rule name → affected service
RULE_TO_SERVICE: dict[str, str] = {
    "backend_down":    "backend",
    "database_down":   "database",
    "high_error_rate": "backend",
    "high_5xx_ratio":  "backend",
    "high_latency":    "backend",
    "slow_database":   "database",
}

# Map rule name → fault_type (mirrors alert_rules.yml labels)
RULE_TO_FAULT_TYPE: dict[str, str] = {
    "backend_down":    "container_down",
    "database_down":   "container_down",
    "high_error_rate": "high_error_rate",
    "high_5xx_ratio":  "high_error_rate",
    "high_latency":    "slow_queries",
    "slow_database":   "slow_queries",
}


# ── Metrics snapshot ──────────────────────────────────────────────────────

@dataclass
class MetricsSnapshot:
    """Prometheus values captured at the moment an incident is detected."""
    backend_up:          float | None = None
    database_up:         float | None = None
    error_rate:          float | None = None   # errors/s
    request_rate:        float | None = None   # req/s
    p95_request_latency: float | None = None   # seconds
    p95_db_latency:      float | None = None   # seconds
    ratio_5xx:           float | None = None   # fraction 0–1
    sampled_at:          str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "backend_up":          self.backend_up,
            "database_up":         self.database_up,
            "error_rate":          round(self.error_rate, 4) if self.error_rate is not None else None,
            "request_rate":        round(self.request_rate, 4) if self.request_rate is not None else None,
            "p95_request_latency": round(self.p95_request_latency, 4) if self.p95_request_latency is not None else None,
            "p95_db_latency":      round(self.p95_db_latency, 4) if self.p95_db_latency is not None else None,
            "ratio_5xx":           round(self.ratio_5xx, 4) if self.ratio_5xx is not None else None,
            "sampled_at":          self.sampled_at,
        }


# ── Active condition tracking (in-memory, not persisted) ─────────────────

@dataclass
class ConditionState:
    """
    Tracks how long a rule's condition has been continuously true.
    Used by the detector to enforce the for_seconds requirement.
    """
    rule_name:   str
    # Store wall-clock times as None and initialise lazily on first access
    # so that test mocks of time.monotonic() are in effect when the values
    # are first read.
    _first_seen: float | None = field(default=None, init=False, repr=False)
    _last_seen:  float | None = field(default=None, init=False, repr=False)
    firing:      bool  = False   # True once for_seconds has elapsed
    incident_id: int | None = None  # DB row id once the incident is opened

    @property
    def first_seen(self) -> float:
        if self._first_seen is None:
            self._first_seen = time.monotonic()
        return self._first_seen

    def duration(self) -> float:
        return time.monotonic() - self.first_seen

    def refresh(self) -> None:
        # last_seen kept for diagnostics; not used in duration calculation
        self._last_seen = time.monotonic()


# ── Incident record (mirrors DB row) ────────────────────────────────────

@dataclass
class Incident:
    """In-memory representation of a row in the incidents table."""
    incident_type:    IncidentType
    service:          str
    fault_type:       str
    severity:         Severity
    status:           IncidentStatus
    fingerprint:      str
    description:      str
    detection_source: str
    metrics_snapshot: MetricsSnapshot
    detected_at:      datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    resolved_at:      datetime | None = None
    mttr_seconds:     int | None = None
    db_id:            int | None = None   # set after INSERT

    @staticmethod
    def make_fingerprint(incident_type: IncidentType, service: str) -> str:
        return f"{incident_type.value}:{service}"
