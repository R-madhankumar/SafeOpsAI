"""
SafeOpsAI — Scoring Agent: FastAPI Application
================================================
Endpoints:
  GET /health          liveness probe
  GET /ready           readiness (DB)
  GET /status          agent operational summary
  GET /scores          recent scored incidents (scoring_results view)
  GET /scores/{id}     scoring detail for a specific incident
  GET /metrics         Prometheus scrape endpoint
"""

from __future__ import annotations

import logging
import time

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

log = logging.getLogger("scoring_agent.api")

app = FastAPI(
    title="SafeOpsAI Scoring Agent",
    version="1.0.0",
    description="Cost / Reliability / Security rule-based scorers — Step 5",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "scoring-agent", "timestamp": time.time()}


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
        "service":          "scoring-agent",
        "started_at":       state.started_at,
        "last_poll_at":     state.last_poll_at,
        "last_poll_ok":     state.last_poll_ok,
        "total_polls":      state.total_polls,
        "total_errors":     state.total_errors,
        "total_scored":     state.total_scored,
        "total_failed":     state.total_failed,
        "db_connected":     state.db_connected,
        "poll_interval_s":  cfg.poll_interval,
        "criteria":         ["cost", "reliability", "security"],
        "convention":       "higher-is-better (cost=cheapest)",
    }


@app.get("/scores")
async def list_scores(limit: int = 20):
    from .db import get_scored_incidents
    try:
        rows = await get_scored_incidents(limit=min(limit, 100))
        return {"scores": rows, "count": len(rows)}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"DB error: {exc}")


@app.get("/scores/{incident_id}")
async def get_scores(incident_id: int):
    from .db import get_scored_for_incident
    try:
        row = await get_scored_for_incident(incident_id)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"DB error: {exc}")
    if row is None:
        raise HTTPException(status_code=404, detail=f"No scores found for incident {incident_id}")
    return row


@app.get("/metrics")
async def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)