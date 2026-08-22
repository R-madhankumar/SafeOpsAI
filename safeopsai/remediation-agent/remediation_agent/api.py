"""
SafeOpsAI — Remediation Agent: FastAPI REST Endpoints
=====================================================
Exposes clean REST APIs for:
  POST /remediation/execute
  GET  /remediation/{remediation_id}
  POST /remediation/{remediation_id}/rollback
  GET  /incidents/{incident_id}/remediation
  GET  /remediation/{remediation_id}/timeline
  GET  /health
  GET  /status
  GET  /metrics
"""

import time
from typing import Any, Dict, List
from fastapi import APIRouter, HTTPException, Query
from starlette.responses import Response
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST

from .config import cfg
from .controller import RemediationController
from .db import (
    fetch_incident_context,
    get_attempt_count_for_incident,
    get_pool,
    get_remediation_by_id,
    get_remediation_for_incident,
    mark_remediating,
    update_incident_status,
    update_remediation_action_record,
    write_agent_decision,
)
from .models import RemediationExecuteRequest, RemediationResponse
from .snapshot import SnapshotManager

router = APIRouter()
controller = RemediationController()


@router.get("/health")
async def health():
    """Liveness probe."""
    return {"status": "ok", "service": "remediation-agent", "timestamp": time.time()}


@router.get("/status")
async def status():
    """Agent status & configuration overview."""
    db_connected = False
    try:
        pool = get_pool()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        db_connected = True
    except Exception:
        db_connected = False

    return {
        "service": "remediation-agent",
        "status": "running",
        "db_connected": db_connected,
        "config": {
            "stabilization_seconds": cfg.stabilization_seconds,
            "max_remediation_attempts": cfg.max_remediation_attempts,
            "recovery_success_threshold": cfg.recovery_success_threshold,
            "degraded_threshold": cfg.degraded_threshold,
            "weights": cfg.weights,
        },
    }


@router.get("/metrics")
async def metrics():
    """Prometheus scrape endpoint."""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@router.post("/remediation/execute")
async def execute_remediation(req: RemediationExecuteRequest):
    """
    POST /remediation/execute
    Triggers production remediation execution for an incident that has passed sandbox validation.
    """
    incident_id = req.incident_id
    context = await fetch_incident_context(incident_id)

    # Check attempt count
    attempt_number = (await get_attempt_count_for_incident(incident_id)) + 1
    if attempt_number > cfg.max_remediation_attempts:
        await update_incident_status(incident_id, "escalated")
        raise HTTPException(
            status_code=400,
            detail=f"Maximum remediation attempts ({cfg.max_remediation_attempts}) exceeded for incident {incident_id}",
        )

    # Extract sandbox action info
    action_type = req.action
    target_service = req.target_service
    sandbox_id = req.remediation_id or 0

    if context and context.get("sandbox_action"):
        sb = context["sandbox_action"]
        action_type = action_type or sb.get("action_type")
        target_service = target_service or sb.get("target_service")
        sandbox_id = sandbox_id or sb.get("id", 0)

    action_type = action_type or "restart_service"
    target_service = target_service or "backend"

    # Claim incident
    await mark_remediating(incident_id)

    res = await controller.execute_remediation_lifecycle(
        incident_id=incident_id,
        action_type=action_type,
        target_service=target_service,
        sandbox_action_id=sandbox_id,
        execution_id=req.execution_id,
        attempt_number=attempt_number,
        context=context,
    )

    # Update PostgreSQL tables
    if sandbox_id:
        await update_remediation_action_record(
            action_id=sandbox_id,
            state=res.state,
            recovery_score=res.recovery_score,
            recovery_metrics=res.recovery.model_dump() if res.recovery else {},
            snapshot_id=res.snapshot_id or "",
            outcome="success" if res.status == "SUCCESS" else ("rolled_back" if res.rollback.performed else "failed"),
            notes=res.reason or "",
            rollback_performed=res.rollback.performed,
            rollback_reason=res.rollback.reason,
            attempt_number=attempt_number,
            max_attempts=cfg.max_remediation_attempts,
            escalated=res.status == "ESCALATED",
            escalation_reason=res.reason if res.status == "ESCALATED" else None,
        )

    if res.status == "SUCCESS":
        await update_incident_status(incident_id, "resolved")
    elif res.status == "ESCALATED":
        await update_incident_status(incident_id, "escalated")

    await write_agent_decision(
        incident_id=incident_id,
        score=res.recovery_score,
        reasoning=f"Remediation lifecycle outcome: status={res.status} state={res.state} score={res.recovery_score}",
        raw_output=res.model_dump(),
        elapsed_ms=200,
    )

    return res.model_dump()


@router.get("/remediation/{remediation_id}")
async def get_remediation_status(remediation_id: int):
    """
    GET /remediation/{remediation_id}
    Retrieve detailed status of a remediation execution by action ID.
    """
    res = await get_remediation_by_id(remediation_id)
    if not res:
        raise HTTPException(status_code=404, detail=f"Remediation action {remediation_id} not found")
    return res


@router.post("/remediation/{remediation_id}/rollback")
async def trigger_manual_rollback(remediation_id: int):
    """
    POST /remediation/{remediation_id}/rollback
    Manually triggers rollback for a remediation action using its recorded snapshot.
    """
    res = await get_remediation_by_id(remediation_id)
    if not res:
        raise HTTPException(status_code=404, detail=f"Remediation action {remediation_id} not found")

    snapshot_id = res.get("snapshot_id")
    if not snapshot_id:
        raise HTTPException(status_code=400, detail=f"No snapshot recorded for remediation action {remediation_id}")

    snap_mgr = SnapshotManager()
    ok, msg = await snap_mgr.restore_snapshot(snapshot_id)

    if ok:
        await update_remediation_action_record(
            action_id=remediation_id,
            state="ROLLED_BACK",
            recovery_score=0.0,
            recovery_metrics={},
            snapshot_id=snapshot_id,
            outcome="rolled_back",
            notes=f"Manual rollback executed: {msg}",
            rollback_performed=True,
            rollback_reason="Manual operator request",
        )
        await update_incident_status(res["incident_id"], "rolled_back")

    return {
        "remediation_id": remediation_id,
        "snapshot_id": snapshot_id,
        "rollback_success": ok,
        "message": msg,
    }


@router.get("/incidents/{incident_id}/remediation")
async def get_incident_remediations(incident_id: int):
    """
    GET /incidents/{incident_id}/remediation
    Retrieve all remediation actions performed for an incident.
    """
    actions = await get_remediation_for_incident(incident_id)
    return {
        "incident_id": incident_id,
        "total_actions": len(actions),
        "remediations": actions,
    }


@router.get("/remediation/{remediation_id}/timeline")
async def get_remediation_timeline(remediation_id: int):
    """
    GET /remediation/{remediation_id}/timeline
    Retrieve state transition timeline for a remediation execution.
    """
    res = await get_remediation_by_id(remediation_id)
    if not res:
        raise HTTPException(status_code=404, detail=f"Remediation action {remediation_id} not found")

    return {
        "remediation_id": remediation_id,
        "state": res.get("state"),
        "outcome": res.get("outcome"),
        "executed_at": res.get("executed_at"),
        "rollback_performed": res.get("rollback_performed"),
        "notes": res.get("notes"),
    }
