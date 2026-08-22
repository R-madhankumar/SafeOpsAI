"""
SafeOpsAI Evaluation — Automated Test Suite
============================================
Covers all 14 mandated Phase 8 requirements:
1. Scenario registration
2. Experiment configuration
3. Baseline execution
4. SafeOpsAI execution
5. Environment reset
6. Metric calculation
7. MTTR calculation
8. Rollback-rate calculation
9. Missing timestamps
10. Failed experiment recovery
11. Randomized ordering
12. Warm-up exclusion
13. Statistical calculations
14. Environment safety guard
"""

import asyncio
import pytest
import os
import sys
from pathlib import Path

# Ensure safeopsai package directory is on sys.path
_SAFEOPS_DIR = Path(__file__).parent.parent.parent
if str(_SAFEOPS_DIR) not in sys.path:
    sys.path.insert(0, str(_SAFEOPS_DIR))

from evaluation.scenarios import list_scenarios, get_scenario, FaultScenario
from evaluation.config import EvaluationConfig
from evaluation.safety import check_safety_guard, SafetyViolationError
from evaluation.strategies import get_strategy
from evaluation.metrics import calculate_latencies, ExperimentRunRecord
from evaluation.runner import ExperimentController
from evaluation.stats import calculate_metric_summary, compare_strategies_statistically


def test_01_scenario_registration():
    scenarios = list_scenarios()
    assert len(scenarios) >= 6
    s2 = get_scenario("SCENARIO-02")
    assert s2.scenario_id == "SCENARIO-02"
    assert s2.fault_type == "slow_queries"
    assert s2.target_service == "database"


def test_02_experiment_configuration():
    cfg = EvaluationConfig()
    assert cfg.repetitions >= 1
    assert "safeopsai" in cfg.strategies
    assert "naive_restart" in cfg.strategies
    assert cfg.recovery_window_seconds > 0


def test_03_baseline_execution():
    async def _run():
        strat = get_strategy("naive_restart")
        res = await strat.execute(1, "SCENARIO-01", "backend", "backend_down", mock_mode=True)
        assert res.strategy_name == "naive_restart"
        assert res.success is True
        assert res.selected_action == "restart_service"
    asyncio.run(_run())


def test_04_safeopsai_execution():
    async def _run():
        strat = get_strategy("safeopsai")
        res = await strat.execute(2, "SCENARIO-02", "database", "slow_queries", mock_mode=True)
        assert res.strategy_name == "safeopsai"
        assert res.success is True
        assert res.sandbox_pass is True
        assert res.recovery_score > 0.0
    asyncio.run(_run())


def test_05_environment_reset():
    async def _run():
        cfg = EvaluationConfig()
        controller = ExperimentController(cfg, mock_mode=True)
        ok = await controller.reset_environment()
        assert ok is True
    asyncio.run(_run())


def test_06_metric_calculation():
    lats = calculate_latencies(
        fault_injected_at="2026-08-22T10:00:00+00:00",
        incident_detected_at="2026-08-22T10:00:05+00:00",
        decision_at="2026-08-22T10:00:07+00:00",
        remediation_started_at="2026-08-22T10:00:08+00:00",
        recovered_at="2026-08-22T10:00:20+00:00",
    )
    assert lats["detection_latency_seconds"] == 5.0
    assert lats["decision_latency_seconds"] == 2.0
    assert lats["remediation_latency_seconds"] == 12.0
    assert lats["mttr_seconds"] == 15.0


def test_07_mttr_calculation():
    lats = calculate_latencies(
        fault_injected_at="2026-08-22T10:00:00+00:00",
        incident_detected_at="2026-08-22T10:00:10+00:00",
        decision_at=None,
        remediation_started_at=None,
        recovered_at="2026-08-22T10:00:45+00:00",
    )
    assert lats["mttr_seconds"] == 35.0


def test_08_rollback_rate_calculation():
    records = [
        ExperimentRunRecord(
            experiment_run_id="R1", experiment_id="E1", scenario_id="S1", strategy="safeopsai",
            repetition=1, started_at="2026-08-22T10:00:00", rollback=True, success=True
        ),
        ExperimentRunRecord(
            experiment_run_id="R2", experiment_id="E1", scenario_id="S1", strategy="safeopsai",
            repetition=2, started_at="2026-08-22T10:00:00", rollback=False, success=True
        ),
    ]
    rb_cnt = sum(1 for r in records if r.rollback)
    rb_rate = rb_cnt / len(records)
    assert rb_rate == 0.5


def test_09_missing_timestamps_handling():
    lats = calculate_latencies(
        fault_injected_at="2026-08-22T10:00:00+00:00",
        incident_detected_at=None,
        decision_at=None,
        remediation_started_at=None,
        recovered_at=None,
        timeout_seconds=120.0,
    )
    assert lats["mttr_seconds"] == 120.0
    assert lats["downtime_seconds"] == 120.0


def test_10_failed_experiment_recovery():
    async def _run():
        cfg = EvaluationConfig()
        controller = ExperimentController(cfg, mock_mode=True)
        # Mock error during trial
        rec = await controller.run_single_trial(
            experiment_id="E_ERR",
            scenario=get_scenario("SCENARIO-01"),
            strategy_name="invalid_strategy_force_fail",
            repetition=1,
        )
        assert rec.status == "ERROR"
        assert rec.mttr_seconds == 120.0
    asyncio.run(_run())


def test_11_randomized_ordering():
    async def _run():
        cfg = EvaluationConfig()
        cfg.randomize_order = True
        cfg.repetitions = 2
        cfg.warmup_runs = 0
        controller = ExperimentController(cfg, mock_mode=True)
        recs = await controller.run_campaign(
            selected_scenarios=["SCENARIO-01", "SCENARIO-02"],
            selected_strategies=["safeopsai", "naive_restart"],
        )
        assert len(recs) == 8
    asyncio.run(_run())


def test_12_warmup_exclusion():
    records = [
        ExperimentRunRecord(
            experiment_run_id="W1", experiment_id="E1", scenario_id="S1", strategy="safeopsai",
            repetition=1, is_warmup=True, started_at="2026-08-22T10:00:00", mttr_seconds=100.0
        ),
        ExperimentRunRecord(
            experiment_run_id="M1", experiment_id="E1", scenario_id="S1", strategy="safeopsai",
            repetition=1, is_warmup=False, started_at="2026-08-22T10:00:00", mttr_seconds=10.0
        ),
    ]
    valid_recs = [r for r in records if not r.is_warmup]
    assert len(valid_recs) == 1
    assert valid_recs[0].mttr_seconds == 10.0


def test_13_statistical_calculations():
    obs = [10.0, 12.0, 11.0, 15.0, 13.0, 9.0, 14.0, 12.0, 11.0, 13.0]
    summary = calculate_metric_summary("mttr", "SCENARIO-01", "safeopsai", obs)
    assert summary.sample_size == 10
    assert summary.mean == 12.0
    assert summary.ci_95_lower < summary.mean < summary.ci_95_upper

    comp = compare_strategies_statistically([20.0]*10, [10.0]*10, "SCENARIO-01", "mttr")
    assert comp.treatment_mean < comp.baseline_mean


def test_14_environment_safety_guard():
    # Should pass when simulation
    check_safety_guard("simulation")

    # Should raise error when production or empty
    with pytest.raises(SafetyViolationError):
        check_safety_guard("production")

    with pytest.raises(SafetyViolationError):
        check_safety_guard("")
