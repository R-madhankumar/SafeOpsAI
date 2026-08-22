"""
Root Cause Agent — Database Layer
=====================================
asyncpg pool + all DB operations the RCA agent needs:
  - init_pool / close_pool
  - run Step 4 migration
  - fetch incidents from rca_queue
  - mark incident as diagnosing (prevents duplicate RCA)
  - write diagnosis to agent_decisions
  - fetch completed diagnoses for the API
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import asyncpg

from .config import DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASS
from .models import DiagnosisOutput, RCARequest

log = logging.getLogger("rca.db")

_MIGRATION_FILE = Path(__file__).parent.parent / "database" / "migrate_step4.sql"
_pool: asyncpg.Pool | None = None


# ── Pool lifecycle ──────────────────────────────────────────────────────────

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
    if not _MIGRATION_FILE.exists():
        log.warning("Step 4 migration file not found at %s — skipping", _MIGRATION_FILE)
        return
    sql = _MIGRATION_FILE.read_text()
    async with get_pool().acquire() as conn:
        try:
            await conn.execute(sql)
            log.info("Step 4 migration applied")
        except Exception as exc:
            log.warning("Migration warning (may already be applied): %s", exc)


# ── RCA queue ───────────────────────────────────────────────────────────────

async def fetch_rca_queue(limit: int = 10) -> list[RCARequest]:
    """
    Return incidents from the rca_queue view that need diagnosis.
    Limit prevents overwhelming Ollama when many incidents pile up.
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, incident_type, service, severity, fault_type,
                   fingerprint, description, detected_at, metrics_snapshot
            FROM   rca_queue
            LIMIT  $1
            """,
            limit,
        )
    result = []
    for r in rows:
        snap = _as_dict(r["metrics_snapshot"])
        result.append(RCARequest(
            incident_id      = r["id"],
            incident_type    = r["incident_type"] or "",
            service          = r["service"],
            severity         = r["severity"],
            fault_type       = r["fault_type"],
            fingerprint      = r["fingerprint"] or "",
            description      = r["description"] or "",
            detected_at      = r["detected_at"].isoformat() if r["detected_at"] else "",
            metrics_snapshot = snap,
        ))
    return result


async def mark_diagnosing(incident_id: int) -> bool:
    """
    Atomically set diagnosing_at to prevent two RCA workers from
    diagnosing the same incident. Returns True if the row was updated.
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE incidents
            SET    diagnosing_at = NOW()
            WHERE  id            = $1
              AND  diagnosing_at IS NULL
              AND  status        = 'open'
            """,
            incident_id,
        )
    updated = result.split()[-1] != "0"
    if updated:
        log.debug("Incident %d marked as diagnosing", incident_id)
    return updated


async def write_diagnosis(output: DiagnosisOutput) -> int:
    """
    Insert a row into agent_decisions for the root_cause agent.
    Returns the new decision id.
    """
    pool = get_pool()
    raw_json = json.dumps(output.to_dict())
    rca_json = json.dumps(output.to_dict())

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO agent_decisions
                (incident_id, agent_name, score, reasoning,
                 raw_output, root_cause_output,
                 execution_time_ms, llm_model)
            VALUES ($1, 'root_cause', $2, $3, $4::jsonb, $5::jsonb, $6, $7)
            RETURNING id
            """,
            output.incident_id,
            output.score_0_10,
            output.reasoning,
            raw_json,
            rca_json,
            output.execution_time_ms,
            output.llm_model,
        )
    log.info(
        "Diagnosis written: incident_id=%d decision_id=%d confidence=%.2f method=%s",
        output.incident_id, row["id"], output.confidence, output.diagnosis_method,
    )
    return row["id"]


async def reset_diagnosing(incident_id: int) -> None:
    """
    If analysis fails, release the diagnosing lock so another worker can retry.
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE incidents SET diagnosing_at = NULL WHERE id = $1",
            incident_id,
        )


# ── JSONB helpers ─────────────────────────────────────────────────────────

def _as_dict(value: Any) -> dict[str, Any]:
    """
    asyncpg returns jsonb columns as JSON strings by default.
    Normalise to a dict so callers never have to care.
    """
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


# ── Query helpers for the API ───────────────────────────────────────────────

async def get_diagnoses(limit: int = 50) -> list[dict[str, Any]]:
    """Return recent diagnoses with their incident context."""
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT * FROM rca_results
            LIMIT $1
            """,
            limit,
        )
    result = []
    for r in rows:
        row = dict(r)
        row["metrics_snapshot"] = _as_dict(row.get("metrics_snapshot"))
        if isinstance(row.get("root_cause_output"), str):
            try:
                row["root_cause_output"] = json.loads(row["root_cause_output"])
            except json.JSONDecodeError:
                pass
        for k, v in row.items():
            if hasattr(v, "isoformat"):
                row[k] = v.isoformat()
        result.append(row)
    return result


async def get_diagnosis_for_incident(incident_id: int) -> dict[str, Any] | None:
    """Return the RCA diagnosis for a specific incident, or None."""
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT * FROM rca_results WHERE incident_id = $1
            """,
            incident_id,
        )
    if not row:
        return None
    result = dict(row)
    result["metrics_snapshot"] = _as_dict(result.get("metrics_snapshot"))
    if isinstance(result.get("root_cause_output"), str):
        try:
            result["root_cause_output"] = json.loads(result["root_cause_output"])
        except json.JSONDecodeError:
            pass
    for k, v in result.items():
        if hasattr(v, "isoformat"):
            result[k] = v.isoformat()
    return result
