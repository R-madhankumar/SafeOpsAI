"""
SafeOpsAI — Remediation Agent: Database Layer
==============================================
Manages PostgreSQL connection pool, migration execution, and lifecycle persistence.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import asyncpg

from .config import DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASS
from .models import RemediationResponse, RollbackInfo, TimelineEntry

log = logging.getLogger("remediation_agent.db")

_MIGRATION_FILE = Path(__file__).parent.parent / "database" / "migrate_step8.sql"
_pool: asyncpg.Pool | None = None


async def init_pool(retries: int = 10, delay: float = 3.0) -> None:
    global _pool
    import asyncio
    for attempt in range(retries):
        try:
            _pool = await asyncpg.create_pool(
                host=DB_HOST, port=DB_PORT,
                database=DB_NAME, user=DB_USER, password=DB_PASS,
                min_size=2, max_size=5, command_timeout=15,
            )
            log.info("DB pool ready (%s@%s:%s/%s)", DB_USER, DB_HOST, DB_PORT, DB_NAME)
            await _run_migration()
            return
        except Exception as exc:
            log.warning("DB connect attempt %d/%d: %s", attempt + 1, retries, exc)
            if attempt < retries - 1:
                await asyncio.sleep(delay)
    raise RuntimeError(f"Cannot connect to PostgreSQL after {retries} attempts")


async def close_pool() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("DB pool not initialised — call init_pool() first")
    return _pool


async def _run_migration() -> None:
    migration_paths = [
        _MIGRATION_FILE,
        Path(__file__).parent.parent.parent / "database" / "migrate_step8.sql",
        Path("/app/database/migrate_step8.sql"),
    ]
    sql_text = None
    for p in migration_paths:
        if p.exists():
            sql_text = p.read_text()
            log.info("Found migration at %s", p)
            break

    if not sql_text:
        log.warning("Step 8 migration file not found — skipping auto-migration")
        return

    async with get_pool().acquire() as conn:
        try:
            await conn.execute(sql_text)
            log.info("Step 8 migration applied successfully")
        except Exception as exc:
            log.warning("Migration warning (may already be applied): %s", exc)


def _as_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return {}
    if isinstance(value, dict):
        return dict(value)
    return {}


async def fetch_remediation_queue(limit: int = 10) -> List[dict[str, Any]]:
    """Return incidents that passed sandbox validation and need production remediation."""
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT * FROM remediation_queue LIMIT $1
            """,
            limit,
        )
    return [dict(r) for r in rows]


async def fetch_incident_context(incident_id: int) -> Optional[dict[str, Any]]:
    """Fetch complete context for pre-remediation safety gate validation."""
    pool = get_pool()
    async with pool.acquire() as conn:
        inc = await conn.fetchrow("SELECT * FROM incidents WHERE id = $1", incident_id)
        if not inc:
            return None
        
        # Check RCA decision
        rca = await conn.fetchrow(
            "SELECT * FROM agent_decisions WHERE incident_id = $1 AND agent_name = 'root_cause'",
            incident_id,
        )
        # Check MCDM decision
        mcdm = await conn.fetchrow(
            "SELECT * FROM agent_decisions WHERE incident_id = $1 AND agent_name = 'coordinator'",
            incident_id,
        )
        # Check selected sandbox validation
        sandbox_action = await conn.fetchrow(
            """
            SELECT * FROM remediation_actions
            WHERE incident_id = $1 AND execution_authorized = TRUE AND selection_status = 'selected'
            ORDER BY id DESC LIMIT 1
            """,
            incident_id,
        )

    return {
        "incident": dict(inc),
        "root_cause_decision": dict(rca) if rca else None,
        "mcdm_decision": dict(mcdm) if mcdm else None,
        "sandbox_action": dict(sandbox_action) if sandbox_action else None,
    }


async def get_attempt_count_for_incident(incident_id: int) -> int:
    """Return the number of remediation attempts already performed for an incident."""
    pool = get_pool()
    async with pool.acquire() as conn:
        count = await conn.fetchval(
            """
            SELECT COUNT(*) FROM remediation_actions
            WHERE incident_id = $1 AND state IN ('EXECUTING', 'OBSERVING', 'SUCCESS', 'DEGRADED', 'ROLLING_BACK', 'ROLLED_BACK', 'FAILED', 'ESCALATED')
            """,
            incident_id,
        )
    return count or 0


async def mark_remediating(incident_id: int) -> bool:
    pool = get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE incidents
            SET    remediating_at = NOW(), status = 'remediating'
            WHERE  id             = $1
              AND  remediating_at IS NULL
              AND  status         = 'open'
            """,
            incident_id,
        )
    return result.split()[-1] != "0"


async def reset_remediating(incident_id: int) -> None:
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE incidents SET remediating_at = NULL, status = 'open' WHERE id = $1",
            incident_id,
        )


async def update_incident_status(incident_id: int, status: str) -> None:
    pool = get_pool()
    async with pool.acquire() as conn:
        if status == "resolved":
            await conn.execute(
                """
                UPDATE incidents
                SET status = 'resolved', resolved_at = NOW(),
                    mttr_seconds = EXTRACT(EPOCH FROM (NOW() - detected_at))::integer
                WHERE id = $1
                """,
                incident_id,
            )
        else:
            await conn.execute(
                "UPDATE incidents SET status = $1 WHERE id = $2",
                status, incident_id,
            )


async def update_remediation_action_record(
    action_id: int,
    state: str,
    recovery_score: float,
    recovery_metrics: dict[str, Any],
    snapshot_id: str,
    outcome: str,
    notes: str,
    rollback_performed: bool = False,
    rollback_reason: Optional[str] = None,
    attempt_number: int = 1,
    max_attempts: int = 2,
    escalated: bool = False,
    escalation_reason: Optional[str] = None,
) -> None:
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE remediation_actions
            SET    state              = $1,
                   recovery_score     = $2,
                   recovery_metrics   = $3::jsonb,
                   snapshot_id        = $4,
                   outcome            = $5,
                   notes              = $6,
                   executed_at        = COALESCE(executed_at, NOW()),
                   rollback_performed = $7,
                   rollback_ended_at  = CASE WHEN $7 THEN NOW() ELSE rollback_ended_at END,
                   rollback_reason    = $8,
                   attempt_number     = $9,
                   max_attempts       = $10,
                   escalated          = $11,
                   escalation_reason  = $12
            WHERE  id = $13
            """,
            state,
            recovery_score,
            json.dumps(recovery_metrics),
            snapshot_id,
            outcome,
            notes,
            rollback_performed,
            rollback_reason,
            attempt_number,
            max_attempts,
            escalated,
            escalation_reason,
            action_id,
        )


async def write_agent_decision(
    incident_id: int,
    score: float,
    reasoning: str,
    raw_output: dict[str, Any],
    elapsed_ms: int,
) -> int:
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO agent_decisions
                (incident_id, agent_name, score, reasoning, raw_output, execution_time_ms)
            VALUES ($1, 'remediation', $2, $3, $4::jsonb, $5)
            RETURNING id
            """,
            incident_id,
            round(score, 2),
            reasoning,
            json.dumps(raw_output),
            elapsed_ms,
        )
    return row["id"]


async def get_remediation_by_id(remediation_id: int) -> Optional[dict[str, Any]]:
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT ra.*, i.incident_type, i.service AS incident_service
            FROM   remediation_actions ra
            JOIN   incidents i ON i.id = ra.incident_id
            WHERE  ra.id = $1
            """,
            remediation_id,
        )
    if not row:
        return None
    r = dict(row)
    r["checks"] = _as_dict(r.get("checks"))
    r["baseline_metrics"] = _as_dict(r.get("baseline_metrics"))
    r["after_metrics"] = _as_dict(r.get("after_metrics"))
    r["recovery_metrics"] = _as_dict(r.get("recovery_metrics"))
    for k in ("sandbox_started_at", "sandbox_ended_at", "executed_at", "rollback_started_at", "rollback_ended_at"):
        if hasattr(r.get(k), "isoformat"):
            r[k] = r[k].isoformat()
    return r


async def get_remediation_for_incident(incident_id: int) -> List[dict[str, Any]]:
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT ra.*, i.incident_type, i.service AS incident_service
            FROM   remediation_actions ra
            JOIN   incidents i ON i.id = ra.incident_id
            WHERE  ra.incident_id = $1
            ORDER BY ra.id ASC
            """,
            incident_id,
        )
    result = []
    for row in rows:
        r = dict(row)
        r["checks"] = _as_dict(r.get("checks"))
        r["baseline_metrics"] = _as_dict(r.get("baseline_metrics"))
        r["after_metrics"] = _as_dict(r.get("after_metrics"))
        r["recovery_metrics"] = _as_dict(r.get("recovery_metrics"))
        for k in ("sandbox_started_at", "sandbox_ended_at", "executed_at", "rollback_started_at", "rollback_ended_at"):
            if hasattr(r.get(k), "isoformat"):
                r[k] = r[k].isoformat()
        result.append(r)
    return result
