"""
SafeOpsAI — Sandbox Agent: Database Layer
=========================================
Manages PostgreSQL connection pool, migration execution, and persistence
for sandbox validation outcomes in remediation_actions and agent_decisions.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import asyncpg

from .config import DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASS
from .models import ValidationAttemptResult, CheckDetails, BaselineMetrics, AfterMetrics

log = logging.getLogger("sandbox_agent.db")

_MIGRATION_FILE = Path(__file__).parent.parent / "database" / "migrate_step7.sql"
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
        Path(__file__).parent.parent.parent / "database" / "migrate_step7.sql",
        Path("/app/database/migrate_step7.sql"),
    ]
    sql_text = None
    for p in migration_paths:
        if p.exists():
            sql_text = p.read_text()
            log.info("Found migration at %s", p)
            break

    if not sql_text:
        log.warning("Step 7 migration file not found — skipping auto-migration")
        return

    async with get_pool().acquire() as conn:
        try:
            await conn.execute(sql_text)
            log.info("Step 7 migration applied successfully")
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


async def fetch_sandbox_queue(limit: int = 10) -> List[dict[str, Any]]:
    """Return open incidents that have MCDM coordination but no sandbox validation yet."""
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, incident_type, service, severity, fault_type, detected_at, coordinated_at
            FROM   sandbox_queue
            LIMIT  $1
            """,
            limit,
        )
    return [dict(r) for r in rows]


async def fetch_coordinator_ranking(incident_id: int) -> List[dict[str, Any]]:
    """
    Extract ranked candidates from the coordinator's decision in agent_decisions.
    Returns list of candidate dicts ordered by rank.
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT raw_output
            FROM   agent_decisions
            WHERE  incident_id = $1 AND agent_name = 'coordinator'
            ORDER BY created_at DESC LIMIT 1
            """,
            incident_id,
        )
    if not row:
        return []

    raw = _as_dict(row["raw_output"])
    ranking = raw.get("ranking") or []
    if isinstance(ranking, list) and ranking:
        return ranking

    winner = raw.get("winner")
    if winner and isinstance(winner, dict):
        return [winner]

    return []


async def mark_sandboxed(incident_id: int) -> bool:
    """Claim an incident for sandbox validation to prevent duplicate runs."""
    pool = get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE incidents
            SET    sandbox_at = NOW()
            WHERE  id         = $1
              AND  sandbox_at IS NULL
              AND  status     = 'open'
            """,
            incident_id,
        )
    return result.split()[-1] != "0"


async def reset_sandboxed(incident_id: int) -> None:
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE incidents SET sandbox_at = NULL WHERE id = $1",
            incident_id,
        )


async def write_remediation_action(res: ValidationAttemptResult) -> int:
    """Insert a detailed record into remediation_actions table."""
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO remediation_actions
                (incident_id, action_type, target_service, final_score,
                 candidate_rank, validation_score, sandbox_passed,
                 sandbox_started_at, sandbox_ended_at, checks,
                 baseline_metrics, after_metrics, failure_reason,
                 selection_status, execution_authorized, rollback_available,
                 outcome, notes)
            VALUES
                ($1, $2, $3, $4,
                 $5, $6, $7,
                 $8::timestamptz, $9::timestamptz, $10::jsonb,
                 $11::jsonb, $12::jsonb, $13,
                 $14, $15, $16,
                 $17, $18)
            RETURNING id
            """,
            res.incident_id,
            res.action,
            res.target_service,
            res.validation_score,
            res.candidate_rank,
            res.validation_score,
            res.status == "PASS",
            res.sandbox_started_at or None,
            res.sandbox_ended_at or None,
            json.dumps(res.checks.model_dump()),
            json.dumps(res.baseline.model_dump()),
            json.dumps(res.after.model_dump()),
            res.reason,
            res.selection_status,
            res.execution_authorized,
            res.rollback_available,
            "success" if res.status == "PASS" else "failed",
            res.reason,
        )
    return row["id"]


async def write_agent_decision(
    incident_id: int,
    score: float,
    reasoning: str,
    raw_output: dict[str, Any],
    elapsed_ms: int,
) -> int:
    """Insert an entry into agent_decisions for agent_name='sandbox'."""
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO agent_decisions
                (incident_id, agent_name, score, reasoning, raw_output, execution_time_ms)
            VALUES ($1, 'sandbox', $2, $3, $4::jsonb, $5)
            RETURNING id
            """,
            incident_id,
            round(score, 2),
            reasoning,
            json.dumps(raw_output),
            elapsed_ms,
        )
    return row["id"]


async def get_validation_by_id(validation_id: int) -> Optional[dict[str, Any]]:
    """Retrieve details for a single remediation action validation."""
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT ra.*, i.incident_type, i.service AS incident_service
            FROM   remediation_actions ra
            JOIN   incidents i ON i.id = ra.incident_id
            WHERE  ra.id = $1
            """,
            validation_id,
        )
    if not row:
        return None
    r = dict(row)
    r["checks"] = _as_dict(r.get("checks"))
    r["baseline_metrics"] = _as_dict(r.get("baseline_metrics"))
    r["after_metrics"] = _as_dict(r.get("after_metrics"))
    for k in ("sandbox_started_at", "sandbox_ended_at", "executed_at"):
        if hasattr(r.get(k), "isoformat"):
            r[k] = r[k].isoformat()
    return r


async def get_validations_for_incident(incident_id: int) -> List[dict[str, Any]]:
    """Retrieve all validation attempts for an incident."""
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
        for k in ("sandbox_started_at", "sandbox_ended_at", "executed_at"):
            if hasattr(r.get(k), "isoformat"):
                r[k] = r[k].isoformat()
        result.append(r)
    return result
