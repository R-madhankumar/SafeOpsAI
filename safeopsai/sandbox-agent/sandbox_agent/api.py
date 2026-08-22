"""
SafeOpsAI — Sandbox Agent: FastAPI REST Endpoints
=================================================
Exposes clean REST APIs for:
  POST /sandbox/validate
  GET  /sandbox/{validation_id}
  GET  /incidents/{incident_id}/validations
  GET  /health
  GET  /status
"""

import time
from typing import Any, Dict, List
from fastapi import APIRouter, HTTPException, Query
from starlette.responses import Response
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST

from .config import cfg
from .db import (
    fetch_coordinator_ranking,
    get_pool,
    get_validation_by_id,
    get_validations_for_incident,
    mark_sandboxed,
    write_agent_decision,
    write_remediation_action,
)
from .models import ValidationRequest
from .validator import execute_adaptive_candidate_fallback, validate_candidate_sandbox

router = APIRouter()


@router.get("/health")
async def health():
    """Liveness probe."""
    return {"status": "ok", "service": "sandbox-agent", "timestamp": time.time()}


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
        "service": "sandbox-agent",
        "status": "running",
        "db_connected": db_connected,
        "config": {
            "poll_interval": cfg.poll_interval,
            "health_check_timeout": cfg.health_check_timeout,
            "stabilization_period": cfg.stabilization_period,
            "min_validation_score": cfg.min_validation_score,
            "max_acceptable_latency": cfg.max_acceptable_latency,
            "max_acceptable_error_rate": cfg.max_acceptable_error_rate,
        },
    }


@router.get("/metrics")
async def metrics():
    """Prometheus scrape endpoint."""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@router.post("/sandbox/validate")
async def trigger_sandbox_validation(req: ValidationRequest):
    """
    POST /sandbox/validate
    Manually triggers sandbox validation for an incident.
    Reads candidate ranking from coordinator decision in PostgreSQL
    and executes adaptive candidate fallback validation.
    """
    incident_id = req.incident_id

    # If action is explicitly provided in request body
    if req.action:
        res = await validate_candidate_sandbox(
            incident_id=incident_id,
            candidate_rank=1,
            action=req.action,
            target_service=req.target_service or "backend",
            service_name="backend",
            fault_type="manual_request",
        )
        action_id = await write_remediation_action(res)
        res.validation_id = action_id
        await write_agent_decision(
            incident_id=incident_id,
            score=res.validation_score,
            reasoning=f"Manual validation: action={res.action} status={res.status} score={res.validation_score}",
            raw_output=res.model_dump(),
            elapsed_ms=100,
        )
        return res.model_dump()

    # Otherwise load candidates from MCDM coordinator ranking
    candidates = await fetch_coordinator_ranking(incident_id)
    if not candidates:
        raise HTTPException(
            status_code=404,
            detail=f"No MCDM coordinator candidates found for incident_id {incident_id}",
        )

    # Claim incident
    await mark_sandboxed(incident_id)

    winner_res, all_attempts = await execute_adaptive_candidate_fallback(
        incident_id=incident_id,
        candidates=candidates,
        service_name="backend",
        fault_type="coordinator_queue",
    )

    action_ids = []
    for att in all_attempts:
        aid = await write_remediation_action(att)
        att.validation_id = aid
        action_ids.append(aid)

    final_res = winner_res or (all_attempts[-1] if all_attempts else None)
    score = final_res.validation_score if final_res else 0.0
    reasoning = (
        f"Adaptive sandbox validation complete: winner={final_res.action if final_res else 'none'} "
        f"status={final_res.status if final_res else 'FAIL'} score={score} attempts={len(all_attempts)}"
    )

    raw_output = {
        "incident_id": incident_id,
        "winner": final_res.model_dump() if final_res else None,
        "attempts": [a.model_dump() for a in all_attempts],
        "total_attempts": len(all_attempts),
    }

    await write_agent_decision(
        incident_id=incident_id,
        score=score,
        reasoning=reasoning,
        raw_output=raw_output,
        elapsed_ms=150,
    )

    return {
        "incident_id": incident_id,
        "status": final_res.status if final_res else "FAIL",
        "validation_score": score,
        "winner": final_res.model_dump() if final_res else None,
        "attempts": [a.model_dump() for a in all_attempts],
    }


@router.get("/sandbox/{validation_id}")
async def get_validation_details(validation_id: int):
    """
    GET /sandbox/{validation_id}
    Retrieve structured validation details by remediation action ID.
    """
    res = await get_validation_by_id(validation_id)
    if not res:
        raise HTTPException(status_code=404, detail=f"Validation record {validation_id} not found")
    return res


@router.get("/incidents/{incident_id}/validations")
async def get_incident_validations(incident_id: int):
    """
    GET /incidents/{incident_id}/validations
    Retrieve all validation attempt records for a specific incident.
    """
    res = await get_validations_for_incident(incident_id)
    return {
        "incident_id": incident_id,
        "total_attempts": len(res),
        "validations": res,
    }
