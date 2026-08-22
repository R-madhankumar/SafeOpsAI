"""
SafeOpsAI — Remediation Lifecycle State Machine
================================================
Validates explicit state transitions across the 12 lifecycle states:

  PENDING -> PRECHECK -> SNAPSHOT_CREATED -> EXECUTING -> STABILIZING -> OBSERVING
  OBSERVING -> { SUCCESS, DEGRADED, ROLLING_BACK, FAILED, ESCALATED }
  DEGRADED  -> { SUCCESS, ROLLING_BACK, FAILED, ESCALATED }
  ROLLING_BACK -> { ROLLED_BACK, FAILED, ESCALATED }
  ROLLED_BACK  -> { ESCALATED }

Rejects invalid transitions (e.g., ROLLED_BACK -> EXECUTING).
"""

from enum import Enum
from typing import Dict, Set


class RemediationState(str, Enum):
    PENDING = "PENDING"
    PRECHECK = "PRECHECK"
    SNAPSHOT_CREATED = "SNAPSHOT_CREATED"
    EXECUTING = "EXECUTING"
    STABILIZING = "STABILIZING"
    OBSERVING = "OBSERVING"
    SUCCESS = "SUCCESS"
    DEGRADED = "DEGRADED"
    ROLLING_BACK = "ROLLING_BACK"
    ROLLED_BACK = "ROLLED_BACK"
    FAILED = "FAILED"
    ESCALATED = "ESCALATED"


VALID_TRANSITIONS: Dict[RemediationState, Set[RemediationState]] = {
    RemediationState.PENDING: {
        RemediationState.PRECHECK,
        RemediationState.FAILED,
        RemediationState.ESCALATED,
    },
    RemediationState.PRECHECK: {
        RemediationState.SNAPSHOT_CREATED,
        RemediationState.FAILED,
        RemediationState.ESCALATED,
    },
    RemediationState.SNAPSHOT_CREATED: {
        RemediationState.EXECUTING,
        RemediationState.FAILED,
        RemediationState.ESCALATED,
    },
    RemediationState.EXECUTING: {
        RemediationState.STABILIZING,
        RemediationState.FAILED,
        RemediationState.ESCALATED,
    },
    RemediationState.STABILIZING: {
        RemediationState.OBSERVING,
        RemediationState.FAILED,
        RemediationState.ESCALATED,
    },
    RemediationState.OBSERVING: {
        RemediationState.SUCCESS,
        RemediationState.DEGRADED,
        RemediationState.ROLLING_BACK,
        RemediationState.FAILED,
        RemediationState.ESCALATED,
    },
    RemediationState.DEGRADED: {
        RemediationState.SUCCESS,
        RemediationState.ROLLING_BACK,
        RemediationState.FAILED,
        RemediationState.ESCALATED,
    },
    RemediationState.ROLLING_BACK: {
        RemediationState.ROLLED_BACK,
        RemediationState.FAILED,
        RemediationState.ESCALATED,
    },
    RemediationState.ROLLED_BACK: {
        RemediationState.ESCALATED,
    },
    RemediationState.SUCCESS: set(),
    RemediationState.FAILED: {
        RemediationState.ESCALATED,
    },
    RemediationState.ESCALATED: set(),
}


class StateMachine:
    def __init__(self, initial_state: RemediationState = RemediationState.PENDING) -> None:
        self._current_state = initial_state

    @property
    def state(self) -> RemediationState:
        return self._current_state

    def transition_to(self, new_state: RemediationState | str) -> RemediationState:
        if isinstance(new_state, str):
            try:
                new_state = RemediationState(new_state.upper())
            except ValueError:
                raise ValueError(f"Unknown remediation state: '{new_state}'")

        if new_state not in VALID_TRANSITIONS.get(self._current_state, set()):
            raise ValueError(
                f"Invalid state transition from '{self._current_state.value}' to '{new_state.value}'"
            )

        self._current_state = new_state
        return self._current_state


def validate_transition(current: str, target: str) -> bool:
    try:
        curr_enum = RemediationState(current.upper())
        targ_enum = RemediationState(target.upper())
        return targ_enum in VALID_TRANSITIONS.get(curr_enum, set())
    except ValueError:
        return False
