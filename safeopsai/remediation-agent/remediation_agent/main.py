"""
SafeOpsAI — Remediation Agent: Main Entry Point & Background Polling Loop
=========================================================================
Polls remediation_queue for sandbox-authorized incidents, executes autonomous
risk-aware production remediation, post-monitoring, and automatic rollback on port :8006.
"""

import asyncio
import logging
import time
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from .api import router, controller
from .config import API_PORT, LOG_LEVEL, cfg
from .db import (
    close_pool,
    fetch_incident_context,
    fetch_remediation_queue,
    get_attempt_count_for_incident,
    init_pool,
    mark_remediating,
    reset_remediating,
    update_incident_status,
    update_remediation_action_record,
    write_agent_decision,
)

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
log = logging.getLogger("remediation_agent.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Autonomous Remediation & Rollback Agent starting on port %d", API_PORT)
    try:
        await init_pool()
    except RuntimeError as exc:
        log.error("DB init failed: %s — agent will poll when DB becomes available", exc)

    poll_task = asyncio.create_task(run_polling_loop())

    yield

    log.info("Remediation Agent shutting down")
    poll_task.cancel()
    try:
        await poll_task
    except asyncio.CancelledError:
        pass
    await close_pool()


app = FastAPI(
    title="SafeOpsAI Risk-Aware Remediation Controller",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(router)


async def run_polling_loop() -> None:
    """Polls the remediation_queue continuously for sandbox-authorized incidents."""
    while True:
        try:
            await _poll_cycle()
        except asyncio.CancelledError:
            break
        except Exception as exc:
            log.error("Remediation polling loop exception: %s", exc, exc_info=True)

        await asyncio.sleep(cfg.poll_interval)


async def _poll_cycle() -> None:
    queue = await fetch_remediation_queue(limit=cfg.max_concurrent)
    if not queue:
        return

    log.info("Remediation Queue: %d incident(s) awaiting production remediation", len(queue))

    for item in queue:
        incident_id = item["incident_id"]
        claimed = await mark_remediating(incident_id)
        if not claimed:
            continue

        log.info("Processing Autonomous Production Remediation for Incident %d", incident_id)

        try:
            context = await fetch_incident_context(incident_id)
            attempt_number = (await get_attempt_count_for_incident(incident_id)) + 1

            res = await controller.execute_remediation_lifecycle(
                incident_id=incident_id,
                action_type=item.get("action_type", "restart_service"),
                target_service=item.get("target_service", "backend"),
                sandbox_action_id=item.get("sandbox_action_id", 0),
                attempt_number=attempt_number,
                context=context,
            )

            sandbox_id = item.get("sandbox_action_id", 0)
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
                reasoning=f"Remediation lifecycle completed: status={res.status} score={res.recovery_score}",
                raw_output=res.model_dump(),
                elapsed_ms=250,
            )

        except Exception as exc:
            log.error("Failed remediation lifecycle for incident %d: %s", incident_id, exc, exc_info=True)
            await reset_remediating(incident_id)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=API_PORT)
