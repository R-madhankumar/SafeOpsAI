"""
SafeOpsAI — Sandbox Agent: Main Entry Point & Background Polling Loop
======================================================================
Polls sandbox_queue for coordinated incidents, runs multi-signal sandbox
validation with adaptive candidate fallbacks, and serves REST APIs on :8005.
"""

import asyncio
import logging
import time
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from .api import router
from .config import API_PORT, LOG_LEVEL, cfg
from .db import (
    close_pool,
    fetch_coordinator_ranking,
    fetch_sandbox_queue,
    init_pool,
    mark_sandboxed,
    reset_sandboxed,
    write_agent_decision,
    write_remediation_action,
)
from .metrics import SANDBOX_QUEUE_LENGTH
from .validator import execute_adaptive_candidate_fallback

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
log = logging.getLogger("sandbox_agent.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Sandbox Validation Agent starting — multi-signal sandbox engine on port %d", API_PORT)
    try:
        await init_pool()
    except RuntimeError as exc:
        log.error("DB init failed: %s — agent will poll when DB becomes available", exc)

    # Start background polling task
    poll_task = asyncio.create_task(run_polling_loop())

    yield

    log.info("Sandbox Agent shutting down")
    poll_task.cancel()
    try:
        await poll_task
    except asyncio.CancelledError:
        pass
    await close_pool()


app = FastAPI(
    title="SafeOpsAI Sandbox Validation Agent",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(router)


async def run_polling_loop() -> None:
    """Polls the sandbox_queue continuously for coordinated incidents."""
    while True:
        try:
            await _poll_cycle()
        except asyncio.CancelledError:
            break
        except Exception as exc:
            log.error("Sandbox polling loop exception: %s", exc, exc_info=True)

        await asyncio.sleep(cfg.poll_interval)


async def _poll_cycle() -> None:
    """One poll cycle: fetch queue -> validate each incident with adaptive fallback."""
    queue = await fetch_sandbox_queue(limit=cfg.max_concurrent)
    SANDBOX_QUEUE_LENGTH.set(len(queue))

    if not queue:
        return

    log.info("Sandbox Queue: %d incident(s) awaiting validation", len(queue))

    for item in queue:
        incident_id = item["id"]
        claimed = await mark_sandboxed(incident_id)
        if not claimed:
            continue

        log.info("Processing Sandbox Validation for Incident %d (%s)", incident_id, item.get("incident_type"))

        try:
            candidates = await fetch_coordinator_ranking(incident_id)
            if not candidates:
                log.warning("Incident %d has no MCDM candidates to validate", incident_id)
                await reset_sandboxed(incident_id)
                continue

            t0 = time.monotonic()
            winner_res, all_attempts = await execute_adaptive_candidate_fallback(
                incident_id=incident_id,
                candidates=candidates,
                service_name=item.get("service", "backend"),
                fault_type=item.get("fault_type", "unknown"),
            )
            elapsed_ms = int((time.monotonic() - t0) * 1000)

            for att in all_attempts:
                aid = await write_remediation_action(att)
                att.validation_id = aid

            final_res = winner_res or (all_attempts[-1] if all_attempts else None)
            score = final_res.validation_score if final_res else 0.0
            reasoning = (
                f"Sandbox validation complete: winner={final_res.action if final_res else 'none'} "
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
                elapsed_ms=elapsed_ms,
            )

            log.info(
                "Sandbox Validation completed for Incident %d: status=%s score=%.2f winner=%s",
                incident_id,
                final_res.status if final_res else "FAIL",
                score,
                final_res.action if final_res else "none",
            )

        except Exception as exc:
            log.error("Failed sandbox validation for incident %d: %s", incident_id, exc, exc_info=True)
            await reset_sandboxed(incident_id)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=API_PORT)
