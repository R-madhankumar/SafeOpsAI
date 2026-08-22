"""
SafeOpsAI Evaluation — Database Persistence & CSV Exporter
===========================================================
Saves experiment campaigns, individual run records, and raw metric observations
to PostgreSQL and exports raw CSV data files.
"""

import csv
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
import asyncpg

from .metrics import ExperimentRunRecord

log = logging.getLogger("safeopsai.evaluation.db")

RESULTS_DIR = Path(__file__).parent.parent.parent / "results"
RAW_DIR = RESULTS_DIR / "raw"
PROCESSED_DIR = RESULTS_DIR / "processed"


def ensure_results_directories() -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    (RESULTS_DIR / "figures").mkdir(parents=True, exist_ok=True)
    (RESULTS_DIR / "reports").mkdir(parents=True, exist_ok=True)


class EvaluationDB:
    def __init__(self, host="localhost", port=5433, db="safeopsdb", user="safeops", password="safeops123"):
        self.host = host
        self.port = port
        self.db = db
        self.user = user
        self.password = password
        self.pool: Optional[asyncpg.Pool] = None

    async def connect(self) -> None:
        try:
            self.pool = await asyncpg.create_pool(
                host=self.host,
                port=self.port,
                database=self.db,
                user=self.user,
                password=self.password,
                min_size=1,
                max_size=5,
            )
            # Apply migrate_step9.sql if migration script exists
            mig_path = Path(__file__).parent.parent / "database" / "migrate_step9.sql"
            if mig_path.exists():
                sql = mig_path.read_text(encoding="utf-8")
                async with self.pool.acquire() as conn:
                    await conn.execute(sql)
            log.info("EvaluationDB pool established and schema verified.")
        except Exception as exc:
            log.warning("EvaluationDB connection skipped (will store locally): %s", exc)

    async def close(self) -> None:
        if self.pool:
            await self.pool.close()

    async def save_experiment(self, exp_id: str, name: str, config: Dict[str, Any]) -> None:
        if not self.pool:
            return
        try:
            async with self.pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO experiments (id, name, config)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, config = EXCLUDED.config
                    """,
                    exp_id, name, json.dumps(config)
                )
        except Exception as exc:
            log.error("Failed to save experiment %s: %s", exp_id, exc)

    async def save_run_record(self, record: ExperimentRunRecord) -> None:
        if not self.pool:
            return
        try:
            async with self.pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO experiment_runs (
                        experiment_run_id, experiment_id, scenario_id, strategy, repetition, is_warmup, status,
                        started_at, fault_injected_at, incident_detected_at, decision_at, remediation_started_at, recovered_at,
                        mttr_seconds, downtime_seconds, detection_latency_seconds, decision_latency_seconds, remediation_latency_seconds,
                        rollback, success, escalated, selected_remediation, top_ranked_candidate, sandbox_pass,
                        candidate_fallback_count, remediation_attempts, recovery_score, final_outcome, error_message,
                        raw_logs, system_config, random_seed
                    ) VALUES (
                        $1, $2, $3, $4, $5, $6, $7,
                        $8, $9, $10, $11, $12, $13,
                        $14, $15, $16, $17, $18,
                        $19, $20, $21, $22, $23, $24,
                        $25, $26, $27, $28, $29,
                        $30, $31, $32
                    ) ON CONFLICT (experiment_run_id) DO UPDATE SET status = EXCLUDED.status, mttr_seconds = EXCLUDED.mttr_seconds
                    """,
                    record.experiment_run_id, record.experiment_id, record.scenario_id, record.strategy, record.repetition, record.is_warmup, record.status,
                    record.started_at, record.fault_injected_at, record.incident_detected_at, record.decision_at, record.remediation_started_at, record.recovered_at,
                    record.mttr_seconds, record.downtime_seconds, record.detection_latency_seconds, record.decision_latency_seconds, record.remediation_latency_seconds,
                    record.rollback, record.success, record.escalated, record.selected_remediation, record.top_ranked_candidate, record.sandbox_pass,
                    record.candidate_fallback_count, record.remediation_attempts, record.recovery_score, record.final_outcome, record.error_message,
                    json.dumps(record.raw_logs), json.dumps(record.system_config), record.random_seed
                )
        except Exception as exc:
            log.error("Failed to save run record %s: %s", record.experiment_run_id, exc)


def export_runs_to_csv(records: List[ExperimentRunRecord], output_path: Optional[Path] = None) -> Path:
    ensure_results_directories()
    target = output_path or (RAW_DIR / "experiment_runs.csv")

    fieldnames = [
        "experiment_run_id", "experiment_id", "scenario_id", "strategy", "repetition", "is_warmup", "status",
        "started_at", "fault_injected_at", "incident_detected_at", "decision_at", "remediation_started_at", "recovered_at",
        "mttr_seconds", "downtime_seconds", "detection_latency_seconds", "decision_latency_seconds", "remediation_latency_seconds",
        "rollback", "success", "escalated", "selected_remediation", "top_ranked_candidate", "sandbox_pass",
        "candidate_fallback_count", "remediation_attempts", "recovery_score", "final_outcome"
    ]

    with open(target, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in records:
            d = r.to_dict()
            row = {k: d.get(k) for k in fieldnames}
            writer.writerow(row)

    log.info("Exported %d experiment run records to %s", len(records), target)
    return target
