"""
SafeOpsAI — Sandbox Agent: Configuration Loader
=================================================
Loads environment variables and YAML configuration.
"""

import os
from pathlib import Path
from dataclasses import dataclass, field
import yaml

DB_HOST = os.getenv("DB_HOST", "database")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_NAME = os.getenv("DB_NAME", "safeopsdb")
DB_USER = os.getenv("DB_USER", "safeops")
DB_PASS = os.getenv("DB_PASS", "safeops123")

API_PORT = int(os.getenv("API_PORT", "8005"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://prometheus:9090")
BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8000")


@dataclass
class Config:
    poll_interval: float = 5.0
    max_concurrent: int = 5
    health_check_timeout: float = 5.0
    stabilization_period: float = 2.0
    min_validation_score: float = 0.70
    max_acceptable_latency: float = 1.5
    max_acceptable_error_rate: float = 0.05
    db_connection_timeout: float = 5.0
    prometheus_url: str = PROMETHEUS_URL
    backend_url: str = BACKEND_URL

    @classmethod
    def load(cls) -> "Config":
        config_file = Path(__file__).parent / "config.yml"
        if config_file.exists():
            try:
                data = yaml.safe_load(config_file.read_text()) or {}
                return cls(
                    poll_interval=float(data.get("poll_interval", 5.0)),
                    max_concurrent=int(data.get("max_concurrent", 5)),
                    health_check_timeout=float(data.get("health_check_timeout", 5.0)),
                    stabilization_period=float(data.get("stabilization_period", 2.0)),
                    min_validation_score=float(data.get("min_validation_score", 0.70)),
                    max_acceptable_latency=float(data.get("max_acceptable_latency", 1.5)),
                    max_acceptable_error_rate=float(data.get("max_acceptable_error_rate", 0.05)),
                    db_connection_timeout=float(data.get("db_connection_timeout", 5.0)),
                    prometheus_url=os.getenv("PROMETHEUS_URL", data.get("prometheus_url", PROMETHEUS_URL)),
                    backend_url=os.getenv("BACKEND_URL", data.get("backend_url", BACKEND_URL)),
                )
            except Exception:
                pass
        return cls()


cfg = Config.load()
