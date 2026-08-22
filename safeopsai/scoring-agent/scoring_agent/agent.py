"""
SafeOpsAI — Scoring Agent: Polling Loop
=========================================
Polls the scoring_queue view, runs the three deterministic scorers
(cost / reliability / security) over the RCA's remediation candidates,
and writes every score to agent_decisions.

Self-monitoring: exposes Prometheus metrics for the agent's own health.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

from prometheus_client import Counter, Gauge, Histogram

from .config import cfg
from .db import (
    close_pool,
    fetch_scoring_queue,
    init_pool,
    mark_scoring,
    reset_scoring,
    write_scored_candidates,
)
from .scoring import score_candidates

log = logging.getLogger("scoring_agent.agent")

# ── Self-monitoring metrics ────────────────────────────────────────────────
_polls           = Counter("scoring_agent_polls_total",       "Total scoring polling cycles")
_poll_errors     = Counter("scoring_agent_poll_errors_total", "Failed scoring polling cycles")
_incidents_scored = Counter("scoring_incidents_scored_total", "Incidents scored",
                            ["method"])
_score_rows      = Counter("scoring_decision_rows_total",     "agent_decisions rows written")
_failed          = Counter("scoring_incidents_failed_total", "Incidents that failed to score")
_queue_length    = Gauge(  "scoring_queue_length",            "Incidents waiting for scoring")
_active          = Gauge(  "scoring_active",                  "Incidents currently being scored")
_score_latency   = Histogram(
    "scoring_duration_seconds",
    "Wall-clock time to score one incident",
    buckets=[0.1, 0.25, 0.5, 1, 2, 5, 10],
)


# ── Shared agent state (exposed to API /status) ────────────────────────────
class AgentState:
    def __init__(self) -> None:
        self.started_at:     str = datetime.now(timezone.utc).isoformat()
        self.last_poll_at:   str = ""
        self.last_poll_ok:   bool = False
        self.total_polls:    int = 0
        self.total_errors:   int = 0
        self.total_scored:   int = 0
        self.total_failed:   int = 0
        self.db_connected:   bool = False


state = AgentState()


async def run_agent() -> None:
    """Initialise DB, then poll the scoring queue forever."""
    log.info("Scoring Agent starting — criteria: cost, reliability, security")

    try:
        await init_pool()
        state.db_connected = True
    except RuntimeError as exc:
        log.error("DB init failed: %s — scores will not be stored", exc)

    try:
        while True:
            cycle_start = time.monotonic()
            await _poll_cycle()
            elapsed = time.monotonic() - cycle_start
            await asyncio.sleep(max(0.0, cfg.poll_interval - elapsed))
    except asyncio.CancelledError:
        log.info("Scoring polling loop cancelled — shutting down")
    finally:
        await close_pool()


async def _poll_cycle() -> None:
    """One poll cycle: fetch queue → score → store."""
    _polls.inc()
    state.total_polls += 1

    try:
        queue = await fetch_scoring_queue(limit=cfg.max_concurrent)
        _queue_length.set(len(queue))

        if not queue:
            state.last_poll_at = datetime.now(timezone.utc).isoformat()
            state.last_poll_ok = True
            return

        log.info("Scoring queue: %d incident(s) to score", len(queue))

        for item in queue:
            await _score_one(item)

        state.last_poll_at = datetime.now(timezone.utc).isoformat()
        state.last_poll_ok = True

    except asyncio.CancelledError:
        raise
    except Exception as exc:
        log.error("Scoring poll cycle error: %s", exc, exc_info=True)
        _poll_errors.inc()
        state.total_errors += 1
        state.last_poll_ok = False


async def _score_one(item) -> None:
    """Score a single incident within the semaphore-free (CPU-only) path."""
    incident_id = item["incident_id"]
    if not state.db_connected:
        log.warning("DB unavailable — skipping scoring for incident %d", incident_id)
        return

    claimed = await mark_scoring(incident_id)
    if not claimed:
        log.debug("Incident %d already claimed by another worker", incident_id)
        return

    _active.inc()
    t0 = time.monotonic()
    try:
        ctx = item["context"]
        candidates = item["candidates"]
        scored = score_candidates(
            candidates,
            ctx,
            min_candidates=cfg.min_candidates,
            max_candidates=cfg.max_candidates,
        )
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        rows = await write_scored_candidates(incident_id, scored, elapsed_ms)

        _score_latency.observe(time.monotonic() - t0)
        _score_rows.inc(rows)
        _incidents_scored.labels(method="rules").inc()
        state.total_scored += 1

        log.info(
            "Scoring complete: incident_id=%d candidates=%d rows=%d "
            "scores=%s latency_ms=%d",
            incident_id, len(candidates), rows,
            _format_scores(scored), elapsed_ms,
        )

    except Exception as exc:
        log.error("Scoring failed for incident %d: %s", incident_id, exc)
        await reset_scoring(incident_id)
        _failed.inc()
        state.total_failed += 1
    finally:
        _active.dec()


def _format_scores(scored) -> str:
    """Compact per-candidate summary for logging."""
    parts = []
    for sc in scored:
        c = sc.candidate
        nums = {k: v.score for k, v in sc.scores.items()}
        parts.append(f"{c.action}[{c.target}]={nums}")
    return ", ".join(parts)