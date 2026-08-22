"""
SafeOpsAI Evaluation — Experiment Controller & Scenario Runner
================================================================
Orchestrates experiment execution cycles:
Reset Environment -> Inject Fault -> Detect Incident -> Execute Strategy -> Monitor Recovery -> Record Metrics -> Reset Environment.
"""

import asyncio
import datetime
import random
import time
import uuid
import logging
import httpx
from typing import Any, Dict, List, Optional, Tuple

from .config import EvaluationConfig
from .safety import check_safety_guard
from .scenarios import FaultScenario, get_scenario
from .strategies import get_strategy
from .metrics import ExperimentRunRecord, calculate_latencies
from .db import EvaluationDB, export_runs_to_csv

log = logging.getLogger("safeopsai.evaluation.runner")


class ExperimentController:
    """Orchestrates reproducible evaluation campaigns."""

    def __init__(self, config: EvaluationConfig, mock_mode: bool = False):
        self.config = config
        self.mock_mode = mock_mode
        self.db = EvaluationDB(
            host=config.db_host,
            port=config.db_port,
            db=config.db_name,
            user=config.db_user,
            password=config.db_pass,
        )
        self.records: List[ExperimentRunRecord] = []

    async def reset_environment(self) -> bool:
        """
        Environment Reset Protocol:
        1. Stop active remediation
        2. Clear injected faults via /admin/fault/reset
        3. Verify services healthy
        4. Wait for stabilization
        """
        log.info("Resetting environment for next experiment run...")
        if self.mock_mode:
            await asyncio.sleep(self.config.cooldown_seconds)
            return True

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                # 1. Reset backend fault flags
                res = await client.post(f"{self.config.backend_url}/admin/fault/reset")
                if res.status_code != 200:
                    log.warning("Reset endpoint returned status %d", res.status_code)

                # 2. Verify backend readiness
                for attempt in range(5):
                    try:
                        r = await client.get(f"{self.config.backend_url}/health")
                        if r.status_code == 200:
                            break
                    except Exception:
                        pass
                    await asyncio.sleep(1.0)

            await asyncio.sleep(self.config.cooldown_seconds)
            return True
        except Exception as exc:
            log.error("Environment reset failed: %s", exc)
            return False

    async def inject_fault(self, scenario: FaultScenario) -> str:
        """Injects specified fault into target environment."""
        log.info("Injecting fault scenario %s (%s)...", scenario.scenario_id, scenario.fault_type)
        t_injected = datetime.datetime.now(datetime.timezone.utc).isoformat()

        if self.mock_mode:
            await asyncio.sleep(0.1)
            return t_injected

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                body = {}
                if scenario.fault_type == "slow_queries":
                    body = {"slow_queries": True}
                elif scenario.fault_type == "high_error_rate":
                    body = {"high_error_rate": True}
                elif scenario.fault_type == "db_unavailable":
                    body = {"db_unavailable": True}

                if body:
                    await client.post(f"{self.config.backend_url}/admin/fault", json=body)
                else:
                    log.info("Scenario %s uses container-level fault simulation", scenario.scenario_id)
            return t_injected
        except Exception as exc:
            log.error("Fault injection failed: %s", exc)
            return t_injected

    async def run_single_trial(
        self,
        experiment_id: str,
        scenario: FaultScenario,
        strategy_name: str,
        repetition: int,
        is_warmup: bool = False,
        random_seed: Optional[int] = None,
    ) -> ExperimentRunRecord:
        """Executes a single experiment trial with complete metric logging."""
        run_id = f"RUN-{uuid.uuid4().hex[:8].upper()}"
        started_at = datetime.datetime.now(datetime.timezone.utc).isoformat()

        log.info(
            "Executing Trial [%s] Scenario=%s Strategy=%s Repetition=%d Warmup=%s",
            run_id, scenario.scenario_id, strategy_name, repetition, is_warmup
        )

        try:
            # 1. Reset Environment
            await self.reset_environment()

            # 2. Inject Fault
            t_fault = await self.inject_fault(scenario)

            # 3. Incident Detection
            await asyncio.sleep(0.2 if self.mock_mode else 1.5)
            t_detected = datetime.datetime.now(datetime.timezone.utc).isoformat()

            # 4. Strategy Execution
            strat = get_strategy(strategy_name)
            res = await strat.execute(
                incident_id=random.randint(100, 999),
                scenario_id=scenario.scenario_id,
                target_service=scenario.target_service,
                fault_type=scenario.fault_type,
                mock_mode=self.mock_mode,
            )

            # 5. Reset environment post-trial
            await self.reset_environment()

            # 6. Compute Latencies
            lats = calculate_latencies(
                fault_injected_at=t_fault,
                incident_detected_at=t_detected,
                decision_at=res.decision_at,
                remediation_started_at=res.remediation_started_at,
                recovered_at=res.recovered_at,
                timeout_seconds=scenario.timeout_seconds,
            )

            record = ExperimentRunRecord(
                experiment_run_id=run_id,
                experiment_id=experiment_id,
                scenario_id=scenario.scenario_id,
                strategy=strategy_name,
                repetition=repetition,
                is_warmup=is_warmup,
                status="COMPLETED" if res.success else "FAILED",
                started_at=started_at,
                fault_injected_at=t_fault,
                incident_detected_at=t_detected,
                decision_at=res.decision_at,
                remediation_started_at=res.remediation_started_at,
                recovered_at=res.recovered_at,
                mttr_seconds=lats["mttr_seconds"],
                downtime_seconds=lats["downtime_seconds"],
                detection_latency_seconds=lats["detection_latency_seconds"],
                decision_latency_seconds=lats["decision_latency_seconds"],
                remediation_latency_seconds=lats["remediation_latency_seconds"],
                rollback=res.rollback_performed,
                success=res.success,
                escalated=res.escalated,
                selected_remediation=res.selected_action,
                top_ranked_candidate=res.top_ranked_candidate,
                sandbox_pass=res.sandbox_pass,
                candidate_fallback_count=res.candidate_fallback_count,
                remediation_attempts=res.remediation_attempts,
                recovery_score=res.recovery_score,
                final_outcome=res.final_outcome,
                error_message="",
                raw_logs={"notes": res.notes},
                system_config=self.config.to_dict(),
                random_seed=random_seed,
            )

        except Exception as exc:
            log.error("Trial %s CRASHED: %s", run_id, exc, exc_info=True)
            # Reset environment after failure
            await self.reset_environment()
            record = ExperimentRunRecord(
                experiment_run_id=run_id,
                experiment_id=experiment_id,
                scenario_id=scenario.scenario_id,
                strategy=strategy_name,
                repetition=repetition,
                is_warmup=is_warmup,
                status="ERROR",
                started_at=started_at,
                fault_injected_at=started_at,
                mttr_seconds=scenario.timeout_seconds,
                downtime_seconds=scenario.timeout_seconds,
                success=False,
                escalated=True,
                final_outcome="ERROR",
                error_message=str(exc),
            )

        self.records.append(record)
        await self.db.save_run_record(record)
        return record

    async def run_campaign(
        self,
        experiment_id: Optional[str] = None,
        selected_scenarios: Optional[List[str]] = None,
        selected_strategies: Optional[List[str]] = None,
        repetitions: Optional[int] = None,
    ) -> List[ExperimentRunRecord]:
        """Runs complete evaluation campaign across scenarios and strategies."""
        check_safety_guard(self.config.env_mode)
        exp_id = experiment_id or f"EXP-{uuid.uuid4().hex[:6].upper()}"

        target_scenarios = [get_scenario(s) for s in (selected_scenarios or self.config.scenarios)]
        target_strategies = selected_strategies or self.config.strategies
        reps = repetitions if repetitions is not None else self.config.repetitions
        warmups = self.config.warmup_runs

        await self.db.connect()
        await self.db.save_experiment(exp_id, self.config.experiment_name, self.config.to_dict())

        # Construct execution tasks
        trials: List[Tuple[FaultScenario, str, int, bool]] = []

        # 1. Warm-up trials
        for sc in target_scenarios:
            for st in target_strategies:
                for w in range(1, warmups + 1):
                    trials.append((sc, st, w, True))

        # 2. Measured trials
        measured_trials: List[Tuple[FaultScenario, str, int, bool]] = []
        for sc in target_scenarios:
            for st in target_strategies:
                for r in range(1, reps + 1):
                    measured_trials.append((sc, st, r, False))

        if self.config.randomize_order:
            random.seed(self.config.random_seed)
            random.shuffle(measured_trials)

        all_trials = trials + measured_trials
        log.info(
            "Starting Campaign %s: %d total trials (%d warmup, %d measured)",
            exp_id, len(all_trials), len(trials), len(measured_trials)
        )

        for sc, st, rep, is_warm in all_trials:
            await self.run_single_trial(
                experiment_id=exp_id,
                scenario=sc,
                strategy_name=st,
                repetition=rep,
                is_warmup=is_warm,
                random_seed=self.config.random_seed,
            )

        export_runs_to_csv(self.records)
        await self.db.close()
        log.info("Campaign %s completed successfully.", exp_id)
        return self.records
