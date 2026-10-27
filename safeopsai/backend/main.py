"""
SafeOpsAI - Backend API Service
A realistic FastAPI service that reads/writes to PostgreSQL.
Instrumented with Prometheus metrics for incident detection.
"""

import asyncio
import time
import random
import logging
import os
from contextlib import asynccontextmanager

import asyncpg
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger("safeopsai.backend")

# ---------------------------------------------------------------------------
# Prometheus metrics
# ---------------------------------------------------------------------------
REQUEST_COUNT = Counter(
    "http_requests_total",
    "Total HTTP request count",
    ["method", "endpoint", "status_code"],
)
REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency in seconds",
    ["method", "endpoint"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
)
DB_QUERY_LATENCY = Histogram(
    "db_query_duration_seconds",
    "Database query latency in seconds",
    ["operation"],
    buckets=[0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0],
)
ACTIVE_CONNECTIONS = Gauge(
    "db_active_connections",
    "Number of active database connections",
)
ERROR_RATE = Counter(
    "application_errors_total",
    "Total application error count",
    ["error_type"],
)
ITEMS_CREATED = Counter(
    "items_created_total",
    "Total number of items created",
)

# ---------------------------------------------------------------------------
# Config from environment
# ---------------------------------------------------------------------------
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_NAME = os.getenv("DB_NAME", "safeopsdb")
DB_USER = os.getenv("DB_USER", "safeops")
DB_PASS = os.getenv("DB_PASS", "safeops123")

# Fault injection flags (set via /admin/fault endpoint)
_fault_state = {
    "slow_queries": False,       # adds artificial DB latency
    "high_error_rate": False,    # randomly fail 50% of requests
    "db_unavailable": False,     # reject all DB calls
}

# ---------------------------------------------------------------------------
# Database pool
# ---------------------------------------------------------------------------
db_pool: asyncpg.Pool | None = None


async def get_db_pool() -> asyncpg.Pool:
    global db_pool
    if db_pool is None:
        raise HTTPException(status_code=503, detail="Database not available")
    return db_pool


# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    global db_pool
    logger.info("Connecting to PostgreSQL at %s:%s/%s", DB_HOST, DB_PORT, DB_NAME)
    retries = 10
    for attempt in range(retries):
        try:
            db_pool = await asyncpg.create_pool(
                host=DB_HOST,
                port=DB_PORT,
                database=DB_NAME,
                user=DB_USER,
                password=DB_PASS,
                min_size=2,
                max_size=10,
            )
            logger.info("Database pool established")
            break
        except Exception as exc:
            logger.warning("DB connection attempt %d/%d failed: %s", attempt + 1, retries, exc)
            if attempt == retries - 1:
                logger.error("Could not connect to database after %d attempts", retries)
            else:
                await asyncio.sleep(3)

    yield  # app runs here

    if db_pool:
        await db_pool.close()
        logger.info("Database pool closed")


app = FastAPI(
    title="SafeOpsAI Demo Backend",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Prometheus middleware — records latency + request count on every request
# ---------------------------------------------------------------------------
@app.middleware("http")
async def prometheus_middleware(request: Request, call_next):
    start = time.time()

    # Fault: randomly fail requests
    if _fault_state["high_error_rate"] and random.random() < 0.5:
        ERROR_RATE.labels(error_type="injected_fault").inc()
        REQUEST_COUNT.labels(
            method=request.method,
            endpoint=request.url.path,
            status_code=500,
        ).inc()
        return JSONResponse(status_code=500, content={"detail": "Injected fault: high error rate"})

    response = await call_next(request)
    duration = time.time() - start

    REQUEST_COUNT.labels(
        method=request.method,
        endpoint=request.url.path,
        status_code=response.status_code,
    ).inc()
    REQUEST_LATENCY.labels(
        method=request.method,
        endpoint=request.url.path,
    ).observe(duration)

    return response


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class Item(BaseModel):
    name: str
    description: str = ""
    value: float = 0.0


class FaultConfig(BaseModel):
    slow_queries: bool = False
    high_error_rate: bool = False
    db_unavailable: bool = False


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get("/health")
async def health():
    """Liveness probe — also used by sandbox validation."""
    return {"status": "ok", "service": "backend", "timestamp": time.time()}


@app.get("/ready")
async def ready():
    """Readiness probe — checks DB connectivity."""
    if db_pool is None:
        raise HTTPException(status_code=503, detail="Database pool not initialised")
    try:
        async with db_pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        return {"status": "ready", "db": "connected"}
    except Exception as exc:
        ERROR_RATE.labels(error_type="db_health_check").inc()
        raise HTTPException(status_code=503, detail=f"DB not ready: {exc}")


@app.get("/metrics")
async def metrics():
    """Prometheus scrape endpoint."""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/items")
async def list_items():
    """Return all items from the database."""
    if _fault_state["db_unavailable"]:
        ERROR_RATE.labels(error_type="db_unavailable_fault").inc()
        raise HTTPException(status_code=503, detail="Database unavailable (injected fault)")

    pool = await get_db_pool()
    t0 = time.time()

    if _fault_state["slow_queries"]:
        await asyncio.sleep(random.uniform(2.0, 5.0))  # simulate slow query

    try:
        async with pool.acquire() as conn:
            ACTIVE_CONNECTIONS.set(pool.get_size())
            rows = await conn.fetch("SELECT id, name, description, value, created_at FROM items ORDER BY created_at DESC LIMIT 100")
            DB_QUERY_LATENCY.labels(operation="select").observe(time.time() - t0)
            return [dict(r) for r in rows]
    except Exception as exc:
        ERROR_RATE.labels(error_type="db_query_error").inc()
        logger.error("Failed to list items: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/items", status_code=201)
async def create_item(item: Item):
    """Insert a new item into the database."""
    if _fault_state["db_unavailable"]:
        ERROR_RATE.labels(error_type="db_unavailable_fault").inc()
        raise HTTPException(status_code=503, detail="Database unavailable (injected fault)")

    pool = await get_db_pool()
    t0 = time.time()
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO items (name, description, value) VALUES ($1, $2, $3) RETURNING id, name, description, value, created_at",
                item.name, item.description, item.value,
            )
            DB_QUERY_LATENCY.labels(operation="insert").observe(time.time() - t0)
            ITEMS_CREATED.inc()
            return dict(row)
    except Exception as exc:
        ERROR_RATE.labels(error_type="db_query_error").inc()
        logger.error("Failed to create item: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/items/{item_id}")
async def get_item(item_id: int):
    """Fetch a single item by ID."""
    pool = await get_db_pool()
    t0 = time.time()
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, name, description, value, created_at FROM items WHERE id = $1",
                item_id,
            )
            DB_QUERY_LATENCY.labels(operation="select_by_id").observe(time.time() - t0)
            if row is None:
                raise HTTPException(status_code=404, detail="Item not found")
            return dict(row)
    except HTTPException:
        raise
    except Exception as exc:
        ERROR_RATE.labels(error_type="db_query_error").inc()
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/stats")
async def stats():
    """Aggregate stats — gives the frontend something interesting to display."""
    pool = await get_db_pool()
    try:
        async with pool.acquire() as conn:
            total = await conn.fetchval("SELECT COUNT(*) FROM items")
            avg_val = await conn.fetchval("SELECT AVG(value) FROM items")
            return {
                "total_items": total,
                "average_value": round(float(avg_val or 0), 2),
                "db_pool_size": pool.get_size(),
                "fault_state": _fault_state,
            }
    except Exception as exc:
        ERROR_RATE.labels(error_type="stats_error").inc()
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# Admin / fault injection endpoints (used by fault-injection scripts)
# ---------------------------------------------------------------------------
@app.post("/admin/fault")
async def set_fault(config: FaultConfig):
    """
    Activate or deactivate fault modes without restarting the container.
    Used by the fault injection scripts to simulate incidents.
    """
    _fault_state["slow_queries"] = config.slow_queries
    _fault_state["high_error_rate"] = config.high_error_rate
    _fault_state["db_unavailable"] = config.db_unavailable
    logger.warning("Fault state updated: %s", _fault_state)
    return {"message": "Fault state updated", "current_state": _fault_state}


@app.get("/admin/fault")
async def get_fault():
    """Return current fault injection state."""
    return {"fault_state": _fault_state}


@app.post("/admin/fault/reset")
async def reset_fault():
    """Clear all active faults — used by rollback/remediation."""
    _fault_state["slow_queries"] = False
    _fault_state["high_error_rate"] = False
    _fault_state["db_unavailable"] = False
    logger.info("All faults cleared")
    return {"message": "All faults cleared", "current_state": _fault_state}


# ---------------------------------------------------------------------------
# Control Center Query Endpoints (Phase 7)
# ---------------------------------------------------------------------------
import json

def _as_json_dict(val):
    if val is None:
        return {}
    if isinstance(val, str):
        try:
            return json.loads(val)
        except Exception:
            return {}
    if isinstance(val, dict):
        return val
    return {}


@app.get("/incidents")
async def get_all_incidents(
    limit: int = 200,
    offset: int = 0,
    service: str | None = None,
    severity: str | None = None,
    status: str | None = None,
    incident_type: str | None = None,
    search: str | None = None,
    is_demo: bool | None = None,
):
    """Return list of incidents with stage and status, supporting search, filters, and pagination."""
    pool = await get_db_pool()
    try:
        async with pool.acquire() as conn:
            query = """
                SELECT i.*,
                       (SELECT json_build_object('agent_name', ad.agent_name, 'score', ad.score, 'reasoning', ad.reasoning, 'raw_output', ad.raw_output)
                        FROM agent_decisions ad WHERE ad.incident_id = i.id AND ad.agent_name = 'root_cause' ORDER BY ad.id DESC LIMIT 1) AS root_cause_data,
                       (SELECT json_build_object('agent_name', ad.agent_name, 'score', ad.score, 'reasoning', ad.reasoning, 'raw_output', ad.raw_output)
                        FROM agent_decisions ad WHERE ad.incident_id = i.id AND ad.agent_name = 'coordinator' ORDER BY ad.id DESC LIMIT 1) AS mcdm_data,
                       (SELECT json_build_object('validation_score', ra.validation_score, 'sandbox_passed', ra.sandbox_passed, 'checks', ra.checks, 'selection_status', ra.selection_status, 'failure_reason', ra.failure_reason)
                        FROM remediation_actions ra WHERE ra.incident_id = i.id AND ra.execution_authorized = TRUE ORDER BY ra.id DESC LIMIT 1) AS sandbox_data,
                       (SELECT json_build_object('action_type', ra.action_type, 'state', ra.state, 'recovery_score', ra.recovery_score, 'rollback_performed', ra.rollback_performed, 'escalated', ra.escalated)
                        FROM remediation_actions ra WHERE ra.incident_id = i.id AND ra.state IS NOT NULL ORDER BY ra.id DESC LIMIT 1) AS remediation_data
                FROM incidents i
                WHERE 1=1
            """
            params = []
            param_idx = 1

            if service:
                query += f" AND i.service = ${param_idx}"
                params.append(service)
                param_idx += 1

            if severity:
                query += f" AND i.severity = ${param_idx}"
                params.append(severity.lower())
                param_idx += 1

            if status:
                query += f" AND i.status = ${param_idx}"
                params.append(status.lower())
                param_idx += 1

            if incident_type:
                query += f" AND (i.incident_type ILIKE ${param_idx} OR i.fault_type ILIKE ${param_idx})"
                params.append(f"%{incident_type}%")
                param_idx += 1

            if is_demo is not None:
                query += f" AND i.is_demo = ${param_idx}"
                params.append(is_demo)
                param_idx += 1

            if search:
                query += f" AND (i.description ILIKE ${param_idx} OR i.service ILIKE ${param_idx} OR i.incident_type ILIKE ${param_idx} OR CAST(i.id AS TEXT) ILIKE ${param_idx})"
                params.append(f"%{search}%")
                param_idx += 1

            query += f" ORDER BY i.detected_at DESC LIMIT ${param_idx} OFFSET ${param_idx + 1}"
            params.extend([limit, offset])

            rows = await conn.fetch(query, *params)
            result = []
            for r in rows:
                row = dict(r)
                for k in ("detected_at", "resolved_at", "diagnosing_at", "scoring_at", "coordinated_at", "sandbox_at", "remediating_at"):
                    if hasattr(row.get(k), "isoformat"):
                        row[k] = row[k].isoformat()
                for k in ("root_cause_data", "mcdm_data", "sandbox_data", "remediation_data"):
                    row[k] = _as_json_dict(row.get(k))

                # Determine active stage
                if row.get("status") == "resolved":
                    stage = "RECOVERY"
                elif row.get("remediating_at"):
                    stage = "PRODUCTION"
                elif row.get("sandbox_at"):
                    stage = "SANDBOX"
                elif row.get("coordinated_at"):
                    stage = "MCDM"
                elif row.get("scoring_at"):
                    stage = "AGENT NEGOTIATION"
                elif row.get("diagnosing_at"):
                    stage = "ROOT CAUSE"
                else:
                    stage = "INCIDENT"
                row["current_stage"] = stage
                result.append(row)
            return result
    except Exception as exc:
        logger.error("Failed to fetch incidents: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/incidents/{incident_id}")
async def get_incident_detail(incident_id: int):
    """Fetch single incident detail."""
    pool = await get_db_pool()
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM incidents WHERE id = $1", incident_id)
            if not row:
                raise HTTPException(status_code=404, detail="Incident not found")
            res = dict(row)
            for k in ("detected_at", "resolved_at", "diagnosing_at", "scoring_at", "coordinated_at", "sandbox_at", "remediating_at"):
                if hasattr(res.get(k), "isoformat"):
                    res[k] = res[k].isoformat()
            return res
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/incidents/{incident_id}/pipeline")
async def get_incident_pipeline_breakdown(incident_id: int):
    """
    Detailed 7-stage pipeline breakdown for an incident:
    RCA, Scorer decisions, MCDM ranking, Sandbox validation, Remediation state, Timeline.
    """
    pool = await get_db_pool()
    try:
        async with pool.acquire() as conn:
            inc = await conn.fetchrow("SELECT * FROM incidents WHERE id = $1", incident_id)
            if not inc:
                raise HTTPException(status_code=404, detail="Incident not found")

            # Agent decisions
            decisions_rows = await conn.fetch(
                "SELECT id, agent_name, score, reasoning, raw_output, execution_time_ms, created_at FROM agent_decisions WHERE incident_id = $1 ORDER BY id ASC",
                incident_id,
            )
            decisions = []
            for d in decisions_rows:
                dr = dict(d)
                dr["raw_output"] = _as_json_dict(dr.get("raw_output"))
                if hasattr(dr.get("created_at"), "isoformat"):
                    dr["created_at"] = dr["created_at"].isoformat()
                decisions.append(dr)

            # Remediation actions
            actions_rows = await conn.fetch(
                "SELECT * FROM remediation_actions WHERE incident_id = $1 ORDER BY id ASC",
                incident_id,
            )
            actions = []
            for a in actions_rows:
                ar = dict(a)
                for k in ("checks", "baseline_metrics", "after_metrics", "recovery_metrics"):
                    ar[k] = _as_json_dict(ar.get(k))
                for k in ("sandbox_started_at", "sandbox_ended_at", "executed_at", "rollback_started_at", "rollback_ended_at"):
                    if hasattr(ar.get(k), "isoformat"):
                        ar[k] = ar[k].isoformat()
                actions.append(ar)

            inc_dict = dict(inc)
            for k in ("detected_at", "resolved_at", "diagnosing_at", "scoring_at", "coordinated_at", "sandbox_at", "remediating_at"):
                if hasattr(inc_dict.get(k), "isoformat"):
                    inc_dict[k] = inc_dict[k].isoformat()

            if inc_dict.get("resolved_at"):
                inc_dict["current_stage"] = "RECOVERY"
            elif inc_dict.get("remediating_at"):
                inc_dict["current_stage"] = "PRODUCTION"
            elif inc_dict.get("sandbox_at"):
                inc_dict["current_stage"] = "SANDBOX"
            elif inc_dict.get("coordinated_at"):
                inc_dict["current_stage"] = "MCDM"
            elif inc_dict.get("scoring_at"):
                inc_dict["current_stage"] = "AGENT NEGOTIATION"
            elif inc_dict.get("diagnosing_at"):
                inc_dict["current_stage"] = "ROOT CAUSE"
            else:
                inc_dict["current_stage"] = "INCIDENT"

            # Separate decisions by agent
            rca = next((d for d in decisions if d["agent_name"] == "root_cause"), None)
            scorers = {d["agent_name"]: d for d in decisions if d["agent_name"] in ("cost", "reliability", "security")}
            mcdm = next((d for d in decisions if d["agent_name"] == "coordinator"), None)

            # Reconstruct candidate rows for counterfactual simulation
            # (Matches coordinator_agent logic)
            by_cand = {}
            cand_order = []
            for d in decisions:
                if d["agent_name"] in ("cost", "reliability", "security"):
                    raw = d.get("raw_output") or {}
                    cand = raw.get("candidate") or {}
                    action_name = str(cand.get("action", "investigate"))
                    target_name = str(cand.get("target", "unknown"))
                    key = (action_name, target_name)
                    if key not in by_cand:
                        by_cand[key] = {
                            "action": action_name,
                            "target": target_name,
                            "priority": int(cand.get("priority", 1) or 1),
                            "cost": 0.0,
                            "reliability": 0.0,
                            "security": 0.0,
                        }
                        cand_order.append(key)
                    try:
                        by_cand[key][d["agent_name"]] = float(d.get("score") or 0.0)
                    except (ValueError, TypeError):
                        pass

            # If coordinator decision already contains full ranking/scores in raw_output, also extract from there
            if not cand_order and mcdm and isinstance(mcdm.get("raw_output"), dict):
                ranking_data = mcdm["raw_output"].get("ranking") or []
                for item in ranking_data:
                    act = item.get("action", "unknown")
                    tgt = item.get("target", "unknown")
                    sc = item.get("scores") or {}
                    key = (act, tgt)
                    if key not in by_cand:
                        by_cand[key] = {
                            "action": act,
                            "target": tgt,
                            "priority": int(item.get("priority", 1) or 1),
                            "cost": float(sc.get("cost", 0.0)),
                            "reliability": float(sc.get("reliability", 0.0)),
                            "security": float(sc.get("security", 0.0)),
                        }
                        cand_order.append(key)

            candidates_list = [by_cand[k] for k in cand_order]

            # Construct timeline
            timeline = []
            if inc_dict.get("detected_at"):
                timeline.append({"time": inc_dict["detected_at"], "event": "Incident Detected", "stage": "INCIDENT", "description": f"Incident {inc_dict.get('incident_type') or inc_dict.get('fault_type')} opened on service {inc_dict.get('service')}"})
            if inc_dict.get("diagnosing_at"):
                timeline.append({"time": inc_dict["diagnosing_at"], "event": "Root Cause Diagnosed via Ollama", "stage": "ROOT CAUSE", "description": f"Diagnosis: {rca.get('reasoning') if rca else 'Automated RCA analysis'}"})
            if inc_dict.get("scoring_at"):
                timeline.append({"time": inc_dict["scoring_at"], "event": "Cost / Reliability / Security Evaluated", "stage": "AGENT NEGOTIATION", "description": f"Evaluated {len(candidates_list)} candidate remediation actions"})
            if inc_dict.get("coordinated_at"):
                mcdm_winner = (mcdm.get("raw_output") or {}).get("winner") if mcdm else None
                winner_act = mcdm_winner.get("action") if isinstance(mcdm_winner, dict) else "best candidate"
                timeline.append({"time": inc_dict["coordinated_at"], "event": "MCDM Candidate Ranked & Winner Selected", "stage": "MCDM", "description": f"Selected action: {winner_act}"})
            if inc_dict.get("sandbox_at"):
                sb_passed = any(a.get("sandbox_passed") for a in actions)
                timeline.append({"time": inc_dict["sandbox_at"], "event": "Sandbox Validation Completed", "stage": "SANDBOX", "description": f"Multi-signal checks {'passed' if sb_passed else 'completed'}"})
            if inc_dict.get("remediating_at"):
                rem_act = next((a.get("action_type") for a in actions if a.get("executed_at")), "action")
                timeline.append({"time": inc_dict["remediating_at"], "event": "Production Remediation Executed", "stage": "PRODUCTION", "description": f"Executed: {rem_act}"})
            if inc_dict.get("resolved_at"):
                timeline.append({"time": inc_dict["resolved_at"], "event": "Service Recovery Confirmed & Resolved", "stage": "RECOVERY", "description": f"Recovery verified, MTTR: {inc_dict.get('mttr_seconds', 0)}s"})

            # Assess completeness of historical data
            required_fields = ["detected_at", "diagnosing_at", "scoring_at", "coordinated_at", "sandbox_at", "remediating_at", "resolved_at"]
            completed_stages_count = sum(1 for f in required_fields if inc_dict.get(f) is not None)
            is_complete = completed_stages_count >= 5  # At least 5 stages recorded

            return {
                "incident": inc_dict,
                "root_cause": rca,
                "agent_evaluations": scorers,
                "mcdm_decision": mcdm,
                "candidates": candidates_list,
                "remediation_actions": actions,
                "timeline": timeline,
                "history_completeness": {
                    "is_complete": is_complete,
                    "recorded_stages": completed_stages_count,
                    "total_stages": 7,
                    "missing_stages": [s.replace("_at", "").upper() for s in required_fields if inc_dict.get(s) is None]
                }
            }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to fetch incident pipeline: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# Decision Lab & Simulation (MCDM / TOPSIS Counterfactual Preview)
# ---------------------------------------------------------------------------
class CounterfactualRequest(BaseModel):
    incident_id: int
    cost_weight: float = 0.30
    reliability_weight: float = 0.50
    security_weight: float = 0.20
    method: str = "weighted_sum"  # 'weighted_sum' or 'topsis'


@app.post("/decision-lab/simulate")
async def simulate_counterfactual(req: CounterfactualRequest):
    """
    Pure read-only counterfactual simulation for the Decision Lab.
    Allows adjusting weights or MCDM method without mutating production state
    or modifying original audit records.
    """
    # Validate weights (must be non-negative)
    if req.cost_weight < 0 or req.reliability_weight < 0 or req.security_weight < 0:
        raise HTTPException(status_code=400, detail="Weights must be non-negative numbers.")

    raw_sum = req.cost_weight + req.reliability_weight + req.security_weight
    if raw_sum <= 0:
        # Fall back to equal weights
        norm_cost = norm_rel = norm_sec = 1.0 / 3.0
        normalized_weights = {"cost": norm_cost, "reliability": norm_rel, "security": norm_sec}
        norm_explanation = "Raw weights sum to 0; defaulted to equal distribution (1/3 each)."
    else:
        norm_cost = req.cost_weight / raw_sum
        norm_rel = req.reliability_weight / raw_sum
        norm_sec = req.security_weight / raw_sum
        normalized_weights = {
            "cost": round(norm_cost, 4),
            "reliability": round(norm_rel, 4),
            "security": round(norm_sec, 4),
        }
        if abs(raw_sum - 1.0) > 1e-4:
            norm_explanation = f"Weights scaled by 1/{round(raw_sum, 3)} to normalize sum to 1.0."
        else:
            norm_explanation = "Weights already sum to 1.0."

    # Fetch incident and candidates
    pool = await get_db_pool()
    try:
        async with pool.acquire() as conn:
            # 1. Fetch original coordinator decision if exists
            orig_mcdm_row = await conn.fetchrow(
                "SELECT raw_output FROM agent_decisions WHERE incident_id = $1 AND agent_name = 'coordinator' ORDER BY id DESC LIMIT 1",
                req.incident_id,
            )
            orig_output = _as_json_dict(orig_mcdm_row["raw_output"]) if orig_mcdm_row else {}
            orig_winner = (orig_output.get("winner") or {}).get("action")
            orig_weights = orig_output.get("weights_used") or {"cost": 0.3, "reliability": 0.5, "security": 0.2}
            orig_ranking = orig_output.get("ranking") or []

            # 2. Reconstruct candidates from agent_decisions
            dec_rows = await conn.fetch(
                """
                SELECT agent_name, score, raw_output
                FROM agent_decisions
                WHERE incident_id = $1 AND agent_name IN ('cost', 'reliability', 'security')
                """,
                req.incident_id,
            )

            by_candidate = {}
            cand_order = []
            for r in dec_rows:
                raw = _as_json_dict(r["raw_output"])
                cand = raw.get("candidate") or {}
                act = str(cand.get("action", "investigate"))
                tgt = str(cand.get("target", "unknown"))
                key = (act, tgt)
                if key not in by_candidate:
                    by_candidate[key] = {
                        "action": act,
                        "target": tgt,
                        "priority": int(cand.get("priority", 1) or 1),
                        "cost": 0.0,
                        "reliability": 0.0,
                        "security": 0.0,
                    }
                    cand_order.append(key)
                try:
                    by_candidate[key][r["agent_name"]] = float(r["score"])
                except (ValueError, TypeError):
                    pass

            # Fallback to candidates from original coordinator decision if scoring rows aren't present
            if not cand_order and orig_ranking:
                for item in orig_ranking:
                    act = item.get("action", "unknown")
                    tgt = item.get("target", "unknown")
                    sc = item.get("scores") or {}
                    key = (act, tgt)
                    if key not in by_candidate:
                        by_candidate[key] = {
                            "action": act,
                            "target": tgt,
                            "priority": int(item.get("priority", 1) or 1),
                            "cost": float(sc.get("cost", 0.0)),
                            "reliability": float(sc.get("reliability", 0.0)),
                            "security": float(sc.get("security", 0.0)),
                        }
                        cand_order.append(key)

            if not cand_order:
                raise HTTPException(status_code=404, detail="No scored candidates found for this incident.")

            candidate_list = [by_candidate[k] for k in cand_order]

            # Re-rank using the coordinator's exact MCDM math
            # 1. Weighted sum
            weighted_results = []
            for c in candidate_list:
                score = (
                    normalized_weights["cost"] * c["cost"]
                    + normalized_weights["reliability"] * c["reliability"]
                    + normalized_weights["security"] * c["security"]
                )
                weighted_results.append({
                    "action": c["action"],
                    "target": c["target"],
                    "priority": c["priority"],
                    "scores": {
                        "cost": round(c["cost"], 2),
                        "reliability": round(c["reliability"], 2),
                        "security": round(c["security"], 2),
                    },
                    "components": {
                        "cost": round(normalized_weights["cost"] * c["cost"], 3),
                        "reliability": round(normalized_weights["reliability"] * c["reliability"], 3),
                        "security": round(normalized_weights["security"] * c["security"], 3),
                    },
                    "metric": round(score, 4),
                })
            # Sort: highest metric, then lower priority num, then action
            weighted_results.sort(key=lambda x: (-x["metric"], x["priority"], x["action"]))
            for idx, item in enumerate(weighted_results, 1):
                item["rank"] = idx

            # 2. TOPSIS (Technique for Order of Preference by Similarity to Ideal Solution)
            import math
            n = len(candidate_list)
            criteria_keys = ["cost", "reliability", "security"]
            norms = {}
            for crit in criteria_keys:
                denom = math.sqrt(sum(c[crit] ** 2 for c in candidate_list))
                norms[crit] = denom if denom > 0 else 1.0

            # Normalized weighted matrix
            v_matrix = []
            for c in candidate_list:
                row_v = {
                    crit: (c[crit] / norms[crit]) * normalized_weights[crit]
                    for crit in criteria_keys
                }
                v_matrix.append((c, row_v))

            # Ideal best (A+) and worst (A-)
            a_plus = {crit: max(row_v[crit] for _, row_v in v_matrix) for crit in criteria_keys}
            a_minus = {crit: min(row_v[crit] for _, row_v in v_matrix) for crit in criteria_keys}

            topsis_results = []
            for c, row_v in v_matrix:
                d_plus = math.sqrt(sum((row_v[crit] - a_plus[crit]) ** 2 for crit in criteria_keys))
                d_minus = math.sqrt(sum((row_v[crit] - a_minus[crit]) ** 2 for crit in criteria_keys))
                denom = d_plus + d_minus
                closeness = (d_minus / denom) if denom > 1e-12 else 0.5
                topsis_results.append({
                    "action": c["action"],
                    "target": c["target"],
                    "priority": c["priority"],
                    "scores": {
                        "cost": round(c["cost"], 2),
                        "reliability": round(c["reliability"], 2),
                        "security": round(c["security"], 2),
                    },
                    "d_plus": round(d_plus, 4),
                    "d_minus": round(d_minus, 4),
                    "metric": round(closeness, 4),
                })
            topsis_results.sort(key=lambda x: (-x["metric"], x["priority"], x["action"]))
            for idx, item in enumerate(topsis_results, 1):
                item["rank"] = idx

            # Active preview ranking based on method requested
            active_ranking = topsis_results if req.method == "topsis" else weighted_results
            preview_winner = active_ranking[0]["action"] if active_ranking else None

            # Explain decision change
            action_changed = (orig_winner is not None) and (orig_winner != preview_winner)
            
            # Identify dominant criterion in preview ranking
            # Compare contributions of winner vs runner-up if available
            explanation = ""
            if len(active_ranking) >= 2:
                top = active_ranking[0]
                second = active_ranking[1]
                if req.method == "weighted_sum":
                    cost_diff = top["components"]["cost"] - second["components"]["cost"]
                    rel_diff = top["components"]["reliability"] - second["components"]["reliability"]
                    sec_diff = top["components"]["security"] - second["components"]["security"]
                    diffs = [("reliability", rel_diff), ("cost", cost_diff), ("security", sec_diff)]
                    diffs.sort(key=lambda x: -x[1])
                    biggest_driver = diffs[0]
                    explanation = (
                        f"'{top['action']}' ranked #1 ({top['metric']:.2f}) over '{second['action']}' ({second['metric']:.2f}). "
                        f"Primary differentiator was {biggest_driver[0]} contribution (+{biggest_driver[1]:.3f})."
                    )
                else:
                    explanation = (
                        f"TOPSIS ranked '{top['action']}' #1 with relative closeness coefficient C = {top['metric']:.4f} "
                        f"(vs {second['action']} C = {second['metric']:.4f})."
                    )
            elif len(active_ranking) == 1:
                explanation = f"Only one remediation candidate '{active_ranking[0]['action']}' was available."
            else:
                explanation = "Insufficient candidate evidence to compute comparative explanation."

            return {
                "incident_id": req.incident_id,
                "is_preview": True,
                "method_used": req.method,
                "raw_weights": {
                    "cost": req.cost_weight,
                    "reliability": req.reliability_weight,
                    "security": req.security_weight,
                },
                "normalized_weights": normalized_weights,
                "normalization_explanation": norm_explanation,
                "original_weights": orig_weights,
                "original_winner": orig_winner,
                "original_ranking": orig_ranking,
                "preview_winner": preview_winner,
                "action_changed": action_changed,
                "ranking": active_ranking,
                "weighted_sum_ranking": weighted_results,
                "topsis_ranking": topsis_results,
                "explanation": explanation,
                "formula": (
                    f"Score = {normalized_weights['cost']}*Cost + {normalized_weights['reliability']}*Rel + {normalized_weights['security']}*Sec"
                    if req.method == "weighted_sum"
                    else "TOPSIS closeness coefficient C = D- / (D+ + D-) from ideal solutions"
                ),
            }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Decision Lab simulation failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/dashboard/summary")
async def get_dashboard_summary():
    """Top-level Operations Control Center KPI Summary."""
    pool = await get_db_pool()
    try:
        async with pool.acquire() as conn:
            active_cnt = await conn.fetchval(
                "SELECT COUNT(*) FROM incidents WHERE status IN ('open', 'remediating', 'diagnosing', 'scoring', 'sandbox')"
            )
            total_incidents = await conn.fetchval("SELECT COUNT(*) FROM incidents")
            resolved_cnt = await conn.fetchval("SELECT COUNT(*) FROM incidents WHERE status = 'resolved'")
            failed_incidents = await conn.fetchval("SELECT COUNT(*) FROM incidents WHERE status = 'failed'")
            total_remediations = await conn.fetchval("SELECT COUNT(*) FROM remediation_actions WHERE state IS NOT NULL")
            success_remediations = await conn.fetchval("SELECT COUNT(*) FROM remediation_actions WHERE state = 'SUCCESS'")
            failed_remediations = await conn.fetchval(
                "SELECT COUNT(*) FROM remediation_actions WHERE state = 'FAILED' OR rollback_performed = TRUE"
            )
            rollback_cnt = await conn.fetchval("SELECT COUNT(*) FROM remediation_actions WHERE rollback_performed = TRUE")
            avg_mttr = await conn.fetchval("SELECT AVG(mttr_seconds) FROM incidents WHERE resolved_at IS NOT NULL AND mttr_seconds IS NOT NULL")

            # Check backend fault status for topology health
            active_faults = [k for k, v in _fault_state.items() if v]
            sys_status = "OPERATIONAL" if not active_faults and active_cnt == 0 else "DEGRADED"

            return {
                "system_status": sys_status,
                "active_incidents": active_cnt or 0,
                "total_incidents": total_incidents or 0,
                "resolved_incidents": resolved_cnt or 0,
                "failed_incidents": failed_incidents or 0,
                "total_remediations": total_remediations or 0,
                "successful_remediations": success_remediations or 0,
                "failed_remediations": failed_remediations or 0,
                "remediation_success_rate": round(float(success_remediations or 0) / float(total_remediations or 1) * 100, 1),
                "rollbacks": rollback_cnt or 0,
                "avg_mttr_seconds": round(float(avg_mttr or 0), 1),
                "active_faults": active_faults,
            }
    except Exception as exc:
        logger.error("Failed to fetch dashboard summary: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/dashboard/analytics")
async def get_dashboard_analytics():
    """Aggregated analytics for incident distributions, MTTR, trends, and service health."""
    pool = await get_db_pool()
    try:
        async with pool.acquire() as conn:
            total_incidents = await conn.fetchval("SELECT COUNT(*) FROM incidents")
            active_cnt = await conn.fetchval(
                "SELECT COUNT(*) FROM incidents WHERE status IN ('open', 'remediating', 'diagnosing', 'scoring', 'sandbox')"
            )
            resolved_cnt = await conn.fetchval("SELECT COUNT(*) FROM incidents WHERE status = 'resolved'")
            failed_incidents = await conn.fetchval("SELECT COUNT(*) FROM incidents WHERE status = 'failed'")
            total_remediations = await conn.fetchval("SELECT COUNT(*) FROM remediation_actions WHERE state IS NOT NULL")
            successful_remediations = await conn.fetchval("SELECT COUNT(*) FROM remediation_actions WHERE state = 'SUCCESS'")
            failed_remediations = await conn.fetchval(
                "SELECT COUNT(*) FROM remediation_actions WHERE state = 'FAILED' OR rollback_performed = TRUE"
            )
            rollback_cnt = await conn.fetchval("SELECT COUNT(*) FROM remediation_actions WHERE rollback_performed = TRUE")
            avg_mttr = await conn.fetchval("SELECT AVG(mttr_seconds) FROM incidents WHERE resolved_at IS NOT NULL AND mttr_seconds IS NOT NULL")

            tot_val = total_incidents or 1

            # Severity distribution
            sev_rows = await conn.fetch("SELECT severity, COUNT(*) as cnt FROM incidents GROUP BY severity ORDER BY cnt DESC")
            severity_dist = [
                {"severity": r["severity"], "count": r["cnt"], "percentage": round(r["cnt"] / tot_val * 100, 1)}
                for r in sev_rows
            ]

            # Incident types by frequency
            type_rows = await conn.fetch(
                "SELECT COALESCE(incident_type, fault_type) as itype, COUNT(*) as cnt FROM incidents GROUP BY itype ORDER BY cnt DESC"
            )
            incident_types = [
                {"type": r["itype"], "count": r["cnt"], "percentage": round(r["cnt"] / tot_val * 100, 1)}
                for r in type_rows
            ]

            # Service health & incident distribution
            svc_rows = await conn.fetch(
                """
                SELECT service,
                       COUNT(*) as total,
                       COUNT(*) FILTER (WHERE status IN ('open', 'remediating', 'diagnosing', 'scoring', 'sandbox')) as active,
                       COUNT(*) FILTER (WHERE status = 'resolved') as resolved,
                       COUNT(*) FILTER (WHERE status = 'failed') as failed,
                       ROUND(COALESCE(AVG(mttr_seconds) FILTER (WHERE resolved_at IS NOT NULL), 0), 1) as avg_mttr
                FROM incidents
                GROUP BY service
                ORDER BY total DESC
                """
            )
            service_dist = [
                {
                    "service": r["service"],
                    "total": r["total"],
                    "active": r["active"],
                    "resolved": r["resolved"],
                    "failed": r["failed"],
                    "avg_mttr": float(r["avg_mttr"]),
                    "health": "DEGRADED" if r["active"] > 0 else "HEALTHY",
                }
                for r in svc_rows
            ]

            # Incidents over time (past 30 days daily counts)
            time_rows = await conn.fetch(
                """
                SELECT DATE(detected_at) as day,
                       COUNT(*) as total,
                       COUNT(*) FILTER (WHERE status = 'resolved') as resolved,
                       COUNT(*) FILTER (WHERE status IN ('open', 'remediating', 'diagnosing', 'scoring', 'sandbox')) as active,
                       COUNT(*) FILTER (WHERE status = 'failed') as failed
                FROM incidents
                WHERE detected_at >= NOW() - INTERVAL '30 days'
                GROUP BY day
                ORDER BY day ASC
                """
            )
            incidents_over_time = [
                {
                    "date": str(r["day"]),
                    "total": r["total"],
                    "resolved": r["resolved"],
                    "active": r["active"],
                    "failed": r["failed"],
                }
                for r in time_rows
            ]

            # Demo vs real isolation breakdown
            demo_cnt = await conn.fetchval("SELECT COUNT(*) FROM incidents WHERE is_demo = TRUE")
            real_cnt = await conn.fetchval("SELECT COUNT(*) FROM incidents WHERE is_demo = FALSE")

            return {
                "total_incidents": total_incidents or 0,
                "active_incidents": active_cnt or 0,
                "resolved_incidents": resolved_cnt or 0,
                "failed_incidents": failed_incidents or 0,
                "total_remediations": total_remediations or 0,
                "successful_remediations": successful_remediations or 0,
                "failed_remediations": failed_remediations or 0,
                "remediation_success_rate": round(float(successful_remediations or 0) / float(total_remediations or 1) * 100, 1),
                "rollbacks": rollback_cnt or 0,
                "avg_mttr_seconds": round(float(avg_mttr or 0), 1),
                "severity_distribution": severity_dist,
                "incident_types": incident_types,
                "service_distribution": service_dist,
                "incidents_over_time": incidents_over_time,
                "demo_isolation": {
                    "demo_records": demo_cnt or 0,
                    "real_records": real_cnt or 0,
                },
            }
    except Exception as exc:
        logger.error("Failed to fetch dashboard analytics: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/dashboard/topology")
async def get_dashboard_topology():
    """Live service dependency topology status."""
    backend_status = "HEALTHY"
    if _fault_state["db_unavailable"]:
        backend_status = "DOWN"
    elif _fault_state["slow_queries"] or _fault_state["high_error_rate"]:
        backend_status = "DEGRADED"

    db_status = "DOWN" if _fault_state["db_unavailable"] else ("DEGRADED" if _fault_state["slow_queries"] else "HEALTHY")

    # Probe DB readiness (SELECT 1 against the live pool)
    real_db_status = db_status
    if db_pool:
        try:
            async with db_pool.acquire() as _conn:
                await _conn.fetchval("SELECT 1")
        except Exception:
            real_db_status = "DOWN"

    # Probe Prometheus health endpoint
    prom_status = "UNKNOWN"
    prom_url = os.getenv("PROMETHEUS_URL", "http://localhost:9090")
    try:
        import httpx
        async with httpx.AsyncClient(timeout=2.0) as _client:
            _r = await _client.get(f"{prom_url}/-/healthy")
            prom_status = "HEALTHY" if _r.status_code == 200 else "DEGRADED"
    except Exception:
        prom_status = "UNREACHABLE"

    return {
        "services": [
            {
                "name": "FRONTEND",
                "type": "Nginx / Static UI",
                "status": "HEALTHY",
                "port": 3000,
                "dependencies": ["BACKEND"],
            },
            {
                "name": "BACKEND",
                "type": "FastAPI App",
                "status": backend_status,
                "port": 8000,
                "dependencies": ["DATABASE", "PROMETHEUS"],
            },
            {
                "name": "DATABASE",
                "type": "PostgreSQL 16",
                "status": real_db_status,
                "port": int(os.getenv("DB_PORT", "5432")),
                "dependencies": [],
            },
            {
                "name": "PROMETHEUS",
                "type": "Prometheus",
                "status": prom_status,
                "port": 9090,
                "dependencies": [],
            },
        ]
    }


# ---------------------------------------------------------------------------
# Phase 8 — Evaluation Dashboard API Endpoints
# ---------------------------------------------------------------------------

@app.get("/evaluation/summary")
async def get_evaluation_summary():
    """Summary KPI metrics for the Evaluation Dashboard."""
    pool = await get_db_pool()
    try:
        async with pool.acquire() as conn:
            # Check if experiment_runs table exists
            table_exists = await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'experiment_runs')"
            )
            if not table_exists:
                return {
                    "total_experiments": 0,
                    "successful_experiments": 0,
                    "failed_experiments": 0,
                    "avg_mttr_seconds": 0.0,
                    "median_mttr_seconds": 0.0,
                    "rollback_rate_pct": 0.0,
                    "escalation_rate_pct": 0.0,
                }

            total = await conn.fetchval("SELECT COUNT(*) FROM experiment_runs WHERE is_warmup = FALSE")
            succ = await conn.fetchval("SELECT COUNT(*) FROM experiment_runs WHERE is_warmup = FALSE AND success = TRUE")
            failed = await conn.fetchval("SELECT COUNT(*) FROM experiment_runs WHERE is_warmup = FALSE AND success = FALSE")
            avg_mttr = await conn.fetchval("SELECT AVG(mttr_seconds) FROM experiment_runs WHERE is_warmup = FALSE AND success = TRUE")
            med_mttr = await conn.fetchval("SELECT PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY mttr_seconds) FROM experiment_runs WHERE is_warmup = FALSE AND success = TRUE")
            rollbacks = await conn.fetchval("SELECT COUNT(*) FROM experiment_runs WHERE is_warmup = FALSE AND rollback = TRUE")
            escalated = await conn.fetchval("SELECT COUNT(*) FROM experiment_runs WHERE is_warmup = FALSE AND escalated = TRUE")

            tot_val = total or 1
            return {
                "total_experiments": total or 0,
                "successful_experiments": succ or 0,
                "failed_experiments": failed or 0,
                "avg_mttr_seconds": round(float(avg_mttr or 0.0), 2),
                "median_mttr_seconds": round(float(med_mttr or 0.0), 2),
                "rollback_rate_pct": round(float(rollbacks or 0) / tot_val * 100.0, 1),
                "escalation_rate_pct": round(float(escalated or 0) / tot_val * 100.0, 1),
            }
    except Exception as exc:
        logger.error("Failed to fetch evaluation summary: %s", exc)
        return {
            "total_experiments": 0,
            "successful_experiments": 0,
            "failed_experiments": 0,
            "avg_mttr_seconds": 0.0,
            "median_mttr_seconds": 0.0,
            "rollback_rate_pct": 0.0,
            "escalation_rate_pct": 0.0,
        }


@app.get("/evaluation/comparison")
async def get_evaluation_comparison():
    """Strategy performance comparison table data."""
    pool = await get_db_pool()
    try:
        async with pool.acquire() as conn:
            table_exists = await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'experiment_runs')"
            )
            if not table_exists:
                return {"strategies": []}

            rows = await conn.fetch(
                """
                SELECT strategy,
                       COUNT(*) AS total_runs,
                       COUNT(*) FILTER (WHERE success = TRUE) AS success_runs,
                       AVG(mttr_seconds) FILTER (WHERE success = TRUE) AS avg_mttr,
                       PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY mttr_seconds) FILTER (WHERE success = TRUE) AS median_mttr,
                       AVG(downtime_seconds) AS avg_downtime,
                       AVG(decision_latency_seconds) AS avg_decision_latency,
                       COUNT(*) FILTER (WHERE rollback = TRUE) AS rollback_runs
                FROM experiment_runs
                WHERE is_warmup = FALSE
                GROUP BY strategy
                ORDER BY strategy ASC
                """
            )
            res = []
            for r in rows:
                t = r["total_runs"] or 1
                res.append({
                    "strategy": r["strategy"],
                    "total_runs": r["total_runs"],
                    "success_rate_pct": round(float(r["success_runs"] or 0) / t * 100.0, 1),
                    "avg_mttr_seconds": round(float(r["avg_mttr"] or 0.0), 2),
                    "median_mttr_seconds": round(float(r["median_mttr"] or 0.0), 2),
                    "avg_downtime_seconds": round(float(r["avg_downtime"] or 0.0), 2),
                    "avg_decision_latency_seconds": round(float(r["avg_decision_latency"] or 0.0), 3),
                    "rollback_rate_pct": round(float(r["rollback_runs"] or 0) / t * 100.0, 1),
                })
            return {"strategies": res}
    except Exception as exc:
        logger.error("Failed to fetch evaluation comparison: %s", exc)
        return {"strategies": []}


@app.get("/evaluation/scenarios")
async def get_evaluation_scenarios():
    """Fault scenario performance breakdown data."""
    pool = await get_db_pool()
    try:
        async with pool.acquire() as conn:
            table_exists = await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'experiment_runs')"
            )
            if not table_exists:
                return {"scenarios": []}

            rows = await conn.fetch(
                """
                SELECT scenario_id, strategy,
                       COUNT(*) AS total_runs,
                       COUNT(*) FILTER (WHERE success = TRUE) AS success_runs,
                       AVG(mttr_seconds) AS avg_mttr
                FROM experiment_runs
                WHERE is_warmup = FALSE
                GROUP BY scenario_id, strategy
                ORDER BY scenario_id ASC, strategy ASC
                """
            )
            sc_map = {}
            for r in rows:
                sid = r["scenario_id"]
                if sid not in sc_map:
                    sc_map[sid] = {"scenario_id": sid, "strategies": {}}
                t = r["total_runs"] or 1
                sc_map[sid]["strategies"][r["strategy"]] = {
                    "avg_mttr": round(float(r["avg_mttr"] or 0.0), 2),
                    "success_rate": round(float(r["success_runs"] or 0) / t * 100.0, 1),
                }
            return {"scenarios": list(sc_map.values())}
    except Exception as exc:
        logger.error("Failed to fetch evaluation scenarios: %s", exc)
        return {"scenarios": []}


@app.get("/evaluation/runs")
async def get_evaluation_runs(limit: int = 50):
    """List recent evaluation experiment runs."""
    pool = await get_db_pool()
    try:
        async with pool.acquire() as conn:
            table_exists = await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'experiment_runs')"
            )
            if not table_exists:
                return {"runs": []}

            rows = await conn.fetch(
                """
                SELECT * FROM experiment_runs
                ORDER BY started_at DESC
                LIMIT $1
                """,
                limit,
            )
            res = []
            for r in rows:
                d = dict(r)
                for k in ("started_at", "fault_injected_at", "incident_detected_at", "decision_at", "remediation_started_at", "recovered_at", "created_at"):
                    if hasattr(d.get(k), "isoformat"):
                        d[k] = d[k].isoformat()
                d["raw_logs"] = _as_json_dict(d.get("raw_logs"))
                d["system_config"] = _as_json_dict(d.get("system_config"))
                res.append(d)
            return {"runs": res}
    except Exception as exc:
        logger.error("Failed to fetch evaluation runs: %s", exc)
        return {"runs": []}


@app.get("/evaluation/runs/{run_id}")
async def get_evaluation_run_detail(run_id: str):
    """Fetch single evaluation run detail by experiment_run_id."""
    pool = await get_db_pool()
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM experiment_runs WHERE experiment_run_id = $1", run_id
            )
            if not row:
                raise HTTPException(status_code=404, detail=f"Experiment run {run_id} not found")
            res = dict(row)
            for k in ("started_at", "fault_injected_at", "incident_detected_at", "decision_at", "remediation_started_at", "recovered_at", "created_at"):
                if hasattr(res.get(k), "isoformat"):
                    res[k] = res[k].isoformat()
            res["raw_logs"] = _as_json_dict(res.get("raw_logs"))
            res["system_config"] = _as_json_dict(res.get("system_config"))

            # Construct execution timeline
            timeline = []
            if res.get("started_at"):
                timeline.append({"time": res["started_at"], "event": "Experiment Run Initialized"})
            if res.get("fault_injected_at"):
                timeline.append({"time": res["fault_injected_at"], "event": f"Fault Injected ({res.get('scenario_id')})"})
            if res.get("incident_detected_at"):
                timeline.append({"time": res["incident_detected_at"], "event": "Incident Detected"})
            if res.get("decision_at"):
                timeline.append({"time": res["decision_at"], "event": f"Strategy ({res.get('strategy')}) Decision Formulated"})
            if res.get("remediation_started_at"):
                timeline.append({"time": res["remediation_started_at"], "event": f"Remediation Execution Started ({res.get('selected_remediation')})"})
            if res.get("recovered_at"):
                timeline.append({"time": res["recovered_at"], "event": "System Service Recovery Confirmed"})

            res["timeline"] = timeline
            return res
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to fetch evaluation run detail: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


