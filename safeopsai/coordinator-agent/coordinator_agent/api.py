"""
SafeOpsAI — Coordinator Agent: FastAPI Application
=====================================================
Endpoints:
  GET  /health                liveness probe
  GET  /ready                 readiness (DB)
  GET  /status                agent operational summary
  GET  /weights               current runtime weights + method
  POST /weights               set runtime weights + method (no restart)
  GET  /decisions             recent coordinator decisions
  GET  /decisions/{id}        coordinator decision for a specific incident
  GET  /simulate              read-only ranking for weight sweeps / TOPSIS ablation
  GET  /metrics               Prometheus scrape endpoint
"""

from __future__ import annotations

import logging
import time

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel

log = logging.getLogger("coordinator_agent.api")

app = FastAPI(
    title="SafeOpsAI Coordinator Agent",
    version="1.0.0",
    description="Deterministic weighted-sum MCDM + TOPSIS ablation — Step 6",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


class WeightsBody(BaseModel):
    cost: float = 0.3
    reliability: float = 0.5
    security: float = 0.2
    method: str = "weighted_sum"
    note: str = ""


@app.get("/health")
async def health():
    return {"status": "ok", "service": "coordinator-agent", "timestamp": time.time()}


@app.get("/ready")
async def ready():
    from .db import get_pool
    try:
        pool = get_pool()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"DB not ready: {exc}")
    return {"status": "ready", "db": "connected"}


@app.get("/status")
async def status():
    from .agent import state
    from .config import cfg
    return {
        "service":         "coordinator-agent",
        "started_at":      state.started_at,
        "last_poll_at":    state.last_poll_at,
        "last_poll_ok":    state.last_poll_ok,
        "total_polls":     state.total_polls,
        "total_errors":    state.total_errors,
        "total_decided":   state.total_decided,
        "total_failed":    state.total_failed,
        "db_connected":    state.db_connected,
        "method":          state.method,
        "weights":         {k: round(v, 4) for k, v in state.weights.items()},
        "default_method":  cfg.default_method,
        "formula":         "final = w_cost*cost + w_rel*rel + w_sec*sec",
    }


@app.get("/weights")
async def get_weights():
    from .db import get_config
    try:
        conf = await get_config()
        return conf.to_dict()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"DB error: {exc}")


@app.post("/weights")
async def post_weights(body: WeightsBody):
    """Swap runtime weights / method. Affects the NEXT decision immediately."""
    from .db import set_config
    try:
        conf = await set_config(
            body.cost, body.reliability, body.security, body.method, body.note,
        )
        return {"updated": True, "config": conf.to_dict()}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"DB error: {exc}")


@app.get("/decisions")
async def list_decisions(limit: int = 20):
    from .db import get_decisions
    try:
        rows = await get_decisions(limit=min(limit, 100))
        return {"decisions": rows, "count": len(rows)}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"DB error: {exc}")


@app.get("/decisions/{incident_id}")
async def get_decision(incident_id: int):
    from .db import get_decision_for_incident
    try:
        row = await get_decision_for_incident(incident_id)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"DB error: {exc}")
    if row is None:
        raise HTTPException(status_code=404, detail=f"No coordinator decision for incident {incident_id}")
    return row


@app.get("/simulate")
async def simulate(
    incident_id: int = Query(...),
    method: str = Query("weighted_sum"),
    cost: float = Query(0.3),
    reliability: float = Query(0.5),
    security: float = Query(0.2),
):
    """
    READ-ONLY ranking for an already-scored incident, for any weights/method.
    Used by the weight-sensitivity sweep and the TOPSIS ablation — does NOT
    write anything to the database.
    """
    from .db import _candidate_rows, get_pool
    from .mcdm import decision_to_jsonb, rank_candidates, select_winner

    try:
        pool = get_pool()
        async with pool.acquire() as conn:
            rows = await _candidate_rows(conn, incident_id)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"DB error: {exc}")

    if not rows:
        raise HTTPException(status_code=404, detail=f"No scored candidates for incident {incident_id}")

    weights = {"cost": cost, "reliability": reliability, "security": security}
    ranked = rank_candidates(rows, weights, method)
    winner = select_winner(ranked)
    return {
        "incident_id": incident_id,
        "method":      method,
        "weights":     weights,
        "simulated":   True,
        **decision_to_jsonb(incident_id, method, weights, ranked, winner),
    }


@app.get("/metrics")
async def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)