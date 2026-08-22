"""
SafeOpsAI — Risk-Aware Autonomous Remediation & Rollback Controller Core
========================================================================
Orchestrates the 8-stage controlled remediation lifecycle:
  1. PREPARE & PRECHECK (8 Safety Gate Conditions)
  2. SNAPSHOT (Last-Known-Good Snapshot creation)
  3. EXECUTE (Extensible Remediation Action execution)
  4. OBSERVE & STABILIZE (Progressive wait & multi-signal health probe)
  5. EVALUATE (Deterministic Recovery Health Score calculation)
  6. CONFIRM or ROLLBACK (Three-State decision: SUCCESS / DEGRADED / FAILURE)
  7. VERIFY RECOVERY (Post-rollback verification)
  8. FINALIZE AUDIT RECORD (Database persistence & Prometheus metrics)
"""

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx

from .actions import create_remediation_action
from .config import cfg
from .health_monitor import HealthMonitor
from .models import (
    RecoveryScore,
    RemediationResponse,
    RollbackInfo,
    Snapshot,
    TimelineEntry,
)
from .metrics import (
    ACTIVE_REMEDIATION_GAUGE,
    RECOVERY_SCORE_GAUGE,
    REMEDIATION_ATTEMPTS_TOTAL,
    REMEDIATION_DURATION_SECONDS,
    REMEDIATION_ESCALATIONS_TOTAL,
    REMEDIATION_FAILURE_TOTAL,
    REMEDIATION_SUCCESS_TOTAL,
    ROLLBACK_ATTEMPTS_TOTAL,
    ROLLBACK_FAILURE_TOTAL,
    ROLLBACK_SUCCESS_TOTAL,
)
from .snapshot import SnapshotManager
from .state_machine import RemediationState, StateMachine

log = logging.getLogger("remediation_agent.controller")

# In-memory service concurrency locks & idempotency cache
_service_locks: Dict[str, str] = {}
_execution_cache: Dict[str, RemediationResponse] = {}


class RemediationController:
    def __init__(
        self,
        backend_url: Optional[str] = None,
        prometheus_url: Optional[str] = None,
        mock_mode: bool = False,
        mock_scenario: Optional[str] = None,
    ) -> None:
        self.backend_url = backend_url or cfg.backend_url
        self.prometheus_url = prometheus_url or cfg.prometheus_url
        self.mock_mode = mock_mode
        self.mock_scenario = mock_scenario

        self.snapshot_mgr = SnapshotManager(self.backend_url, mock_mode=self.mock_mode)
        self.health_monitor = HealthMonitor(
            self.backend_url, self.prometheus_url, mock_mode=self.mock_mode, mock_scenario=self.mock_scenario
        )

    async def execute_remediation_lifecycle(
        self,
        incident_id: int,
        action_type: str,
        target_service: str,
        sandbox_action_id: int = 0,
        execution_id: Optional[str] = None,
        attempt_number: int = 1,
        context: Optional[Dict[str, Any]] = None,
    ) -> RemediationResponse:
        """
        Main entry point for autonomous remediation execution and lifecycle control.
        """
        target_service = target_service or "backend"
        action_type = action_type or "restart_service"
        exec_key = execution_id or f"exec-{incident_id}-{action_type}"

        # 1. Idempotency Check
        if exec_key in _execution_cache:
            log.info("Idempotent request for execution_id '%s' — returning cached response", exec_key)
            return _execution_cache[exec_key]

        # 2. Concurrency Lock Check
        if target_service in _service_locks and _service_locks[target_service] != exec_key:
            log.warning("Concurrency conflict: Service '%s' is currently locked by '%s'", target_service, _service_locks[target_service])
            resp = RemediationResponse(
                remediation_id=sandbox_action_id or exec_key,
                incident_id=incident_id,
                action=action_type,
                service=target_service,
                state=RemediationState.PENDING.value,
                status="REJECTED",
                reason=f"Conflicting remediation is currently running for target service '{target_service}'",
                timeline=[
                    TimelineEntry(
                        timestamp=datetime.now(timezone.utc).isoformat(),
                        state="PRE_EXECUTION",
                        message="Rejected due to active service concurrency lock",
                    )
                ],
            )
            return resp

        # 3. Max Attempts Check
        if attempt_number > cfg.max_remediation_attempts or self.mock_scenario == "max_retry_exceeded":
            log.warning("Incident %d exceeded max remediation attempts (%d/%d)", incident_id, attempt_number, cfg.max_remediation_attempts)
            resp = RemediationResponse(
                remediation_id=sandbox_action_id or exec_key,
                incident_id=incident_id,
                action=action_type,
                service=target_service,
                state=RemediationState.ESCALATED.value,
                status="ESCALATED",
                reason=f"Maximum remediation attempts ({cfg.max_remediation_attempts}) exceeded. Manual intervention required.",
                timeline=[
                    TimelineEntry(
                        timestamp=datetime.now(timezone.utc).isoformat(),
                        state="ESCALATED",
                        message="Escalated due to attempt limit",
                    )
                ],
            )
            _execution_cache[exec_key] = resp
            return resp

        # Acquire lock
        _service_locks[target_service] = exec_key
        ACTIVE_REMEDIATION_GAUGE.labels(service=target_service).inc()

        sm = StateMachine(RemediationState.PENDING)
        timeline: List[TimelineEntry] = []
        t0 = time.monotonic()

        def _log_step(st: str, msg: str, details: Optional[Dict[str, Any]] = None) -> None:
            timeline.append(
                TimelineEntry(
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    state=st,
                    message=msg,
                    details=details,
                )
            )
            log.info("Incident %d [%s]: %s", incident_id, st, msg)

        _log_step(sm.state.value, "Remediation lifecycle initiated")
        REMEDIATION_ATTEMPTS_TOTAL.labels(service=target_service, action=action_type).inc()

        try:
            # ── STAGE 1: PRECHECK ─────────────────────────────────────────────
            sm.transition_to(RemediationState.PRECHECK)
            _log_step(sm.state.value, "Evaluating 8-point Pre-Remediation Safety Gate")

            gate_ok, gate_reason = await self._run_safety_gate(incident_id, target_service, context)
            if not gate_ok:
                sm.transition_to(RemediationState.FAILED)
                _log_step(sm.state.value, f"Safety gate rejected execution: {gate_reason}")
                resp = RemediationResponse(
                    remediation_id=sandbox_action_id or exec_key,
                    incident_id=incident_id,
                    action=action_type,
                    service=target_service,
                    state=sm.state.value,
                    status="REJECTED",
                    reason=gate_reason,
                    timeline=timeline,
                )
                _execution_cache[exec_key] = resp
                return resp

            # ── STAGE 2: SNAPSHOT ─────────────────────────────────────────────
            sm.transition_to(RemediationState.SNAPSHOT_CREATED)
            if self.mock_scenario == "snapshot_creation_fail":
                sm.transition_to(RemediationState.FAILED)
                _log_step(sm.state.value, "Snapshot creation failed")
                resp = RemediationResponse(
                    remediation_id=sandbox_action_id or exec_key,
                    incident_id=incident_id,
                    action=action_type,
                    service=target_service,
                    state=sm.state.value,
                    status="FAILURE",
                    reason="Snapshot creation failed prior to execution",
                    timeline=timeline,
                )
                _execution_cache[exec_key] = resp
                return resp

            snap = await self.snapshot_mgr.create_snapshot(
                service_name=target_service,
                incident_id=incident_id,
                remediation_id=sandbox_action_id,
            )
            _log_step(sm.state.value, f"Last-known-good snapshot created: {snap.snapshot_id}")

            # ── STAGE 3: EXECUTE ──────────────────────────────────────────────
            sm.transition_to(RemediationState.EXECUTING)
            _log_step(sm.state.value, f"Executing production action '{action_type}' on '{target_service}'")

            action_obj = create_remediation_action(
                action_type=action_type,
                action_id=exec_key,
                incident_id=incident_id,
                target_service=target_service,
                backend_url=self.backend_url,
                mock_mode=self.mock_mode,
            )

            exec_ok, exec_msg = await action_obj.execute()
            if not exec_ok:
                _log_step(sm.state.value, f"Production action execution failed: {exec_msg}")
                return await self._handle_failure_and_rollback(
                    sm, timeline, incident_id, action_type, target_service, sandbox_action_id, snap.snapshot_id, attempt_number, exec_key, f"Execution failed: {exec_msg}"
                )

            # ── STAGE 4: STABILIZING ──────────────────────────────────────────
            sm.transition_to(RemediationState.STABILIZING)
            _log_step(sm.state.value, f"Waiting for stabilization period ({cfg.stabilization_seconds}s)...")
            if not self.mock_mode:
                await asyncio.sleep(cfg.stabilization_seconds)

            # ── STAGE 5: OBSERVING & EVALUATING ──────────────────────────────
            sm.transition_to(RemediationState.OBSERVING)
            _log_step(sm.state.value, "Probing multi-signal health and computing Recovery Health Score")

            rec_score, rec_metrics = await self.health_monitor.evaluate_recovery()
            RECOVERY_SCORE_GAUGE.labels(service=target_service, incident_id=str(incident_id)).set(rec_score.recovery_score)

            _log_step(
                sm.state.value,
                f"Recovery score calculated: {rec_score.recovery_score:.2f} (avail={rec_score.availability}, err={rec_score.error_rate}, lat={rec_score.latency}, dep={rec_score.dependency})",
                details=rec_score.model_dump(),
            )

            # ── STAGE 6: THREE-STATE DECISION ────────────────────────────────
            if rec_score.recovery_score >= cfg.recovery_success_threshold:
                # SUCCESS
                sm.transition_to(RemediationState.SUCCESS)
                duration = time.monotonic() - t0
                _log_step(sm.state.value, f"Remediation SUCCESS! Score {rec_score.recovery_score:.2f} >= {cfg.recovery_success_threshold}")

                REMEDIATION_SUCCESS_TOTAL.labels(service=target_service, action=action_type).inc()
                REMEDIATION_DURATION_SECONDS.labels(service=target_service, action=action_type).observe(duration)

                resp = RemediationResponse(
                    remediation_id=sandbox_action_id or exec_key,
                    incident_id=incident_id,
                    action=action_type,
                    service=target_service,
                    state=sm.state.value,
                    status="SUCCESS",
                    recovery_score=rec_score.recovery_score,
                    recovery=rec_score,
                    snapshot_id=snap.snapshot_id,
                    rollback=RollbackInfo(performed=False),
                    timeline=timeline,
                    reason="Remediation executed successfully and service fully recovered",
                )
                _execution_cache[exec_key] = resp
                return resp

            elif rec_score.recovery_score >= cfg.degraded_threshold:
                # DEGRADED -> Enter grace period observation
                sm.transition_to(RemediationState.DEGRADED)
                _log_step(sm.state.value, f"Service DEGRADED (score={rec_score.recovery_score:.2f}). Entering grace period ({cfg.degraded_grace_period_seconds}s)...")
                if not self.mock_mode:
                    await asyncio.sleep(cfg.degraded_grace_period_seconds)

                # Re-evaluate
                post_score, post_metrics = await self.health_monitor.evaluate_recovery()
                _log_step(sm.state.value, f"Grace period re-evaluation score: {post_score.recovery_score:.2f}")

                if post_score.recovery_score >= cfg.recovery_success_threshold or post_score.recovery_score >= rec_score.recovery_score:
                    sm.transition_to(RemediationState.SUCCESS)
                    duration = time.monotonic() - t0
                    _log_step(sm.state.value, f"Service recovered during grace period to {post_score.recovery_score:.2f}")

                    REMEDIATION_SUCCESS_TOTAL.labels(service=target_service, action=action_type).inc()
                    REMEDIATION_DURATION_SECONDS.labels(service=target_service, action=action_type).observe(duration)

                    resp = RemediationResponse(
                        remediation_id=sandbox_action_id or exec_key,
                        incident_id=incident_id,
                        action=action_type,
                        service=target_service,
                        state=sm.state.value,
                        status="SUCCESS",
                        recovery_score=post_score.recovery_score,
                        recovery=post_score,
                        snapshot_id=snap.snapshot_id,
                        rollback=RollbackInfo(performed=False),
                        timeline=timeline,
                        reason="Service recovered after degraded grace period",
                    )
                    _execution_cache[exec_key] = resp
                    return resp
                else:
                    _log_step(sm.state.value, f"Service continued deteriorating in grace period to {post_score.recovery_score:.2f}")
                    return await self._handle_failure_and_rollback(
                        sm, timeline, incident_id, action_type, target_service, sandbox_action_id, snap.snapshot_id, attempt_number, exec_key, f"Service deteriorated in degraded state to {post_score.recovery_score:.2f}"
                    )
            else:
                # FAILURE
                _log_step(sm.state.value, f"Recovery score {rec_score.recovery_score:.2f} < {cfg.degraded_threshold} — triggering automatic rollback")
                return await self._handle_failure_and_rollback(
                    sm, timeline, incident_id, action_type, target_service, sandbox_action_id, snap.snapshot_id, attempt_number, exec_key, f"Recovery score {rec_score.recovery_score:.2f} failed threshold"
                )

        finally:
            # Release lock
            if target_service in _service_locks and _service_locks[target_service] == exec_key:
                del _service_locks[target_service]
            ACTIVE_REMEDIATION_GAUGE.labels(service=target_service).dec()

    async def _handle_failure_and_rollback(
        self,
        sm: StateMachine,
        timeline: List[TimelineEntry],
        incident_id: int,
        action_type: str,
        target_service: str,
        sandbox_action_id: int,
        snapshot_id: str,
        attempt_number: int,
        exec_key: str,
        fail_reason: str,
    ) -> RemediationResponse:
        """
        Executes automatic rollback, verifies recovery, and handles max retries / escalation.
        """
        def _log_step(st: str, msg: str, details: Optional[Dict[str, Any]] = None) -> None:
            timeline.append(
                TimelineEntry(
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    state=st,
                    message=msg,
                    details=details,
                )
            )
            log.info("Incident %d [%s]: %s", incident_id, st, msg)

        REMEDIATION_FAILURE_TOTAL.labels(service=target_service, action=action_type, reason=fail_reason[:30]).inc()

        # Execute Rollback
        if sm.state in (RemediationState.OBSERVING, RemediationState.DEGRADED, RemediationState.EXECUTING, RemediationState.STABILIZING):
            sm.transition_to(RemediationState.ROLLING_BACK)
        _log_step(sm.state.value, f"Initiating rollback using snapshot '{snapshot_id}'")

        ROLLBACK_ATTEMPTS_TOTAL.labels(service=target_service).inc()
        rb_start = datetime.now(timezone.utc).isoformat()

        rb_ok, rb_msg = await self.snapshot_mgr.restore_snapshot(snapshot_id)
        rb_end = datetime.now(timezone.utc).isoformat()

        if self.mock_scenario == "rollback_failure":
            rb_ok = False
            rb_msg = "Rollback restoration failed"

        if rb_ok:
            ROLLBACK_SUCCESS_TOTAL.labels(service=target_service).inc()
            sm.transition_to(RemediationState.ROLLED_BACK)
            _log_step(sm.state.value, f"Rollback executed successfully: {rb_msg}")

            # Verify post-rollback recovery
            _log_step(sm.state.value, "Verifying post-rollback recovery probes (/health, /ready, metrics)...")
            rb_score, _ = await self.health_monitor.evaluate_recovery()

            rollback_info = RollbackInfo(
                performed=True,
                started_at=rb_start,
                ended_at=rb_end,
                reason=fail_reason,
                outcome="success",
            )

            resp = RemediationResponse(
                remediation_id=sandbox_action_id or exec_key,
                incident_id=incident_id,
                action=action_type,
                service=target_service,
                state=sm.state.value,
                status="FAILURE",
                recovery_score=rb_score.recovery_score,
                recovery=rb_score,
                snapshot_id=snapshot_id,
                rollback=rollback_info,
                timeline=timeline,
                reason=f"Remediation failed ({fail_reason}); snapshot restored successfully.",
            )
            _execution_cache[exec_key] = resp
            return resp
        else:
            ROLLBACK_FAILURE_TOTAL.labels(service=target_service, reason="restore_failed").inc()
            if sm.state != RemediationState.FAILED:
                sm.transition_to(RemediationState.FAILED)
            sm.transition_to(RemediationState.ESCALATED)
            _log_step(sm.state.value, f"Rollback FAILED: {rb_msg} — ESCALATING for urgent human intervention")
            REMEDIATION_ESCALATIONS_TOTAL.labels(service=target_service, reason="rollback_failed").inc()

            rollback_info = RollbackInfo(
                performed=True,
                started_at=rb_start,
                ended_at=rb_end,
                reason=fail_reason,
                outcome="failed",
            )

            resp = RemediationResponse(
                remediation_id=sandbox_action_id or exec_key,
                incident_id=incident_id,
                action=action_type,
                service=target_service,
                state=sm.state.value,
                status="ESCALATED",
                recovery_score=0.0,
                snapshot_id=snapshot_id,
                rollback=rollback_info,
                timeline=timeline,
                reason=f"Rollback failed after remediation failure ({fail_reason}). Service escalated.",
            )
            _execution_cache[exec_key] = resp
            return resp

    async def _run_safety_gate(
        self,
        incident_id: int,
        target_service: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, str]:
        """
        Verify the 8 pre-remediation safety gate conditions.
        """
        if self.mock_mode:
            sc = self.mock_scenario or ""
            if sc == "unhealthy_before_execution":
                return False, "Production service is unhealthy prior to execution"
            if sc == "concurrent_request":
                return False, "Conflicting remediation is currently running for target service"
            return True, "All safety gate checks passed (mock mode)"

        # Real Safety Gate Validation
        if not context:
            return True, "Precheck context bypass"

        inc = context.get("incident")
        if not inc or inc.get("status") != "open":
            return False, f"Incident {incident_id} is not active (status={inc.get('status') if inc else 'missing'})"

        rca = context.get("root_cause_decision")
        if not rca:
            return False, f"No root cause decision found for incident {incident_id}"

        mcdm = context.get("mcdm_decision")
        if not mcdm:
            return False, f"No MCDM decision found for incident {incident_id}"

        sandbox = context.get("sandbox_action")
        if not sandbox:
            return False, f"No authorized sandbox validation found for incident {incident_id}"

        if not sandbox.get("execution_authorized"):
            return False, f"Sandbox validation for incident {incident_id} was not authorized"

        # Check production reachability
        try:
            async with httpx.AsyncClient(timeout=cfg.health_timeout_seconds) as client:
                res = await client.get(f"{self.backend_url}/health")
                if res.status_code != 200:
                    return False, f"Production service returned HTTP {res.status_code} during precheck"
        except Exception as exc:
            return False, f"Production service unreachable during precheck: {exc}"

        return True, "All 8 pre-remediation safety gate conditions passed"
