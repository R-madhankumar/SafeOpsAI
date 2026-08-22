"""
Root Cause Agent — Polling Loop
==================================
Polls the rca_queue view for open incidents that need diagnosis,
runs the Analyzer on each one, and writes results to agent_decisions.

Concurrency: uses asyncio.Semaphore to cap simultaneous LLM calls
(Ollama is single-threaded on CPU; more concurrent calls don't help).

Self-monitoring: exposes Prometheus metrics for the agent's own health.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

from prometheus_client import Counter, Gauge, Histogram

from .analyzer import Analyzer
from .config import cfg
from .db import (
    close_pool,
    fetch_rca_queue,
    init_pool,
    mark_diagnosing,
    reset_diagnosing,
    write_diagnosis,
)
from .llm_client import LLMClient
from .models import RCAStatus

log = logging.getLogger("rca.agent")

# ── Self-monitoring metrics ────────────────────────────────────────────────
_polls          = Counter("rca_agent_polls_total",         "Total RCA polling cycles")
_poll_errors    = Counter("rca_agent_poll_errors_total",   "Failed RCA polling cycles")
_diagnosed      = Counter("rca_diagnoses_total",           "Diagnoses completed",
                          ["method", "cause_type"])
_failed         = Counter("rca_diagnoses_failed_total",    "Diagnoses that failed (no fallback)")
_queue_length   = Gauge(  "rca_queue_length",              "Incidents waiting for RCA")
_active_diag    = Gauge(  "rca_active_diagnoses",          "In-flight LLM diagnoses")
_diag_latency   = Histogram(
    "rca_diagnosis_duration_seconds",
    "Wall-clock time to complete one diagnosis",
    buckets=[1, 5, 10, 20, 30, 60, 90, 120, 180],
)
_llm_healthy    = Gauge("rca_llm_healthy", "1 if Ollama is reachable with the target model")


# ── Shared agent state (exposed to API /status) ────────────────────────────
class AgentState:
    def __init__(self) -> None:
        self.started_at:       str  = datetime.now(timezone.utc).isoformat()
        self.last_poll_at:     str  = ""
        self.last_poll_ok:     bool = False
        self.total_polls:      int  = 0
        self.total_errors:     int  = 0
        self.total_diagnosed:  int  = 0
        self.total_fallbacks:  int  = 0
        self.total_failed:     int  = 0
        self.db_connected:     bool = False
        self.llm_healthy:      bool = False
        self.active_diagnoses: int  = 0
        self.model:            str  = cfg.llm_model
        self.ollama_url:       str  = cfg.llm_url


state = AgentState()


# ── Main loop ──────────────────────────────────────────────────────────────

async def run_agent() -> None:
    """
    Initialise DB + Analyzer, then poll rca_queue forever.
    Designed to run as an asyncio.Task.
    """
    log.info("Root Cause Agent starting — model=%s ollama=%s", cfg.llm_model, cfg.llm_url)

    try:
        await init_pool()
        state.db_connected = True
    except RuntimeError as exc:
        log.error("DB init failed: %s — diagnoses will not be stored", exc)

    analyzer  = Analyzer()
    llm_check = LLMClient()
    sem       = asyncio.Semaphore(cfg.max_concurrent)

    # Initial LLM health check
    state.llm_healthy = await llm_check.is_healthy()
    _llm_healthy.set(1 if state.llm_healthy else 0)
    if state.llm_healthy:
        log.info("Ollama reachable — model %r is available", cfg.llm_model)
    else:
        log.warning(
            "Ollama not reachable at %s or model %r not loaded. "
            "Fallback mode will be used until Ollama becomes available.",
            cfg.llm_url, cfg.llm_model,
        )

    try:
        while True:
            cycle_start = time.monotonic()
            await _poll_cycle(analyzer, llm_check, sem)
            elapsed = time.monotonic() - cycle_start
            await asyncio.sleep(max(0.0, cfg.poll_interval - elapsed))
    except asyncio.CancelledError:
        log.info("RCA polling loop cancelled — shutting down")
    finally:
        await close_pool()


async def _poll_cycle(
    analyzer: Analyzer,
    llm_check: LLMClient,
    sem: asyncio.Semaphore,
) -> None:
    """One poll cycle: fetch queue → dispatch diagnoses → update state."""
    _polls.inc()
    state.total_polls += 1

    try:
        # Periodic LLM health refresh (every 5 cycles)
        if state.total_polls % 5 == 1:
            state.llm_healthy = await llm_check.is_healthy()
            _llm_healthy.set(1 if state.llm_healthy else 0)

        queue = await fetch_rca_queue(limit=cfg.max_concurrent)
        _queue_length.set(len(queue))

        if not queue:
            state.last_poll_at = datetime.now(timezone.utc).isoformat()
            state.last_poll_ok = True
            return

        log.info("RCA queue: %d incident(s) to diagnose", len(queue))

        # Dispatch all items in the queue concurrently (capped by semaphore)
        tasks = [
            asyncio.create_task(_diagnose_one(request, analyzer, sem))
            for request in queue
        ]
        await asyncio.gather(*tasks, return_exceptions=True)

        state.last_poll_at = datetime.now(timezone.utc).isoformat()
        state.last_poll_ok = True

    except asyncio.CancelledError:
        raise
    except Exception as exc:
        log.error("RCA poll cycle error: %s", exc, exc_info=True)
        _poll_errors.inc()
        state.total_errors += 1
        state.last_poll_ok = False


async def _diagnose_one(request, analyzer: Analyzer, sem: asyncio.Semaphore) -> None:
    """Diagnose a single incident within the semaphore limit."""
    async with sem:
        # Atomically claim the incident (prevents duplicate diagnoses)
        if not state.db_connected:
            log.warning("DB unavailable — skipping diagnosis for incident %d", request.incident_id)
            return

        claimed = await mark_diagnosing(request.incident_id)
        if not claimed:
            log.debug("Incident %d already claimed by another worker", request.incident_id)
            return

        _active_diag.inc()
        state.active_diagnoses += 1
        t0 = time.monotonic()

        try:
            result = await analyzer.run(request)
            wall_s = time.monotonic() - t0
            _diag_latency.observe(wall_s)

            if result.status in (RCAStatus.COMPLETED, RCAStatus.FALLBACK):
                await write_diagnosis(result.output)
                _diagnosed.labels(
                    method     = result.output.diagnosis_method,
                    cause_type = result.output.cause_type,
                ).inc()
                if result.status == RCAStatus.FALLBACK:
                    state.total_fallbacks += 1
                else:
                    state.total_diagnosed += 1

                log.info(
                    "Diagnosis complete: incident_id=%d method=%s cause=%s "
                    "confidence=%.2f wall_s=%.1f",
                    request.incident_id,
                    result.output.diagnosis_method,
                    result.output.cause_type,
                    result.output.confidence,
                    wall_s,
                )

            elif result.status == RCAStatus.FAILED:
                _failed.inc()
                state.total_failed += 1
                # Release the diagnosing lock so a later cycle can retry
                await reset_diagnosing(request.incident_id)
                log.error(
                    "Diagnosis failed for incident %d: %s", request.incident_id, result.error
                )

        except Exception as exc:
            log.error("Unexpected error diagnosing incident %d: %s", request.incident_id, exc)
            await reset_diagnosing(request.incident_id)
            _failed.inc()
        finally:
            _active_diag.dec()
            state.active_diagnoses = max(0, state.active_diagnoses - 1)
