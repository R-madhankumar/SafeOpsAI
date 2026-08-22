"""
Incident Agent — Rule-Based Detector
======================================
Evaluates the 6 detection rules against a MetricsSnapshot.
Maintains in-memory ConditionState for the sustained-condition timer
and deduplication across polling cycles.

Design
------
Each rule has a fingerprint key: "<INCIDENT_TYPE>:<service>".
State machine per rule:

  ┌──────────────────────────────────────────────┐
  │           condition false                     │
  │  IDLE ─────────────────────────────► IDLE     │
  │    │                                          │
  │    │ condition true                           │
  │    ▼                                          │
  │  PENDING (for_seconds timer running)          │
  │    │ timer elapsed                            │
  │    ▼                                          │
  │  FIRING (incident opened in DB)               │
  │    │ condition false                          │
  │    ▼                                          │
  │  IDLE (incident resolved)                     │
  └──────────────────────────────────────────────┘

Thread safety: the detector is async-only; asyncio's single-threaded
event loop ensures no concurrent access to _states.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import NamedTuple

from .config import rules
from .models import (
    ConditionState,
    Incident,
    IncidentStatus,
    IncidentType,
    MetricsSnapshot,
    RULE_TO_FAULT_TYPE,
    RULE_TO_INCIDENT_TYPE,
    RULE_TO_SERVICE,
    Severity,
)

log = logging.getLogger("incident_agent.detector")


# ── Result types ──────────────────────────────────────────────────────────

class OpenResult(NamedTuple):
    """Detector wants a new incident opened."""
    incident: Incident


class ResolveResult(NamedTuple):
    """Detector wants an existing incident resolved."""
    fingerprint: str
    incident_id: int | None   # DB id if known


# ── Detector ─────────────────────────────────────────────────────────────

class RuleBasedDetector:
    """
    Stateful rule evaluator.

    Call evaluate(snapshot) on every polling cycle.
    Returns lists of OpenResult / ResolveResult for the agent to act on.
    """

    def __init__(self) -> None:
        # fingerprint → ConditionState (only exists while condition is active)
        self._pending: dict[str, ConditionState] = {}
        # fingerprint → ConditionState for currently FIRING conditions
        self._firing: dict[str, ConditionState] = {}

    # ── Public API ────────────────────────────────────────────────────────

    def evaluate(
        self, snap: MetricsSnapshot
    ) -> tuple[list[OpenResult], list[ResolveResult]]:
        """
        Evaluate all enabled rules against `snap`.

        Returns
        -------
        opens    : rules that have become incidents (for_seconds elapsed)
        resolves : previously firing rules whose condition is now false
        """
        opens:    list[OpenResult]   = []
        resolves: list[ResolveResult] = []

        for rule_name in rules.all_rule_names:
            if not rules.rule_enabled(rule_name):
                continue

            condition_true = self._check_condition(rule_name, snap)
            fingerprint    = self._fingerprint(rule_name)

            if condition_true:
                result = self._handle_true(rule_name, fingerprint, snap)
                if result:
                    opens.append(result)
            else:
                result = self._handle_false(rule_name, fingerprint)
                if result:
                    resolves.append(result)

        return opens, resolves

    # ── Active incident tracking ──────────────────────────────────────────

    def mark_opened(self, fingerprint: str, db_id: int) -> None:
        """Called by the agent after the DB INSERT succeeds."""
        if fingerprint in self._pending:
            state = self._pending.pop(fingerprint)
            state.incident_id = db_id
            state.firing = True
            self._firing[fingerprint] = state

    def mark_resolved(self, fingerprint: str) -> None:
        """Called by the agent after the DB UPDATE (resolve) succeeds."""
        self._firing.pop(fingerprint, None)
        self._pending.pop(fingerprint, None)

    def active_fingerprints(self) -> list[str]:
        return list(self._firing.keys())

    def pending_fingerprints(self) -> list[str]:
        return list(self._pending.keys())

    def active_count(self) -> int:
        return len(self._firing)

    # ── Condition evaluation (one method per rule) ────────────────────────

    def _check_condition(self, rule_name: str, snap: MetricsSnapshot) -> bool:
        try:
            if rule_name == "backend_down":
                val = snap.backend_up
                return val is not None and val == 0.0

            if rule_name == "database_down":
                val = snap.database_up
                return val is not None and val == 0.0

            if rule_name == "high_error_rate":
                val = snap.error_rate
                thr = rules.rule_threshold("high_error_rate")
                return val is not None and val > thr

            if rule_name == "high_5xx_ratio":
                val = snap.ratio_5xx
                thr = rules.rule_threshold("high_5xx_ratio")
                return val is not None and val > thr

            if rule_name == "high_latency":
                val = snap.p95_request_latency
                thr = rules.rule_threshold("high_latency", "p95_threshold_seconds")
                return val is not None and val > thr

            if rule_name == "slow_database":
                val = snap.p95_db_latency
                thr = rules.rule_threshold("slow_database", "p95_threshold_seconds")
                return val is not None and val > thr

        except Exception as exc:
            log.error("Error checking condition for rule '%s': %s", rule_name, exc)

        return False

    # ── State machine steps ───────────────────────────────────────────────

    def _handle_true(
        self,
        rule_name: str,
        fingerprint: str,
        snap: MetricsSnapshot,
    ) -> OpenResult | None:
        """
        Condition is currently true.
        Start or extend the pending timer; fire if threshold elapsed.
        Skip if already firing (deduplication).
        """
        # Already firing — do nothing (dedup)
        if fingerprint in self._firing:
            self._firing[fingerprint].refresh()
            return None

        # Start or refresh pending timer
        if fingerprint not in self._pending:
            self._pending[fingerprint] = ConditionState(rule_name=rule_name)
            log.debug(
                "Rule '%s' condition true — starting %ss timer",
                rule_name, rules.rule_for_seconds(rule_name),
            )
        else:
            self._pending[fingerprint].refresh()

        state = self._pending[fingerprint]
        for_seconds = rules.rule_for_seconds(rule_name)

        if state.duration() >= for_seconds:
            # Timer elapsed — build incident and signal open
            incident = self._build_incident(rule_name, snap)
            log.info(
                "Incident detected: type=%s service=%s severity=%s "
                "(condition true for %.1fs >= %.1fs)",
                incident.incident_type.value,
                incident.service,
                incident.severity.value,
                state.duration(),
                for_seconds,
            )
            return OpenResult(incident=incident)

        return None

    def _handle_false(
        self,
        rule_name: str,
        fingerprint: str,
    ) -> ResolveResult | None:
        """
        Condition is currently false.
        Clear pending timer if set; resolve firing incident if present.
        """
        # Clear pending timer (condition dropped before for_seconds elapsed)
        if fingerprint in self._pending:
            dur = self._pending.pop(fingerprint).duration()
            log.debug(
                "Rule '%s' condition cleared before threshold (was pending %.1fs)",
                rule_name, dur,
            )

        # If firing, signal resolution
        if fingerprint in self._firing:
            state = self._firing[fingerprint]
            db_id = state.incident_id
            log.info(
                "Incident resolved: type=%s service=%s (condition false after %.1fs)",
                RULE_TO_INCIDENT_TYPE[rule_name].value,
                RULE_TO_SERVICE[rule_name],
                state.duration(),
            )
            return ResolveResult(fingerprint=fingerprint, incident_id=db_id)

        return None

    # ── Helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _fingerprint(rule_name: str) -> str:
        it = RULE_TO_INCIDENT_TYPE[rule_name]
        svc = RULE_TO_SERVICE[rule_name]
        return Incident.make_fingerprint(it, svc)

    def _build_incident(self, rule_name: str, snap: MetricsSnapshot) -> Incident:
        it  = RULE_TO_INCIDENT_TYPE[rule_name]
        svc = RULE_TO_SERVICE[rule_name]
        sev_str = rules.rule_severity(rule_name)
        sev = Severity(sev_str) if sev_str in Severity._value2member_map_ else Severity.MEDIUM
        return Incident(
            incident_type    = it,
            service          = svc,
            fault_type       = RULE_TO_FAULT_TYPE[rule_name],
            severity         = sev,
            status           = IncidentStatus.OPEN,
            fingerprint      = self._fingerprint(rule_name),
            description      = rules.rule_description(rule_name),
            detection_source = "prometheus_rule",
            metrics_snapshot = snap,
        )
