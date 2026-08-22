"""
Root Cause Agent — FastAPI Application
=========================================
Endpoints:
  GET /health          liveness probe
  GET /ready           readiness (DB + LLM)
  GET /status          agent operational summary
  GET /diagnoses       recent RCA results
  GET /diagnose/{id}   RCA result for a specific incident
  GET /metrics         Prometheus scrape endpoint
"""

from __future__ import annotations

import logging
import time

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

log = logging.getLogger("rca.api")

app = FastAPI(
    title="SafeOpsAI Root Cause Agent",
    version="1.0.0",
    description="LLM-backed root cause analysis — Step 4",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    """Liveness probe — always 200 while the process is running."""
    return {"status": "ok", "service": "root-cause-agent", "timestamp": time.time()}


@app.get("/ready")
async def ready():
    """Readiness probe — checks DB connectivity."""
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
    """Operational summary of the Root Cause Agent."""
    from .agent import state
    from .config import cfg
    return {
        "service":           "root-cause-agent",
        "started_at":        state.started_at,
        "last_poll_at":      state.last_poll_at,
        "last_poll_ok":      state.last_poll_ok,
        "total_polls":       state.total_polls,
        "total_errors":      state.total_errors,
        "total_diagnosed":   state.total_diagnosed,
        "total_fallbacks":   state.total_fallbacks,
        "total_failed":      state.total_failed,
        "active_diagnoses":  state.active_diagnoses,
        "db_connected":      state.db_connected,
        "llm_healthy":       state.llm_healthy,
        "llm_model":         cfg.llm_model,
        "ollama_url":        cfg.llm_url,
        "fallback_enabled":  cfg.llm_fallback,
        "poll_interval_s":   cfg.poll_interval,
    }


@app.get("/diagnoses")
async def list_diagnoses(limit: int = 20):
    """Return recent RCA results (joined with incident context)."""
    from .db import get_diagnoses
    try:
        rows = await get_diagnoses(limit=min(limit, 100))
        return {"diagnoses": rows, "count": len(rows)}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"DB error: {exc}")


@app.get("/diagnose/{incident_id}")
async def get_diagnosis(incident_id: int):
    """Return the RCA result for a specific incident id."""
    from .db import get_diagnosis_for_incident
    try:
        row = await get_diagnosis_for_incident(incident_id)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"DB error: {exc}")
    if row is None:
        raise HTTPException(
            status_code=404,
            detail=f"No RCA diagnosis found for incident {incident_id}",
        )
    return row


@app.get("/metrics")
async def metrics():
    """Prometheus scrape endpoint for agent self-metrics."""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
