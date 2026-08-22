"""
SafeOpsAI — Coordinator Agent: Database Layer
================================================
asyncpg pool + the queries the coordinator needs:
  - init_pool / close_pool
  - run Step 6 migration
  - fetch incidents from the coordinator_queue (with per-candidate scores)
  - read / write runtime weights in coordinator_config
  - claim an incident (prevents duplicate decisions)
  - write the coordinator decision to agent_decisions
  - query helpers for the API
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import asyncpg

from .config import DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASS
from .mcdm import CandidateRow
from .models import CoordinatorConfig

log = logging.getLogger("coordinator_agent.db")

_MIGRATION_FILE = Path(__file__).parent.parent / "database" / "migrate_step6.sql"
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
    if not _MIGRATION_FILE.exists():
        log.warning("Step 6 migration file not found at %s — skipping", _MIGRATION_FILE)
        return
    sql = _MIGRATION_FILE.read_text()
    async with get_pool().acquire() as conn:
        try:
            await conn.execute(sql)
            log.info("Step 6 migration applied")
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


# ── Runtime weights ─────────────────────────────────────────────────────────

async def get_config() -> CoordinatorConfig:
    """Read the latest runtime weights + method from coordinator_config."""
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT cost_weight, reliability_weight, security_weight, method, note "
            "FROM coordinator_config ORDER BY id DESC LIMIT 1"
        )
    if row is None:
        log.warning("coordinator_config empty — using defaults")
        return CoordinatorConfig()
    return CoordinatorConfig(
        cost_weight        = float(row["cost_weight"]),
        reliability_weight = float(row["reliability_weight"]),
        security_weight    = float(row["security_weight"]),
        method             = row["method"],
        note               = row["note"] or "",
    )


async def set_config(
    cost: float,
    reliability: float,
    security: float,
    method: str,
    note: str = "",
) -> CoordinatorConfig:
    """Insert a new weight set (becomes the active set immediately)."""
    method = method if method in ("weighted_sum", "topsis") else "weighted_sum"
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO coordinator_config
                (cost_weight, reliability_weight, security_weight, method, note)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING cost_weight, reliability_weight, security_weight, method, note
            """,
            cost, reliability, security, method, note,
        )
    cfg = CoordinatorConfig(
        cost_weight        = float(row["cost_weight"]),
        reliability_weight = float(row["reliability_weight"]),
        security_weight    = float(row["security_weight"]),
        method             = row["method"],
        note               = row["note"] or "",
    )
    log.info("Coordinator weights updated: %s", cfg.to_dict())
    return cfg


# ── Coordinator queue ───────────────────────────────────────────────────────

async def fetch_coordinator_queue(limit: int = 10) -> list[dict[str, Any]]:
    """
    Return scored incidents that need a coordinator decision, together with
    their per-candidate criterion scores (reconstructed from the cost /
    reliability / security rows in agent_decisions).
    """
    pool = get_pool()
    result = []
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, incident_type, service, severity, fault_type
            FROM   coordinator_queue
            LIMIT  $1
            """,
            limit,
        )
        for r in rows:
            candidates = await _candidate_rows(conn, r["id"])
            result.append({
                "incident_id":   r["id"],
                "incident_type": r["incident_type"] or "",
                "service":       r["service"],
                "severity":      r["severity"],
                "fault_type":    r["fault_type"],
                "candidates":    candidates,
            })
    return result


async def _candidate_rows(conn, incident_id: int) -> list[CandidateRow]:
    """Reconstruct CandidateRows from the scoring decisions for an incident."""
    rows = await conn.fetch(
        """
        SELECT agent_name, score, raw_output
        FROM   agent_decisions
        WHERE  incident_id = $1
          AND  agent_name IN ('cost', 'reliability', 'security')
        """,
        incident_id,
    )
    by_candidate: dict[tuple[str, str], dict[str, Any]] = {}
    order: list[tuple[str, str]] = []
    for r in rows:
        raw = _as_dict(r["raw_output"])
        cand = raw.get("candidate") or {}
        action = str(cand.get("action", "investigate"))
        target = str(cand.get("target", "unknown"))
        key = (action, target)
        if key not in by_candidate:
            by_candidate[key] = {"action": action, "target": target, "priority": int(cand.get("priority", 1) or 1)}
            order.append(key)
        by_candidate[key][r["agent_name"]] = float(r["score"])

    result = []
    for key in order:
        c = by_candidate[key]
        result.append(CandidateRow(
            action       = c["action"],
            target       = c["target"],
            cost         = float(c.get("cost", 0.0)),
            reliability  = float(c.get("reliability", 0.0)),
            security     = float(c.get("security", 0.0)),
            priority     = c["priority"],
        ))
    return result


async def mark_coordinated(incident_id: int) -> bool:
    pool = get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE incidents
            SET    coordinated_at = NOW()
            WHERE  id             = $1
              AND  coordinated_at IS NULL
              AND  status         = 'open'
            """,
            incident_id,
        )
    claimed = result.split()[-1] != "0"
    if claimed:
        log.debug("Incident %d claimed for coordination", incident_id)
    return claimed


async def reset_coordinated(incident_id: int) -> None:
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE incidents SET coordinated_at = NULL WHERE id = $1",
            incident_id,
        )


async def write_decision(
    incident_id: int,
    raw_json: dict[str, Any],
    winner_score: float,
    reasoning: str,
    elapsed_ms: int,
) -> int:
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO agent_decisions
                (incident_id, agent_name, score, reasoning,
                 raw_output, execution_time_ms)
            VALUES ($1, 'coordinator', $2, $3, $4::jsonb, $5)
            RETURNING id
            """,
            incident_id,
            round(winner_score, 2),
            reasoning,
            json.dumps(raw_json),
            elapsed_ms,
        )
    log.info(
        "Coordinator decision written: incident_id=%d decision_id=%d winner_score=%.2f",
        incident_id, row["id"], winner_score,
    )
    return row["id"]


# ── Query helpers for the API ───────────────────────────────────────────────

async def get_decisions(limit: int = 50) -> list[dict[str, Any]]:
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT ad.incident_id, i.incident_type, i.service, i.severity, i.status,
                   ad.score, ad.reasoning, ad.raw_output, ad.created_at
            FROM   agent_decisions ad
            JOIN   incidents i ON i.id = ad.incident_id
            WHERE  ad.agent_name = 'coordinator'
            ORDER  BY ad.created_at DESC
            LIMIT  $1
            """,
            limit,
        )
    result = []
    for r in rows:
        row = dict(r)
        row["raw_output"] = _as_dict(row.get("raw_output"))
        if hasattr(row.get("created_at"), "isoformat"):
            row["created_at"] = row["created_at"].isoformat()
        result.append(row)
    return result


async def get_decision_for_incident(incident_id: int) -> dict[str, Any] | None:
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT ad.incident_id, i.incident_type, i.service, i.severity, i.status,
                   ad.score, ad.reasoning, ad.raw_output, ad.created_at
            FROM   agent_decisions ad
            JOIN   incidents i ON i.id = ad.incident_id
            WHERE  ad.incident_id = $1 AND ad.agent_name = 'coordinator'
            """,
            incident_id,
        )
    if not row:
        return None
    result = dict(row)
    result["raw_output"] = _as_dict(result.get("raw_output"))
    if hasattr(result.get("created_at"), "isoformat"):
        result["created_at"] = result["created_at"].isoformat()
    return result