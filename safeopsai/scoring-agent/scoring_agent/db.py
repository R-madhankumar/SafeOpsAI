"""
SafeOpsAI — Scoring Agent: Database Layer
============================================
asyncpg pool + the queries the scoring agent needs:
  - init_pool / close_pool
  - run Step 5 migration
  - fetch incidents from the scoring_queue view
  - claim an incident (prevents duplicate scoring)
  - write the three criterion scores to agent_decisions
  - query helpers for the API
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import asyncpg

from .config import DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASS
from .models import Candidate, CandidateContext

log = logging.getLogger("scoring_agent.db")

_MIGRATION_FILE = Path(__file__).parent.parent / "database" / "migrate_step5.sql"
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
        log.warning("Step 5 migration file not found at %s — skipping", _MIGRATION_FILE)
        return
    sql = _MIGRATION_FILE.read_text()
    async with get_pool().acquire() as conn:
        try:
            await conn.execute(sql)
            log.info("Step 5 migration applied")
        except Exception as exc:
            log.warning("Migration warning (may already be applied): %s", exc)


def _as_dict(value: Any) -> dict[str, Any]:
    """asyncpg returns jsonb as strings — normalise to dict."""
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


# ── Scoring queue ───────────────────────────────────────────────────────────

async def fetch_scoring_queue(limit: int = 10) -> list[dict[str, Any]]:
    """
    Return incidents that have a root_cause decision but are not yet scored,
    joined with the root_cause output so the scorers have full context.
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT i.id, i.incident_type, i.service, i.severity, i.fault_type,
                   ad.root_cause_output
            FROM   scoring_queue i
            LEFT JOIN agent_decisions ad
                   ON ad.incident_id = i.id AND ad.agent_name = 'root_cause'
            LIMIT  $1
            """,
            limit,
        )
    result = []
    for r in rows:
        rca = _as_dict(r["root_cause_output"])
        context = CandidateContext(
            incident_id        = r["id"],
            incident_type      = r["incident_type"] or "",
            service            = r["service"],
            severity           = r["severity"],
            fault_type         = r["fault_type"],
            root_cause_service = rca.get("root_cause_service", ""),
            cause_type         = rca.get("cause_type", ""),
        )
        raw_candidates = rca.get("remediation_candidates") or []
        candidates = [Candidate.from_dict(c, proposed_by_rca=True) for c in raw_candidates] if isinstance(raw_candidates, list) else []
        result.append({
            "incident_id": r["id"],
            "context":     context,
            "candidates":  candidates,
        })
    return result


async def mark_scoring(incident_id: int) -> bool:
    """Atomically claim an incident for scoring. Returns True if claimed."""
    pool = get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE incidents
            SET    scoring_at = NOW()
            WHERE  id         = $1
              AND  scoring_at IS NULL
              AND  status     = 'open'
            """,
            incident_id,
        )
    claimed = result.split()[-1] != "0"
    if claimed:
        log.debug("Incident %d claimed for scoring", incident_id)
    return claimed


async def reset_scoring(incident_id: int) -> None:
    """Release the scoring claim if something failed mid-way."""
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE incidents SET scoring_at = NULL WHERE id = $1",
            incident_id,
        )


async def write_scored_candidates(incident_id: int, scored_candidates, elapsed_ms: int) -> int:
    """
    Write one agent_decisions row per (candidate, criterion).
    agent_name: cost | reliability | security.
    score:      the 0–10 criterion score.
    reasoning:  the auditable justification.
    raw_output: the full per-criterion breakdown (components).
    Returns the number of rows written.
    """
    pool = get_pool()
    written = 0
    async with pool.acquire() as conn:
        async with conn.transaction():
            for sc in scored_candidates:
                for criterion, dim in sc.scores.items():
                    raw = {
                        "candidate": sc.candidate.to_dict(),
                        "criterion": criterion,
                        "score":     round(dim.score, 2),
                        "components": dim.components,
                        "convention": "higher-is-better",
                    }
                    await conn.execute(
                        """
                        INSERT INTO agent_decisions
                            (incident_id, agent_name, score, reasoning,
                             raw_output, execution_time_ms)
                        VALUES ($1, $2, $3, $4, $5::jsonb, $6)
                        """,
                        incident_id,
                        criterion,
                        round(dim.score, 2),
                        dim.justification,
                        json.dumps(raw),
                        elapsed_ms,
                    )
                    written += 1
    log.info(
        "Scored incident %d — %d candidate(s), %d decision rows written",
        incident_id, len(scored_candidates), written,
    )
    return written


# ── Query helpers for the API ───────────────────────────────────────────────

async def get_scored_incidents(limit: int = 50) -> list[dict[str, Any]]:
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT incident_id, incident_type, affected_service, severity,
                   status, fault_type, cost_score, reliability_score,
                   security_score, cost_reasoning, reliability_reasoning,
                   security_reasoning, scored_at
            FROM   scoring_results
            LIMIT  $1
            """,
            limit,
        )
    result = []
    for r in rows:
        row = dict(r)
        for k, v in row.items():
            if hasattr(v, "isoformat"):
                row[k] = v.isoformat()
        result.append(row)
    return result


async def get_scored_for_incident(incident_id: int) -> dict[str, Any] | None:
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT ad.id, ad.agent_name, ad.score, ad.reasoning, ad.raw_output
            FROM   agent_decisions ad
            WHERE  ad.incident_id = $1
              AND  ad.agent_name IN ('cost', 'reliability', 'security')
            ORDER  BY ad.agent_name
            """,
            incident_id,
        )
    if not rows:
        return None
    result = []
    for r in rows:
        row = dict(r)
        row["raw_output"] = _as_dict(row.get("raw_output"))
        result.append(row)
    return {"incident_id": incident_id, "scores": result}