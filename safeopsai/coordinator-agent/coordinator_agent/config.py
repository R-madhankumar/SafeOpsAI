"""
SafeOpsAI — Coordinator Agent: Configuration
===============================================
Environment variables always override config.yml defaults.
Runtime weights live in the `coordinator_config` DB table (set via the
POST /weights endpoint) so a weight-sensitivity sweep needs no restart.

Key env vars
------------
DB_HOST / DB_PORT / DB_NAME / DB_USER / DB_PASS
API_PORT                8004
LOG_LEVEL               INFO
CONFIG_FILE             /app/coordinator_agent/config.yml
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

DB_HOST: str = os.getenv("DB_HOST", "localhost")
DB_PORT: int = int(os.getenv("DB_PORT", "5432"))
DB_NAME: str = os.getenv("DB_NAME", "safeopsdb")
DB_USER: str = os.getenv("DB_USER", "safeops")
DB_PASS: str = os.getenv("DB_PASS", "safeops123")

API_HOST: str = os.getenv("API_HOST", "0.0.0.0")
API_PORT: int = int(os.getenv("API_PORT", "8004"))
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()

_DEFAULT_CONFIG = Path(__file__).parent / "config.yml"
CONFIG_FILE: Path = Path(os.getenv("CONFIG_FILE", str(_DEFAULT_CONFIG)))


class AgentConfig:
    """Merges config.yml with environment overrides."""

    def __init__(self) -> None:
        self._raw: dict[str, Any] = {}
        if CONFIG_FILE.exists():
            with CONFIG_FILE.open() as fh:
                self._raw = yaml.safe_load(fh) or {}

    @property
    def poll_interval(self) -> float:
        return float(self._raw.get("agent", {}).get("poll_interval_seconds", 5))

    @property
    def max_concurrent(self) -> int:
        return int(self._raw.get("agent", {}).get("max_concurrent_incidents", 10))

    # ── Default weights (used only until the DB table is seeded) ──────────
    @property
    def default_weights(self) -> dict[str, float]:
        w = self._raw.get("weights", {})
        return {
            "cost":        float(w.get("cost", 0.3)),
            "reliability": float(w.get("reliability", 0.5)),
            "security":    float(w.get("security", 0.2)),
        }

    @property
    def default_method(self) -> str:
        return str(self._raw.get("weights", {}).get("method", "weighted_sum"))


# Singleton
cfg = AgentConfig()