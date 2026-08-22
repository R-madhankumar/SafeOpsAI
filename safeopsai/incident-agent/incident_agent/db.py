"""
Incident Agent — Database Layer
================================
asyncpg connection pool + CRUD operations for the incidents table.
Runs the Step 3 migration (migrate_step3.sql) on first connect so
the new columns exist even if the container started before the
migration was applied manually.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import asyncpg

from .config import DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASS
from .models import Incident, IncidentStatus

log = logging.getLogger("incident_agent.db")

_MIGRATION_FILE = Path(__file__).parent.parent / "database" / "migrate_step3.sql"

_pool: asyncpg.Pool | None = None


# ── Pool lifecycle ────────────────────────────────────────────────────────

async def init_pool(retries: int = 10, delay: float = 3.0) -> None:
    """Create the connection pool and run the migration."""
    global _pool
    import asyncio

    for attempt in range(retries):
        try:
            _pool = await asyncpg.create_pool(
                host=DB_HOST,
                port=DB_PORT,
                database=DB_NAME,
                user=DB_USER,
                password=DB_PASS,
                min_size=2,
                max_size=5,
                command_timeout=10,
            )
            log.info("Database pool established (%s@%s:%s/%s)", DB_USER, DB_HOST, DB_PORT, DB_NAME)
            await _run_migration()
            return
        except Exception as exc:
            log.warning("DB connect attempt %d/%d failed: %s", attempt + 1, retries, exc)
            if attempt < retries - 1:
                await asyncio.sleep(delay)

    raise RuntimeError(f"Could not connect to PostgreSQL after {retries} attempts")


async def close_pool() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
        log.info("Database pool closed")


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Database pool not initialised — call init_pool() first")
    return _pool


async def _run_migration() -> None:
    """Apply migrate_step3.sql idempotently."""
    if not _MIGRATION_FILE.exists():
        log.warning("Migration file not found at %s — skipping", _MIGRATION_FILE)
        return
    sql = _MIGRATION_FILE.read_text()
    async with get_pool().acquire() as conn:
        try:
            await conn.execute(sql)
            log.info("Step 3 migration applied successfully")
        except Exception as exc:
            # Migration may have already been applied — log and continue
            log.warning("Migration warning (may already be applied): %s", exc)


# ── Incident CRUD ─────────────────────────────────────────────────────────

async def insert_incident(incident: Incident) -> int:
    """
    Insert a new incident row.

    Returns
    -------
    int  the new row id
    """
    pool = get_pool()
    metrics_json = json.dumps(incident.metrics_snapshot.to_dict())

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO incidents
                (service, fault_type, severity, status, description,
                 detected_at, fingerprint, incident_type,
                 detection_source, metrics_snapshot)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb)
            ON CONFLICT DO NOTHING
            RETURNING id
            """,
            incident.service,
            incident.fault_type,
            incident.severity.value,
            incident.status.value,
            incident.description,
            incident.detected_at,
            incident.fingerprint,
            incident.incident_type.value,
            incident.detection_source,
            metrics_json,
        )

    if row is None:
        # ON CONFLICT DO NOTHING — a duplicate fingerprint was active
        log.warning("Duplicate active incident blocked by DB constraint: %s", incident.fingerprint)
        # Fetch the existing id for bookkeeping
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id FROM incidents WHERE fingerprint=$1 AND status='open'",
                incident.fingerprint,
            )
        return row["id"] if row else -1

    log.info(
        "Incident inserted: id=%d type=%s service=%s severity=%s",
        row["id"],
        incident.incident_type.value,
        incident.service,
        incident.severity.value,
    )
    return row["id"]


async def resolve_incident(incident_id: int, resolved_at: datetime | None = None) -> None:
    """
    Mark an incident as RESOLVED and calculate mttr_seconds.
    """
    pool = get_pool()
    if resolved_at is None:
        resolved_at = datetime.now(timezone.utc)

    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE incidents
            SET
                status       = 'resolved',
                resolved_at  = $1,
                mttr_seconds = EXTRACT(EPOCH FROM ($1 - detected_at))::INTEGER
            WHERE id = $2
              AND status = 'open'
            """,
            resolved_at,
            incident_id,
        )
    log.info("Incident resolved: id=%d resolved_at=%s", incident_id, resolved_at.isoformat())


async def resolve_by_fingerprint(fingerprint: str) -> int | None:
    """
    Resolve the open incident with the given fingerprint.
    Returns the incident id, or None if not found.
    """
    pool = get_pool()
    resolved_at = datetime.now(timezone.utc)

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE incidents
            SET
                status       = 'resolved',
                resolved_at  = $1,
                mttr_seconds = EXTRACT(EPOCH FROM ($1 - detected_at))::INTEGER
            WHERE fingerprint = $2
              AND status      = 'open'
            RETURNING id, mttr_seconds
            """,
            resolved_at,
            fingerprint,
        )

    if row:
        log.info(
            "Incident resolved by fingerprint: id=%d fingerprint=%s mttr=%ds",
            row["id"], fingerprint, row["mttr_seconds"] or 0,
        )
        return row["id"]
    return None


async def get_open_incidents() -> list[dict[str, Any]]:
    """Return all currently open incidents."""
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, incident_type, service, severity, fingerprint,
                   detected_at, description
            FROM   incidents
            WHERE  status = 'open'
            ORDER  BY detected_at DESC
            """
        )
    return [dict(r) for r in rows]


async def get_incident_by_fingerprint(fingerprint: str) -> dict[str, Any] | None:
    """Return the open incident matching fingerprint, or None."""
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM incidents WHERE fingerprint=$1 AND status='open'",
            fingerprint,
        )
    return dict(row) if row else None
