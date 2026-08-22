"""
SafeOpsAI Evaluation — Config Loader
=====================================
Loads configuration settings from experiments.yaml or environment variables.
"""

import os
from pathlib import Path
from typing import Any, Dict, List
import yaml

CONFIG_DIR = Path(__file__).parent / "config"
DEFAULT_CONFIG_PATH = CONFIG_DIR / "experiments.yaml"


class EvaluationConfig:
    def __init__(self, config_path: Path | str | None = None):
        path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                self._raw = yaml.safe_load(f) or {}
        else:
            self._raw = {}

        exp = self._raw.get("experiment", {})
        self.experiment_name: str = exp.get("name", "SafeOpsAI-Evaluation")
        self.repetitions: int = exp.get("repetitions", 20)
        self.warmup_runs: int = exp.get("warmup_runs", 2)
        self.cooldown_seconds: float = float(exp.get("cooldown_seconds", 5))
        self.randomize_order: bool = exp.get("randomize_order", True)
        self.random_seed: int = exp.get("random_seed", 42)

        met = self._raw.get("metrics", {})
        self.recovery_window_seconds: float = float(met.get("recovery_window_seconds", 120))
        self.stabilization_delay_seconds: float = float(met.get("stabilization_delay_seconds", 3))

        self.strategies: List[str] = self._raw.get("strategies", [
            "safeopsai", "naive_restart", "no_sandbox", "no_multi_agent", "no_rollback"
        ])
        self.scenarios: List[str] = self._raw.get("scenarios", [
            "SCENARIO-01", "SCENARIO-02", "SCENARIO-03", "SCENARIO-04", "SCENARIO-05", "SCENARIO-06"
        ])

        env = self._raw.get("environment", {})
        self.env_mode: str = os.getenv("ENVIRONMENT", os.getenv("SAFEOPS_ENV", env.get("mode", "simulation")))
        self.backend_url: str = os.getenv("SAFEOPS_BACKEND_URL", env.get("backend_url", "http://localhost:8000"))
        self.prometheus_url: str = os.getenv("SAFEOPS_PROMETHEUS_URL", env.get("prometheus_url", "http://localhost:9090"))
        self.db_host: str = os.getenv("DB_HOST", env.get("db_host", "localhost"))
        self.db_port: int = int(os.getenv("DB_PORT", env.get("db_port", 5433)))
        self.db_name: str = os.getenv("DB_NAME", env.get("db_name", "safeopsdb"))
        self.db_user: str = os.getenv("DB_USER", env.get("db_user", "safeops"))
        self.db_pass: str = os.getenv("DB_PASS", env.get("db_pass", "safeops123"))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "experiment_name": self.experiment_name,
            "repetitions": self.repetitions,
            "warmup_runs": self.warmup_runs,
            "cooldown_seconds": self.cooldown_seconds,
            "randomize_order": self.randomize_order,
            "random_seed": self.random_seed,
            "recovery_window_seconds": self.recovery_window_seconds,
            "strategies": self.strategies,
            "scenarios": self.scenarios,
            "env_mode": self.env_mode,
        }
