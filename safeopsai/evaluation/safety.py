"""
SafeOpsAI Evaluation — Safety Guard
====================================
Prevents evaluation fault injections against production environments.
Requires ENVIRONMENT=simulation or SAFEOPS_ENV=simulation.
"""

import os
import sys
import logging

log = logging.getLogger("safeopsai.evaluation.safety")


class SafetyViolationError(RuntimeError):
    """Raised when environment is not explicitly marked as simulation."""
    pass


def check_safety_guard(mode_override: str | None = None) -> None:
    """
    Verifies that the execution environment is explicitly set to 'simulation'.
    Refuses execution if environment is production or undefined.
    """
    mode = mode_override or os.getenv("ENVIRONMENT") or os.getenv("SAFEOPS_ENV") or ""
    mode = mode.strip().lower()

    if mode != "simulation":
        msg = (
            f"SAFETY GUARD FAILURE: Target environment mode '{mode}' is NOT 'simulation'.\n"
            f"The evaluation harness MUST NEVER run against a production environment!\n"
            f"Set ENVIRONMENT=simulation or SAFEOPS_ENV=simulation to authorize fault injection."
        )
        log.error(msg)
        raise SafetyViolationError(msg)

    log.info("Safety guard PASSED — Environment verified as '%s'.", mode)
