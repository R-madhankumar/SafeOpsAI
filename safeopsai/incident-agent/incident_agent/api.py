"""
Incident Agent — FastAPI application
======================================
Exposes:
  GET /health   — liveness probe
  GET /ready    — readiness probe (checks DB + Prometheus)
  GET /status   — agent operational summary
  GET /metrics  — Prometheus scrape endpoint (agent self-metrics)
  GET /incidents — list currently open incidents from DB
"""

from __future__ import annotations

import logging
import time

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

log = logging.getLogger("incident_agent.api")

app = FastAPI(
    title="SafeOpsAI Incident Agent",
    version="1.0.0",
    description="Rule-based Prometheus monitoring agent — Step 3",
)

# CORS — allows the existing frontend (port 3000) to call /incidents
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


# ── /health ───────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    """Liveness probe — always returns 200 if the process is running."""
    return {"status": "ok", "service": "incident-agent", "timestamp": time.time()}


# ── /ready ────────────────────────────────────────────────────────────────

@app.get("/ready")
async def ready():
    """
    Readiness probe — checks DB pool and Prometheus reachability.
    Returns 200 if both are available, 503 otherwise.
    """
    from .agent import state
    from .db import get_pool

    issues = []

    # DB check
    try:
        pool = get_pool()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
    except Exception as exc:
        issues.append(f"db: {exc}")

    # Prometheus check
    if not state.prometheus_healthy:
        issues.append("prometheus: not healthy")

    if issues:
        raise HTTPException(status_code=503, detail={"issues": issues})

    return {"status": "ready", "db": "connected", "prometheus": "healthy"}


# ── /status ───────────────────────────────────────────────────────────────

@app.get("/status")
async def status():
    """Operational summary of the Incident Agent."""
    from .agent import state
    from .config import PROMETHEUS_URL, rules, topology

    return {
        "service":             "incident-agent",
        "started_at":          state.started_at,
        "last_poll_at":        state.last_poll_at,
        "last_poll_ok":        state.last_poll_ok,
        "prometheus_url":      PROMETHEUS_URL,
        "prometheus_healthy":  state.prometheus_healthy,
        "total_polls":         state.total_polls,
        "total_errors":        state.total_errors,
        "rule_count":          state.rule_count,
        "poll_interval_s":     rules.poll_interval,
        "active_incidents":    len(state.active_fingerprints),
        "active_fingerprints": state.active_fingerprints,
        "db_connected":        state.db_connected,
        "monitored_services":  list(topology.all_services().keys()),
    }


# ── /topology ─────────────────────────────────────────────────────────────

@app.get("/topology")
async def get_topology():
    """
    Return the service dependency map.
    Consumed by the Root Cause Agent (Step 4) to reason about cascade failures.
    """
    from .config import topology
    return topology.to_dict()


# ── /incidents ────────────────────────────────────────────────────────────

@app.get("/incidents")
async def list_incidents():
    """Return all currently open incidents from the database."""
    from .db import get_open_incidents
    try:
        rows = await get_open_incidents()
        # Convert datetime objects to ISO strings for JSON serialisation
        result = []
        for r in rows:
            row = dict(r)
            for k, v in row.items():
                if hasattr(v, "isoformat"):
                    row[k] = v.isoformat()
            result.append(row)
        return {"open_incidents": result, "count": len(result)}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"DB error: {exc}")


# ── /metrics ─────────────────────────────────────────────────────────────

@app.get("/metrics")
async def metrics():
    """Prometheus scrape endpoint for incident-agent self-metrics."""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
