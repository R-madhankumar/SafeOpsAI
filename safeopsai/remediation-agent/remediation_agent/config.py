"""
SafeOpsAI — Remediation Agent: Configuration Loader
===================================================
Loads environment variables and YAML configuration.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict
import yaml

DB_HOST = os.getenv("DB_HOST", "database")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_NAME = os.getenv("DB_NAME", "safeopsdb")
DB_USER = os.getenv("DB_USER", "safeops")
DB_PASS = os.getenv("DB_PASS", "safeops123")

API_PORT = int(os.getenv("API_PORT", "8006"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://prometheus:9090")
BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8000")


@dataclass
class Config:
    poll_interval: float = 5.0
    max_concurrent: int = 5
    stabilization_seconds: float = 5.0
    degraded_grace_period_seconds: float = 5.0
    health_timeout_seconds: float = 5.0
    max_remediation_attempts: int = 2
    validation_expiration_minutes: int = 15
    recovery_success_threshold: float = 0.85
    degraded_threshold: float = 0.60
    weights: Dict[str, float] = field(
        default_factory=lambda: {
            "availability": 0.30,
            "error_rate": 0.25,
            "latency": 0.25,
            "dependency": 0.20,
        }
    )
    max_acceptable_latency: float = 1.5
    max_acceptable_error_rate: float = 0.05
    prometheus_url: str = PROMETHEUS_URL
    backend_url: str = BACKEND_URL

    @classmethod
    def load(cls) -> "Config":
        config_file = Path(__file__).parent / "config.yml"
        if config_file.exists():
            try:
                data = yaml.safe_load(config_file.read_text()) or {}
                weights_raw = data.get("weights", {})
                w = {
                    "availability": float(weights_raw.get("availability", 0.30)),
                    "error_rate": float(weights_raw.get("error_rate", 0.25)),
                    "latency": float(weights_raw.get("latency", 0.25)),
                    "dependency": float(weights_raw.get("dependency", 0.20)),
                }
                return cls(
                    poll_interval=float(data.get("poll_interval", 5.0)),
                    max_concurrent=int(data.get("max_concurrent", 5)),
                    stabilization_seconds=float(data.get("stabilization_seconds", 5.0)),
                    degraded_grace_period_seconds=float(data.get("degraded_grace_period_seconds", 5.0)),
                    health_timeout_seconds=float(data.get("health_timeout_seconds", 5.0)),
                    max_remediation_attempts=int(data.get("max_remediation_attempts", 2)),
                    validation_expiration_minutes=int(data.get("validation_expiration_minutes", 15)),
                    recovery_success_threshold=float(data.get("recovery_success_threshold", 0.85)),
                    degraded_threshold=float(data.get("degraded_threshold", 0.60)),
                    weights=w,
                    max_acceptable_latency=float(data.get("max_acceptable_latency", 1.5)),
                    max_acceptable_error_rate=float(data.get("max_acceptable_error_rate", 0.05)),
                    prometheus_url=os.getenv("PROMETHEUS_URL", data.get("prometheus_url", PROMETHEUS_URL)),
                    backend_url=os.getenv("BACKEND_URL", data.get("backend_url", BACKEND_URL)),
                )
            except Exception:
                pass
        return cls()


cfg = Config.load()
