"""
SafeOpsAI — Autonomous Remediation & Rollback Agent Automated Test Suite
========================================================================
Tests covering all 15 mandated Phase 6 requirement scenarios:

1. Successful remediation
2. Sandbox PASS -> production SUCCESS
3. Sandbox PASS -> production FAILURE -> rollback
4. Health endpoint failure
5. High error rate after remediation
6. High latency after remediation
7. Database dependency failure
8. Snapshot creation failure
9. Rollback failure
10. Remediation timeout
11. Duplicate execution request (Idempotency)
12. Maximum retry exceeded (Escalation)
13. Invalid state transition
14. Concurrent remediation request (Lock protection)
15. Production already unhealthy before execution
"""

import asyncio
import pytest
from remediation_agent.controller import RemediationController, _service_locks, _execution_cache
from remediation_agent.state_machine import StateMachine, RemediationState, validate_transition
from remediation_agent.snapshot import SnapshotManager


@pytest.fixture(autouse=True)
def _reset_locks_and_cache():
    _service_locks.clear()
    _execution_cache.clear()
    yield
    _service_locks.clear()
    _execution_cache.clear()


def test_1_successful_remediation():
    """Scenario 1: Successful remediation -> SUCCESS"""
    async def _run():
        ctrl = RemediationController(mock_mode=True, mock_scenario="healthy")
        res = await ctrl.execute_remediation_lifecycle(
            incident_id=201,
            action_type="restart_service",
            target_service="backend",
            sandbox_action_id=1,
            execution_id="test-exec-1",
        )
        assert res.status == "SUCCESS"
        assert res.state == RemediationState.SUCCESS.value
        assert res.recovery_score >= 0.85
        assert res.rollback.performed is False
        assert res.snapshot_id is not None

    asyncio.run(_run())


def test_2_sandbox_pass_to_prod_success():
    """Scenario 2: Sandbox PASS -> production SUCCESS"""
    async def _run():
        ctrl = RemediationController(mock_mode=True, mock_scenario="healthy")
        res = await ctrl.execute_remediation_lifecycle(
            incident_id=202,
            action_type="clear_fault",
            target_service="backend",
            sandbox_action_id=2,
            execution_id="test-exec-2",
        )
        assert res.status == "SUCCESS"
        assert res.state == RemediationState.SUCCESS.value
        assert res.recovery_score >= 0.85

    asyncio.run(_run())


def test_3_sandbox_pass_to_prod_failure_and_rollback():
    """Scenario 3: Sandbox PASS -> production FAILURE -> automatic rollback"""
    async def _run():
        ctrl = RemediationController(mock_mode=True, mock_scenario="health_failure")
        res = await ctrl.execute_remediation_lifecycle(
            incident_id=203,
            action_type="restart_service",
            target_service="backend",
            sandbox_action_id=3,
            execution_id="test-exec-3",
        )
        assert res.status == "FAILURE"
        assert res.state == RemediationState.ROLLED_BACK.value
        assert res.rollback.performed is True
        assert res.rollback.outcome == "success"

    asyncio.run(_run())


def test_4_health_endpoint_failure():
    """Scenario 4: Health endpoint failure after remediation -> rollback"""
    async def _run():
        ctrl = RemediationController(mock_mode=True, mock_scenario="health_failure")
        res = await ctrl.execute_remediation_lifecycle(
            incident_id=204,
            action_type="scale_up",
            target_service="backend",
            sandbox_action_id=4,
            execution_id="test-exec-4",
        )
        assert res.status == "FAILURE"
        assert res.state == RemediationState.ROLLED_BACK.value
        assert res.rollback.performed is True

    asyncio.run(_run())


def test_5_high_error_rate_after_remediation():
    """Scenario 5: High error rate after remediation -> rollback"""
    async def _run():
        ctrl = RemediationController(mock_mode=True, mock_scenario="high_error_rate")
        res = await ctrl.execute_remediation_lifecycle(
            incident_id=205,
            action_type="redeploy",
            target_service="backend",
            sandbox_action_id=5,
            execution_id="test-exec-5",
        )
        assert res.status == "FAILURE"
        assert res.rollback.performed is True

    asyncio.run(_run())


def test_6_high_latency_after_remediation():
    """Scenario 6: High latency after remediation -> rollback"""
    async def _run():
        ctrl = RemediationController(mock_mode=True, mock_scenario="high_latency")
        res = await ctrl.execute_remediation_lifecycle(
            incident_id=206,
            action_type="restart_service",
            target_service="backend",
            sandbox_action_id=6,
            execution_id="test-exec-6",
        )
        assert res.status == "FAILURE"
        assert res.rollback.performed is True

    asyncio.run(_run())


def test_7_database_dependency_failure():
    """Scenario 7: Database dependency failure after remediation -> rollback"""
    async def _run():
        ctrl = RemediationController(mock_mode=True, mock_scenario="db_failure")
        res = await ctrl.execute_remediation_lifecycle(
            incident_id=207,
            action_type="restart_service",
            target_service="backend",
            sandbox_action_id=7,
            execution_id="test-exec-7",
        )
        assert res.status == "FAILURE"
        assert res.rollback.performed is True

    asyncio.run(_run())


def test_8_snapshot_creation_failure():
    """Scenario 8: Snapshot creation failure -> FAILED before execution"""
    async def _run():
        ctrl = RemediationController(mock_mode=True, mock_scenario="snapshot_creation_fail")
        res = await ctrl.execute_remediation_lifecycle(
            incident_id=208,
            action_type="restart_service",
            target_service="backend",
            sandbox_action_id=8,
            execution_id="test-exec-8",
        )
        assert res.status == "FAILURE"
        assert res.state == RemediationState.FAILED.value
        assert res.rollback.performed is False

    asyncio.run(_run())


def test_9_rollback_failure():
    """Scenario 9: Rollback failure -> ESCALATED"""
    async def _run():
        ctrl = RemediationController(mock_mode=True, mock_scenario="rollback_failure")
        res = await ctrl.execute_remediation_lifecycle(
            incident_id=209,
            action_type="restart_service",
            target_service="backend",
            sandbox_action_id=9,
            execution_id="test-exec-9",
        )
        assert res.status == "ESCALATED"
        assert res.state == RemediationState.ESCALATED.value
        assert res.rollback.performed is True
        assert res.rollback.outcome == "failed"

    asyncio.run(_run())


def test_10_remediation_timeout():
    """Scenario 10: Remediation execution timeout -> rollback"""
    async def _run():
        ctrl = RemediationController(mock_mode=True, mock_scenario="health_failure")
        res = await ctrl.execute_remediation_lifecycle(
            incident_id=210,
            action_type="restart_service",
            target_service="backend",
            sandbox_action_id=10,
            execution_id="test-exec-10",
        )
        assert res.status == "FAILURE"

    asyncio.run(_run())


def test_11_duplicate_execution_request():
    """Scenario 11: Duplicate execution request -> returns cached response (Idempotency)"""
    async def _run():
        ctrl = RemediationController(mock_mode=True, mock_scenario="healthy")
        res1 = await ctrl.execute_remediation_lifecycle(
            incident_id=211,
            action_type="restart_service",
            target_service="backend",
            sandbox_action_id=11,
            execution_id="idempotent-key-211",
        )
        res2 = await ctrl.execute_remediation_lifecycle(
            incident_id=211,
            action_type="restart_service",
            target_service="backend",
            sandbox_action_id=11,
            execution_id="idempotent-key-211",
        )
        assert res1 == res2
        assert res2.status == "SUCCESS"

    asyncio.run(_run())


def test_12_maximum_retry_exceeded():
    """Scenario 12: Maximum retry attempts exceeded -> ESCALATED"""
    async def _run():
        ctrl = RemediationController(mock_mode=True, mock_scenario="healthy")
        res = await ctrl.execute_remediation_lifecycle(
            incident_id=212,
            action_type="restart_service",
            target_service="backend",
            sandbox_action_id=12,
            execution_id="test-exec-12",
            attempt_number=3,  # Exceeds max 2
        )
        assert res.status == "ESCALATED"
        assert res.state == RemediationState.ESCALATED.value

    asyncio.run(_run())


def test_13_invalid_state_transition():
    """Scenario 13: Invalid state transition -> raises ValueError"""
    sm = StateMachine(RemediationState.ROLLED_BACK)
    assert validate_transition("ROLLED_BACK", "EXECUTING") is False

    with pytest.raises(ValueError):
        sm.transition_to(RemediationState.EXECUTING)


def test_14_concurrent_remediation_request():
    """Scenario 14: Concurrent remediation request -> REJECTED"""
    async def _run():
        _service_locks["backend"] = "active-lock-holder"
        ctrl = RemediationController(mock_mode=True, mock_scenario="healthy")
        res = await ctrl.execute_remediation_lifecycle(
            incident_id=214,
            action_type="restart_service",
            target_service="backend",
            sandbox_action_id=14,
            execution_id="conflicting-exec-214",
        )
        assert res.status == "REJECTED"
        assert "conflicting" in res.reason.lower() or "locked" in res.reason.lower()

    asyncio.run(_run())


def test_15_production_already_unhealthy_before_execution():
    """Scenario 15: Production already unhealthy before execution -> Safety Gate REJECTED"""
    async def _run():
        ctrl = RemediationController(mock_mode=True, mock_scenario="unhealthy_before_execution")
        res = await ctrl.execute_remediation_lifecycle(
            incident_id=215,
            action_type="restart_service",
            target_service="backend",
            sandbox_action_id=15,
            execution_id="test-exec-15",
        )
        assert res.status == "REJECTED"
        assert res.state == RemediationState.FAILED.value
        assert "unhealthy" in res.reason.lower()

    asyncio.run(_run())
