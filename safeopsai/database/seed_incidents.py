"""
SafeOpsAI — Realistic Demo Incident Seeding Engine
===================================================
Generates 100+ realistic, reproducible incident records across 8 services,
8 incident types, 4 severities, and full 7-stage lifecycle progressions.

Key safety & data integrity guarantees:
1. Idempotent: uses deterministic fingerprints ('DEMO:INC-XXXX:...').
   Re-running updates or skips existing demo rows without creating duplicates.
2. Isolated: sets is_demo=TRUE and detection_source='demo_seed'.
3. Agent-safe: queue views exclude demo records so background agents never
   trigger real actions or mutate production on demo data.
4. Preserves real data: never resets the DB or deletes real incidents.
   --clean-demo strictly targets WHERE is_demo = TRUE.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import os
import random
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import asyncpg

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger("safeopsai.seeder")

# Environment configurations
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_NAME = os.getenv("DB_NAME", "safeopsdb")
DB_USER = os.getenv("DB_USER", "safeops")
DB_PASS = os.getenv("DB_PASS", "safeops123")

# Domain Catalogs
SERVICES = [
    "backend",
    "database",
    "frontend",
    "api-gateway",
    "auth-service",
    "payment-service",
    "monitoring",
    "cache",
]

INCIDENT_SPECS = {
    "slow_queries": {
        "type": "SLOW_QUERIES",
        "services": ["database", "backend"],
        "descriptions": [
            "Unindexed query scan on transactions table causing connection pool saturation",
            "Slow N+1 query loop detected during batch user synchronization",
            "Full table scan on audit_logs exceeding 5000ms threshold",
            "Contention on row-level locks during concurrent payment processing",
        ],
        "root_causes": [
            "Missing composite index on (user_id, created_at) following migration",
            "Suboptimal execution plan chosen by query planner due to stale table statistics",
            "Long-running analytical query blocking transactional update pipeline",
        ],
        "actions": ["kill_slow_queries", "pool_expansion", "failover_replica"],
        "metrics_factory": lambda rng: {
            "p95_db_latency": round(rng.uniform(2.5, 6.5), 2),
            "active_queries": rng.randint(35, 90),
            "slow_query_count": rng.randint(15, 60),
            "db_cpu": round(rng.uniform(65.0, 95.0), 1),
        },
    },
    "high_latency": {
        "type": "HIGH_LATENCY",
        "services": ["api-gateway", "backend", "frontend"],
        "descriptions": [
            "p99 HTTP latency exceeded SLA threshold (4800ms > 500ms) on checkout routes",
            "Ingress edge proxy queuing latency spiked due to keep-alive pool depletion",
            "API gateway response times degraded by upstream DNS resolution jitter",
            "Frontend asset delivery latency increased due to CDN origin cache bypass",
        ],
        "root_causes": [
            "Upstream socket backlog buffer overrun during unexpected traffic burst",
            "DNS lookup cache expired under sustained request volume",
            "Thread pool exhaustion in edge proxy routing layer",
        ],
        "actions": ["scale_replicas", "clear_cache", "restart_service"],
        "metrics_factory": lambda rng: {
            "p95_request_latency": round(rng.uniform(2.8, 7.5), 2),
            "p99_request_latency": round(rng.uniform(5.0, 11.0), 2),
            "request_rate": round(rng.uniform(250.0, 850.0), 1),
            "queue_depth": rng.randint(120, 600),
        },
    },
    "high_error_rate": {
        "type": "HIGH_ERROR_RATE",
        "services": ["backend", "api-gateway", "auth-service", "payment-service"],
        "descriptions": [
            "5xx HTTP error rate spiked to 18.4% on authenticated user routes",
            "OAuth token validation failure cascade returning 500 Internal Server Error",
            "Payment verification handler throwing unhandled NullPointer exceptions",
            "Gateway upstream proxy returning 502 Bad Gateway to incoming requests",
        ],
        "root_causes": [
            "Stale JWT signing key rotated without zero-downtime cache invalidation",
            "Unchecked JSON parsing exception on malformed third-party webhook payload",
            "Downstream microservice connection pool timeout under load",
        ],
        "actions": ["rollback_deployment", "restart_service", "circuit_breaker"],
        "metrics_factory": lambda rng: {
            "error_rate": round(rng.uniform(0.12, 0.42), 3),
            "http_5xx_ratio": round(rng.uniform(0.10, 0.38), 3),
            "request_rate": round(rng.uniform(300.0, 950.0), 1),
            "upstream_timeouts": rng.randint(40, 220),
        },
    },
    "service_unavailable": {
        "type": "SERVICE_UNAVAILABLE",
        "services": ["backend", "database", "cache", "auth-service"],
        "descriptions": [
            "Health probe failures on 3 consecutive polls; port unresponsive",
            "Service process terminated unexpectedly after fatal unhandled exception",
            "Container failed liveness probe and entered CrashLoopBackOff",
            "Database daemon refused new TCP connections due to socket exhaustion",
        ],
        "root_causes": [
            "Out of memory killer (OOM) terminated service worker process",
            "Deadlock in worker thread supervisor causing health server hang",
            "Corrupt configuration parameter injected during automated deployment",
        ],
        "actions": ["restart_service", "rollback_deployment", "failover_replica"],
        "metrics_factory": lambda rng: {
            "backend_up": 0.0,
            "consecutive_health_failures": rng.randint(3, 8),
            "http_connect_errors": rng.randint(80, 450),
            "uptime_seconds": 0,
        },
    },
    "cpu_saturation": {
        "type": "CPU_SATURATION",
        "services": ["backend", "database", "payment-service"],
        "descriptions": [
            "CPU utilization reached 98.2% across worker pool; runaway thread loop",
            "Cryptographic signature verification saturating all available CPU cores",
            "High CPU load causing request processing delays and starvation",
            "Regex catastrophic backtracking in input sanitizer pinning worker CPUs",
        ],
        "root_causes": [
            "Exponential backtracking regular expression in payment input validator",
            "Tight busy-wait spinloop inside asynchronous retry backoff handler",
            "Unbounded recursion during hierarchical graph serialization",
        ],
        "actions": ["scale_replicas", "restart_service", "circuit_breaker"],
        "metrics_factory": lambda rng: {
            "cpu_utilization_pct": round(rng.uniform(92.0, 99.8), 1),
            "load_average_1m": round(rng.uniform(8.5, 24.0), 1),
            "worker_thread_pool_active": rng.randint(48, 64),
        },
    },
    "memory_pressure": {
        "type": "MEMORY_PRESSURE",
        "services": ["backend", "cache", "monitoring"],
        "descriptions": [
            "Container heap usage reached 94% limit; garbage collector thrashing",
            "Redis cache instance memory exceeded maxmemory eviction policy threshold",
            "Prometheus TSDB block compaction experiencing memory spikes",
            "In-memory session cache leaking uncollected references over time",
        ],
        "root_causes": [
            "Unbounded cache collection retention without proper TTL expiry policy",
            "WebSocket session objects not deregistered upon abnormal client disconnects",
            "Excessive telemetry metric label cardinality inflating heap storage",
        ],
        "actions": ["clear_cache", "restart_service", "scale_replicas"],
        "metrics_factory": lambda rng: {
            "memory_usage_pct": round(rng.uniform(88.0, 97.5), 1),
            "heap_used_mb": rng.randint(3200, 7800),
            "gc_pause_seconds": round(rng.uniform(0.8, 3.2), 2),
        },
    },
    "connection_exhaustion": {
        "type": "CONNECTION_EXHAUSTION",
        "services": ["database", "cache", "backend"],
        "descriptions": [
            "PostgreSQL connection pool exhausted (98/100 connections active)",
            "Redis client connection limit reached; new commands rejected with ERR max clients",
            "Outbound HTTP connection pool leak causing socket starvation",
            "DB connection leak in background report generator holding open transactions",
        ],
        "root_causes": [
            "Database connections not released in exception handling branch of report job",
            "Client connection timeout configured higher than server idle connection drop",
            "Sudden spike in long-running read queries holding connections open",
        ],
        "actions": ["pool_expansion", "kill_slow_queries", "restart_service"],
        "metrics_factory": lambda rng: {
            "db_active_connections": rng.randint(92, 100),
            "max_connections": 100,
            "connection_wait_time_ms": rng.randint(2500, 8500),
            "pool_utilization_pct": round(rng.uniform(92.0, 100.0), 1),
        },
    },
    "dependency_failure": {
        "type": "DEPENDENCY_FAILURE",
        "services": ["payment-service", "auth-service", "api-gateway"],
        "descriptions": [
            "External payment gateway webhook endpoint returning 502 Bad Gateway",
            "Third-party SMS OTP verification service experiencing outage",
            "Downstream tax calculation service DNS resolution failing intermittently",
            "Cloud KMS key encryption service returning 503 Service Unavailable",
        ],
        "root_causes": [
            "Third-party payment provider experiencing regional gateway degradation",
            "Outbound TLS handshake failure due to expired external intermediary certificate",
            "Rate limiter tripped on external vendor quota tier",
        ],
        "actions": ["circuit_breaker", "rollback_deployment", "restart_service"],
        "metrics_factory": lambda rng: {
            "external_api_latency": round(rng.uniform(5.5, 12.0), 2),
            "external_api_error_rate": round(rng.uniform(0.65, 0.95), 2),
            "circuit_breaker_tripped": True,
            "fallback_invocations": rng.randint(80, 350),
        },
    },
}

SEVERITIES = ["low", "medium", "high", "critical"]
SEVERITY_WEIGHTS = [0.15, 0.40, 0.30, 0.15]

# Lifecycle distribution:
# 72% resolved, 10% open, 4% diagnosing, 4% scoring, 3% sandbox, 3% remediating, 4% failed
STATUS_WEIGHTS = [
    ("resolved", 0.72),
    ("open", 0.10),
    ("diagnosing", 0.04),
    ("scoring", 0.04),
    ("sandbox", 0.03),
    ("remediating", 0.03),
    ("failed", 0.04),
]


def pick_weighted(rng: random.Random, items: List[Tuple[Any, float]]) -> Any:
    total = sum(w for _, w in items)
    r = rng.uniform(0, total)
    upto = 0.0
    for item, weight in items:
        if upto + weight >= r:
            return item
        upto += weight
    return items[-1][0]


async def seed_demo_incidents(
    count: int = 120,
    seed: int = 42,
    clean_demo: bool = False,
    days: int = 30,
) -> Dict[str, Any]:
    """
    Generate and insert realistic demo incidents idempotently into safeopsdb.
    """
    rng = random.Random(seed)
    now = datetime.now(timezone.utc)
    logger.info("Initializing SafeOpsAI Demo Seeder (Count=%d, Seed=%d, Days=%d)...", count, seed, days)

    conn = await asyncpg.connect(
        host=DB_HOST,
        port=DB_PORT,
        database=DB_NAME,
        user=DB_USER,
        password=DB_PASS,
    )

    try:
        # 1. Clean demo records if explicitly requested
        if clean_demo:
            logger.warning("Cleaning existing demo records (WHERE is_demo = TRUE)...")
            async with conn.transaction():
                del_acts = await conn.execute("DELETE FROM remediation_actions WHERE is_demo = TRUE")
                del_decs = await conn.execute("DELETE FROM agent_decisions WHERE is_demo = TRUE")
                del_incs = await conn.execute("DELETE FROM incidents WHERE is_demo = TRUE OR fingerprint LIKE 'DEMO:%'")
            logger.info("Demo cleanup complete: %s, %s, %s", del_incs, del_decs, del_acts)

        # 2. Fetch existing demo fingerprints to ensure idempotency
        existing_rows = await conn.fetch("SELECT id, fingerprint FROM incidents WHERE fingerprint LIKE 'DEMO:%'")
        existing_map = {r["fingerprint"]: r["id"] for r in existing_rows}
        logger.info("Found %d pre-existing demo incidents in database.", len(existing_map))

        fault_keys = list(INCIDENT_SPECS.keys())
        created_count = 0
        updated_count = 0

        # Stats accumulators
        status_counts: Dict[str, int] = {}
        severity_counts: Dict[str, int] = {}
        service_counts: Dict[str, int] = {}

        # 3. Generate deterministic sample records
        for idx in range(1, count + 1):
            # Select attributes deterministically
            fault_key = fault_keys[(idx - 1) % len(fault_keys)]
            spec = INCIDENT_SPECS[fault_key]
            service = rng.choice(spec["services"])
            incident_type = spec["type"]
            severity = pick_weighted(rng, list(zip(SEVERITIES, SEVERITY_WEIGHTS)))
            status_lifecycle = pick_weighted(rng, STATUS_WEIGHTS)

            status_counts[status_lifecycle] = status_counts.get(status_lifecycle, 0) + 1
            severity_counts[severity] = severity_counts.get(severity, 0) + 1
            service_counts[service] = service_counts.get(service, 0) + 1

            # Time distribution across previous N days
            # Ensure chronological order with small jitter
            days_ago = days * (1.0 - (idx / float(count))) + rng.uniform(-0.3, 0.3)
            days_ago = max(0.01, min(float(days), days_ago))
            detected_at = now - timedelta(days=days_ago)

            fingerprint = f"DEMO:INC-{idx:04d}:{service}:{fault_key}"
            desc = rng.choice(spec["descriptions"])
            metrics_snap = spec["metrics_factory"](rng)

            # Stage progression timestamps
            diagnosing_at = None
            scoring_at = None
            coordinated_at = None
            sandbox_at = None
            remediating_at = None
            resolved_at = None
            mttr_seconds = None

            # Determine lifecycle progression based on status
            is_resolved = status_lifecycle == "resolved"
            is_failed = status_lifecycle == "failed"

            if status_lifecycle in ("diagnosing", "scoring", "sandbox", "remediating", "resolved", "failed"):
                diagnosing_at = detected_at + timedelta(seconds=rng.randint(3, 10))
            if status_lifecycle in ("scoring", "sandbox", "remediating", "resolved", "failed"):
                scoring_at = diagnosing_at + timedelta(seconds=rng.randint(2, 6))
            if status_lifecycle in ("sandbox", "remediating", "resolved", "failed"):
                coordinated_at = scoring_at + timedelta(seconds=rng.randint(1, 4))
            if status_lifecycle in ("sandbox", "remediating", "resolved", "failed"):
                sandbox_at = coordinated_at + timedelta(seconds=rng.randint(3, 10))
            if status_lifecycle in ("remediating", "resolved", "failed"):
                remediating_at = sandbox_at + timedelta(seconds=rng.randint(4, 12))
            if is_resolved:
                resolved_at = remediating_at + timedelta(seconds=rng.randint(15, 65))
                mttr_seconds = int((resolved_at - detected_at).total_seconds())
            elif is_failed:
                resolved_at = remediating_at + timedelta(seconds=rng.randint(20, 50))
                mttr_seconds = int((resolved_at - detected_at).total_seconds())

            # DB status field: open, remediating, resolved, or failed
            db_status = "resolved" if is_resolved else ("failed" if is_failed else ("remediating" if status_lifecycle == "remediating" else "open"))

            incident_id: Optional[int] = existing_map.get(fingerprint)

            async with conn.transaction():
                if incident_id is None:
                    # Insert new incident
                    row = await conn.fetchrow(
                        """
                        INSERT INTO incidents (
                            service, fault_type, severity, status, description,
                            detected_at, resolved_at, mttr_seconds, fingerprint,
                            incident_type, detection_source, metrics_snapshot,
                            diagnosing_at, scoring_at, coordinated_at, sandbox_at,
                            remediating_at, is_demo
                        ) VALUES (
                            $1, $2, $3, $4, $5,
                            $6, $7, $8, $9,
                            $10, 'demo_seed', $11::jsonb,
                            $12, $13, $14, $15,
                            $16, TRUE
                        )
                        ON CONFLICT (fingerprint) WHERE status NOT IN ('resolved', 'rolled_back')
                        DO UPDATE SET description = EXCLUDED.description
                        RETURNING id
                        """,
                        service,
                        fault_key,
                        severity,
                        db_status,
                        desc,
                        detected_at,
                        resolved_at,
                        mttr_seconds,
                        fingerprint,
                        incident_type,
                        json.dumps(metrics_snap),
                        diagnosing_at,
                        scoring_at,
                        coordinated_at,
                        sandbox_at,
                        remediating_at,
                    )
                    if row:
                        incident_id = row["id"]
                        created_count += 1
                    else:
                        # Existing active row conflict
                        row = await conn.fetchrow("SELECT id FROM incidents WHERE fingerprint = $1", fingerprint)
                        incident_id = row["id"] if row else None
                        updated_count += 1
                else:
                    # Update existing record
                    await conn.execute(
                        """
                        UPDATE incidents SET
                            service = $1, fault_type = $2, severity = $3, status = $4,
                            description = $5, detected_at = $6, resolved_at = $7,
                            mttr_seconds = $8, incident_type = $9, metrics_snapshot = $10::jsonb,
                            diagnosing_at = $11, scoring_at = $12, coordinated_at = $13,
                            sandbox_at = $14, remediating_at = $15, is_demo = TRUE,
                            detection_source = 'demo_seed'
                        WHERE id = $16
                        """,
                        service,
                        fault_key,
                        severity,
                        db_status,
                        desc,
                        detected_at,
                        resolved_at,
                        mttr_seconds,
                        incident_type,
                        json.dumps(metrics_snap),
                        diagnosing_at,
                        scoring_at,
                        coordinated_at,
                        sandbox_at,
                        remediating_at,
                        incident_id,
                    )
                    updated_count += 1

                if incident_id is None:
                    continue

                # 4. Agent Decisions & Remediation Actions (Idempotent: wipe demo rows for this incident first)
                await conn.execute("DELETE FROM agent_decisions WHERE incident_id = $1 AND is_demo = TRUE", incident_id)
                await conn.execute("DELETE FROM remediation_actions WHERE incident_id = $1 AND is_demo = TRUE", incident_id)

                chosen_action = rng.choice(spec["actions"])
                alt_action_1 = spec["actions"][(spec["actions"].index(chosen_action) + 1) % len(spec["actions"])]
                alt_action_2 = spec["actions"][(spec["actions"].index(chosen_action) + 2) % len(spec["actions"])]
                candidates = [chosen_action, alt_action_1, alt_action_2]

                # RCA Decision
                if diagnosing_at:
                    root_cause_explanation = rng.choice(spec["root_causes"])
                    rca_payload = {
                        "diagnosis": {
                            "incident_type": incident_type,
                            "affected_service": service,
                            "root_cause": root_cause_explanation,
                            "confidence": round(rng.uniform(0.85, 0.98), 2),
                            "recommended_action": chosen_action,
                        },
                        "model": "ollama/llama3.2:1b (simulated demo)",
                        "analysis_duration_ms": rng.randint(950, 1850),
                    }
                    await conn.execute(
                        """
                        INSERT INTO agent_decisions (
                            incident_id, agent_name, score, reasoning, raw_output,
                            root_cause_output, execution_time_ms, llm_model, created_at, is_demo
                        ) VALUES ($1, 'root_cause', $2, $3, $4::jsonb, $5::jsonb, $6, $7, $8, TRUE)
                        """,
                        incident_id,
                        round(rng.uniform(8.0, 9.8), 2),
                        f"RCA Diagnosis: {root_cause_explanation}",
                        json.dumps(rca_payload),
                        json.dumps(rca_payload["diagnosis"]),
                        rca_payload["analysis_duration_ms"],
                        "ollama/llama3.2:1b (simulated)",
                        diagnosing_at,
                    )

                # Evaluator Decisions (Cost, Reliability, Security)
                if scoring_at:
                    for cand_act in candidates:
                        # Cost scoring
                        cost_score = round(rng.uniform(6.5, 9.5) if cand_act == chosen_action else rng.uniform(4.0, 7.5), 2)
                        await conn.execute(
                            """
                            INSERT INTO agent_decisions (incident_id, agent_name, score, reasoning, raw_output, created_at, is_demo)
                            VALUES ($1, 'cost', $2, $3, $4::jsonb, $5, TRUE)
                            """,
                            incident_id,
                            cost_score,
                            f"Evaluated financial and resource cost for '{cand_act}' on {service}",
                            json.dumps({"candidate": {"action": cand_act, "target": service, "priority": 1 if cand_act == chosen_action else 2}}),
                            scoring_at,
                        )

                        # Reliability scoring
                        rel_score = round(rng.uniform(7.8, 9.8) if cand_act == chosen_action else rng.uniform(5.0, 7.2), 2)
                        await conn.execute(
                            """
                            INSERT INTO agent_decisions (incident_id, agent_name, score, reasoning, raw_output, created_at, is_demo)
                            VALUES ($1, 'reliability', $2, $3, $4::jsonb, $5, TRUE)
                            """,
                            incident_id,
                            rel_score,
                            f"Assessed MTTR and expected service availability for '{cand_act}' on {service}",
                            json.dumps({"candidate": {"action": cand_act, "target": service, "priority": 1 if cand_act == chosen_action else 2}}),
                            scoring_at,
                        )

                        # Security scoring
                        sec_score = round(rng.uniform(8.2, 9.9) if cand_act == chosen_action else rng.uniform(6.0, 8.5), 2)
                        await conn.execute(
                            """
                            INSERT INTO agent_decisions (incident_id, agent_name, score, reasoning, raw_output, created_at, is_demo)
                            VALUES ($1, 'security', $2, $3, $4::jsonb, $5, TRUE)
                            """,
                            incident_id,
                            sec_score,
                            f"Verified blast-radius and least-privilege boundary for '{cand_act}' on {service}",
                            json.dumps({"candidate": {"action": cand_act, "target": service, "priority": 1 if cand_act == chosen_action else 2}}),
                            scoring_at,
                        )

                # Coordinator Decision (MCDM / TOPSIS)
                if coordinated_at:
                    mcdm_ranking = [
                        {
                            "rank": 1,
                            "action": chosen_action,
                            "target": service,
                            "priority": 1,
                            "metric": round(rng.uniform(0.85, 0.94), 4),
                            "scores": {"cost": 8.5, "reliability": 9.2, "security": 9.5},
                        },
                        {
                            "rank": 2,
                            "action": alt_action_1,
                            "target": service,
                            "priority": 2,
                            "metric": round(rng.uniform(0.60, 0.75), 4),
                            "scores": {"cost": 6.8, "reliability": 7.0, "security": 7.5},
                        },
                        {
                            "rank": 3,
                            "action": alt_action_2,
                            "target": service,
                            "priority": 3,
                            "metric": round(rng.uniform(0.45, 0.58), 4),
                            "scores": {"cost": 5.2, "reliability": 6.1, "security": 6.8},
                        },
                    ]
                    coord_payload = {
                        "winner": {"action": chosen_action, "target": service},
                        "method": "topsis",
                        "weights_used": {"cost": 0.30, "reliability": 0.50, "security": 0.20},
                        "ranking": mcdm_ranking,
                    }
                    await conn.execute(
                        """
                        INSERT INTO agent_decisions (
                            incident_id, agent_name, score, reasoning, raw_output, created_at, is_demo
                        ) VALUES ($1, 'coordinator', $2, $3, $4::jsonb, $5, TRUE)
                        """,
                        incident_id,
                        round(mcdm_ranking[0]["metric"] * 10, 2),
                        f"MCDM TOPSIS selected '{chosen_action}' as highest-ranking candidate (C={mcdm_ranking[0]['metric']})",
                        json.dumps(coord_payload),
                        coordinated_at,
                    )

                # Remediation Action (Sandbox Validation + Execution)
                if sandbox_at:
                    sb_passed = not is_failed
                    validation_score = round(rng.uniform(0.88, 0.98), 2) if sb_passed else round(rng.uniform(0.20, 0.45), 2)
                    action_state = "SUCCESS" if is_resolved else ("FAILED" if is_failed else ("EXECUTING" if status_lifecycle == "remediating" else "PENDING"))
                    outcome = "success" if is_resolved else ("failed" if is_failed else None)
                    rollback_performed = is_failed

                    checks_data = {
                        "liveness_probe": {"passed": True, "latency_ms": rng.randint(4, 18)},
                        "db_ping": {"passed": True, "latency_ms": rng.randint(2, 9)},
                        "error_rate_check": {"passed": sb_passed, "error_rate": 0.01 if sb_passed else 0.38},
                        "side_effect_monitor": {"passed": sb_passed, "risk_score": 0.04 if sb_passed else 0.82},
                    }

                    await conn.execute(
                        """
                        INSERT INTO remediation_actions (
                            incident_id, action_type, target_service, final_score,
                            sandbox_passed, executed_at, outcome, notes, candidate_rank,
                            validation_score, sandbox_started_at, sandbox_ended_at,
                            checks, selection_status, execution_authorized, rollback_available,
                            state, recovery_score, rollback_performed, rollback_reason, is_demo
                        ) VALUES (
                            $1, $2, $3, $4,
                            $5, $6, $7, $8, 1,
                            $9, $10, $11,
                            $12::jsonb, $13, $14, TRUE,
                            $15, $16, $17, $18, TRUE
                        )
                        """,
                        incident_id,
                        chosen_action,
                        service,
                        round(rng.uniform(8.0, 9.5), 2),
                        sb_passed,
                        remediating_at,
                        outcome,
                        f"Simulated execution for demo incident INC-{idx}",
                        validation_score,
                        sandbox_at,
                        sandbox_at + timedelta(seconds=rng.randint(2, 8)),
                        json.dumps(checks_data),
                        "selected" if sb_passed else "rejected",
                        sb_passed,
                        action_state,
                        round(rng.uniform(0.92, 1.0), 2) if is_resolved else (0.15 if is_failed else None),
                        rollback_performed,
                        "Health probe degraded following remediation execution; safety rollback engaged." if rollback_performed else None,
                    )

        total_in_db = await conn.fetchval("SELECT COUNT(*) FROM incidents")
        demo_in_db = await conn.fetchval("SELECT COUNT(*) FROM incidents WHERE is_demo = TRUE")
        real_in_db = await conn.fetchval("SELECT COUNT(*) FROM incidents WHERE is_demo = FALSE")

        summary = {
            "requested_count": count,
            "created_records": created_count,
            "updated_records": updated_count,
            "total_incidents_in_db": total_in_db,
            "demo_incidents": demo_in_db,
            "real_incidents": real_in_db,
            "status_distribution": status_counts,
            "severity_distribution": severity_counts,
            "service_distribution": service_counts,
        }

        logger.info(
            "Demo Seeding Summary: Total in DB: %d (Demo: %d, Real: %d). Created: %d, Updated: %d.",
            total_in_db, demo_in_db, real_in_db, created_count, updated_count
        )
        return summary

    finally:
        await conn.close()


def main():
    parser = argparse.ArgumentParser(description="SafeOpsAI Realistic Demo Incident Seeder")
    parser.add_argument("--count", type=int, default=120, help="Number of demo incidents to generate (default: 120)")
    parser.add_argument("--seed", type=int, default=42, help="Deterministic random seed (default: 42)")
    parser.add_argument("--days", type=int, default=30, help="Historical time window in days (default: 30)")
    parser.add_argument("--clean-demo", action="store_true", help="Delete prior demo incidents before re-seeding (NEVER deletes real incidents)")

    args = parser.parse_args()
    summary = asyncio.run(seed_demo_incidents(
        count=args.count,
        seed=args.seed,
        clean_demo=args.clean_demo,
        days=args.days,
    ))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
