"""
SafeOpsAI — Coordinator Agent: Polling Loop
==============================================
Polls the coordinator_queue, reads the current runtime weights, runs the
deterministic MCDM (weighted-sum or TOPSIS), and writes a fully auditable
decision to agent_decisions (weights + full ranking + winner).

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
    fetch_coordinator_queue,
    get_config,
    init_pool,
    mark_coordinated,
    reset_coordinated,
    write_decision,
)
from .mcdm import decision_to_jsonb, rank_candidates, select_winner

log = logging.getLogger("coordinator_agent.agent")

# ── Self-monitoring metrics ────────────────────────────────────────────────
_polls            = Counter("coordinator_polls_total",        "Total coordinator polling cycles")
_poll_errors      = Counter("coordinator_poll_errors_total",  "Failed coordinator polling cycles")
_decided          = Counter("coordinator_decisions_total",    "Decisions written",
                            ["method"])
_failed           = Counter("coordinator_incidents_failed_total", "Incidents that failed to coordinate")
_queue_length     = Gauge(  "coordinator_queue_length",       "Incidents waiting for a decision")
_active           = Gauge(  "coordinator_active",             "Incidents currently being coordinated")
_decision_latency = Histogram(
    "coordinator_duration_seconds",
    "Wall-clock time to decide one incident",
    buckets=[0.05, 0.1, 0.25, 0.5, 1, 2, 5],
)
_current_weights  = Gauge(
    "coordinator_current_weights",
    "Currently active runtime weights (1 = cost, 2 = reliability, 3 = security)",
    ["criterion"],
)


# ── Shared agent state (exposed to API /status) ────────────────────────────
class AgentState:
    def __init__(self) -> None:
        self.started_at:      str = datetime.now(timezone.utc).isoformat()
        self.last_poll_at:    str = ""
        self.last_poll_ok:    bool = False
        self.total_polls:     int = 0
        self.total_errors:    int = 0
        self.total_decided:   int = 0
        self.total_failed:    int = 0
        self.db_connected:    bool = False
        self.method:          str = cfg.default_method
        self.weights:         dict[str, float] = dict(cfg.default_weights)


state = AgentState()


def _update_weights_metric(weights: dict[str, float]) -> None:
    _current_weights.labels(criterion="cost").set(weights.get("cost", 0.0))
    _current_weights.labels(criterion="reliability").set(weights.get("reliability", 0.0))
    _current_weights.labels(criterion="security").set(weights.get("security", 0.0))


async def run_agent() -> None:
    """Initialise DB, then poll the coordinator queue forever."""
    log.info("Coordinator Agent starting — deterministic MCDM (weighted_sum / topsis)")

    try:
        await init_pool()
        state.db_connected = True
    except RuntimeError as exc:
        log.error("DB init failed: %s — decisions will not be stored", exc)

    try:
        while True:
            cycle_start = time.monotonic()
            await _poll_cycle()
            elapsed = time.monotonic() - cycle_start
            await asyncio.sleep(max(0.0, cfg.poll_interval - elapsed))
    except asyncio.CancelledError:
        log.info("Coordinator polling loop cancelled — shutting down")
    finally:
        await close_pool()


async def _poll_cycle() -> None:
    """One poll cycle: refresh weights → fetch queue → decide each incident."""
    _polls.inc()
    state.total_polls += 1

    try:
        conf = await get_config()
        state.weights = conf.weights()
        state.method = conf.method
        _update_weights_metric(state.weights)

        queue = await fetch_coordinator_queue(limit=cfg.max_concurrent)
        _queue_length.set(len(queue))

        if not queue:
            state.last_poll_at = datetime.now(timezone.utc).isoformat()
            state.last_poll_ok = True
            return

        log.info(
            "Coordinator queue: %d incident(s) to decide (method=%s weights=%s)",
            len(queue), conf.method, conf.weights(),
        )

        for item in queue:
            await _decide_one(item, conf.weights(), conf.method)

        state.last_poll_at = datetime.now(timezone.utc).isoformat()
        state.last_poll_ok = True

    except asyncio.CancelledError:
        raise
    except Exception as exc:
        log.error("Coordinator poll cycle error: %s", exc, exc_info=True)
        _poll_errors.inc()
        state.total_errors += 1
        state.last_poll_ok = False


async def _decide_one(item, weights: dict[str, float], method: str) -> None:
    """Decide a single incident and write the auditable decision."""
    incident_id = item["incident_id"]
    if not state.db_connected:
        log.warning("DB unavailable — skipping coordination for incident %d", incident_id)
        return

    claimed = await mark_coordinated(incident_id)
    if not claimed:
        log.debug("Incident %d already coordinated by another worker", incident_id)
        return

    _active.inc()
    t0 = time.monotonic()
    try:
        candidates = item["candidates"]
        if not candidates:
            log.warning("Incident %d has no scored candidates — nothing to rank", incident_id)
            await reset_coordinated(incident_id)
            return

        ranked = rank_candidates(candidates, weights, method)
        winner = select_winner(ranked)
        elapsed_ms = int((time.monotonic() - t0) * 1000)

        raw_json = decision_to_jsonb(incident_id, method, weights, ranked, winner)
        winner_score = winner.metric if winner else 0.0
        if method == "topsis":
            winner_score = winner.metric * 10.0 if winner else 0.0  # keep 0–10 scale

        reasoning = _build_reasoning(item, method, ranked, winner)
        await write_decision(incident_id, raw_json, winner_score, reasoning, elapsed_ms)

        _decision_latency.observe(time.monotonic() - t0)
        _decided.labels(method=method).inc()
        state.total_decided += 1

        log.info(
            "Decision complete: incident_id=%d method=%s winner=%s/%s "
            "metric=%.4f candidates=%d latency_ms=%d",
            incident_id, method,
            winner.row.action if winner else "none",
            winner.row.target if winner else "",
            winner.metric if winner else 0.0,
            len(candidates), elapsed_ms,
        )

    except Exception as exc:
        log.error("Coordination failed for incident %d: %s", incident_id, exc)
        await reset_coordinated(incident_id)
        _failed.inc()
        state.total_failed += 1
    finally:
        _active.dec()


def _build_reasoning(item, method: str, ranked, winner) -> str:
    """Human-readable audit summary stored in agent_decisions.reasoning."""
    parts = [
        f"method={method}",
        f"incident={item['incident_type']}({item['service']})",
    ]
    for rc in ranked:
        parts.append(
            f"{rc.rank}. {rc.row.action}[{rc.row.target}] "
            f"(cost={rc.row.cost:.1f}, rel={rc.row.reliability:.1f}, "
            f"sec={rc.row.security:.1f}) -> {rc.metric:.3f}"
        )
    if winner:
        parts.append(
            f"WINNER: {winner.row.action}[{winner.row.target}] "
            f"score={winner.metric:.3f}"
        )
    else:
        parts.append("WINNER: none")
    return "; ".join(parts)