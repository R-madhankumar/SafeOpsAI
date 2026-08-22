"""
SafeOpsAI Evaluation — Fault Scenario Catalog
==============================================
Defines standard fault scenarios using the existing fault injection mechanisms.
"""

from typing import Any, Dict, List
from pydantic import BaseModel


class FaultScenario(BaseModel):
    scenario_id: str
    name: str
    target_service: str
    fault_type: str
    expected_symptoms: List[str]
    timeout_seconds: int = 120
    recovery_threshold: float = 0.85

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()


SCENARIO_CATALOG: Dict[str, FaultScenario] = {
    "SCENARIO-01": FaultScenario(
        scenario_id="SCENARIO-01",
        name="Backend service failure",
        target_service="backend",
        fault_type="backend_down",
        expected_symptoms=["http_500", "backend_unreachable", "container_stopped"],
        timeout_seconds=120,
        recovery_threshold=0.85,
    ),
    "SCENARIO-02": FaultScenario(
        scenario_id="SCENARIO-02",
        name="Slow database queries",
        target_service="database",
        fault_type="slow_queries",
        expected_symptoms=["high_latency", "db_query_duration_seconds", "slow_http_responses"],
        timeout_seconds=120,
        recovery_threshold=0.85,
    ),
    "SCENARIO-03": FaultScenario(
        scenario_id="SCENARIO-03",
        name="High error rate",
        target_service="backend",
        fault_type="high_error_rate",
        expected_symptoms=["http_500_spike", "application_errors_total", "50_percent_failures"],
        timeout_seconds=120,
        recovery_threshold=0.85,
    ),
    "SCENARIO-04": FaultScenario(
        scenario_id="SCENARIO-04",
        name="Database unavailable",
        target_service="database",
        fault_type="db_unavailable",
        expected_symptoms=["http_503", "db_connection_refused", "db_health_check_failed"],
        timeout_seconds=120,
        recovery_threshold=0.85,
    ),
    "SCENARIO-05": FaultScenario(
        scenario_id="SCENARIO-05",
        name="CPU stress",
        target_service="backend",
        fault_type="cpu_stress",
        expected_symptoms=["high_cpu_usage", "latency_degradation", "resource_exhaustion"],
        timeout_seconds=120,
        recovery_threshold=0.85,
    ),
    "SCENARIO-06": FaultScenario(
        scenario_id="SCENARIO-06",
        name="Configuration failure",
        target_service="backend",
        fault_type="bad_db_config",
        expected_symptoms=["invalid_env_var", "startup_crash", "db_host_unresolvable"],
        timeout_seconds=120,
        recovery_threshold=0.85,
    ),
}


def get_scenario(scenario_id: str) -> FaultScenario:
    """Retrieve scenario by ID."""
    sid = scenario_id.upper().strip()
    if sid not in SCENARIO_CATALOG:
        # Fallback search by index or alias
        for s in SCENARIO_CATALOG.values():
            if s.fault_type == scenario_id or s.name.lower() == scenario_id.lower():
                return s
        raise KeyError(f"Unknown scenario '{scenario_id}'. Valid IDs: {list(SCENARIO_CATALOG.keys())}")
    return SCENARIO_CATALOG[sid]


def list_scenarios() -> List[FaultScenario]:
    """List all available fault scenarios."""
    return list(SCENARIO_CATALOG.values())
