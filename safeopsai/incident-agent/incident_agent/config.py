"""
Incident Agent — Configuration
================================
All tunables come from environment variables first, then fall back
to the values in rules.yml (for rule thresholds) or the hard-coded
defaults below (for infrastructure addresses).

Environment variables
---------------------
PROMETHEUS_URL          http://localhost:9090
DB_HOST                 localhost
DB_PORT                 5432
DB_NAME                 safeopsdb
DB_USER                 safeops
DB_PASS                 safeops123
RULES_FILE              /app/incident_agent/rules.yml
API_PORT                8001
LOG_LEVEL               INFO
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import yaml

# ── Infrastructure ────────────────────────────────────────────────────────
PROMETHEUS_URL: str = os.getenv("PROMETHEUS_URL", "http://localhost:9090")

DB_HOST: str = os.getenv("DB_HOST", "localhost")
DB_PORT: int = int(os.getenv("DB_PORT", "5432"))
DB_NAME: str = os.getenv("DB_NAME", "safeopsdb")
DB_USER: str = os.getenv("DB_USER", "safeops")
DB_PASS: str = os.getenv("DB_PASS", "safeops123")

API_HOST: str = os.getenv("API_HOST", "0.0.0.0")
API_PORT: int = int(os.getenv("API_PORT", "8001"))

LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()

# ── Rules file ────────────────────────────────────────────────────────────
_DEFAULT_RULES_FILE = Path(__file__).parent / "rules.yml"
RULES_FILE: Path = Path(os.getenv("RULES_FILE", str(_DEFAULT_RULES_FILE)))

# ── Topology file ─────────────────────────────────────────────────────────
_DEFAULT_TOPOLOGY_FILE = Path(__file__).parent / "topology.yml"
TOPOLOGY_FILE: Path = Path(os.getenv("TOPOLOGY_FILE", str(_DEFAULT_TOPOLOGY_FILE)))


# ── Parsed rules ─────────────────────────────────────────────────────────

class RulesConfig:
    """
    Parses rules.yml once at startup and exposes typed accessors.
    Reload by calling load() again (used in tests).
    """

    def __init__(self) -> None:
        self._raw: dict[str, Any] = {}
        self.load()

    def load(self, path: Path | None = None) -> None:
        p = path or RULES_FILE
        if not p.exists():
            raise FileNotFoundError(f"Rules file not found: {p}")
        with p.open() as fh:
            self._raw = yaml.safe_load(fh)

    # ── Polling ───────────────────────────────────────────────────────────
    @property
    def poll_interval(self) -> float:
        return float(self._raw.get("polling", {}).get("poll_interval_seconds", 5))

    @property
    def prometheus_timeout(self) -> float:
        return float(self._raw.get("polling", {}).get("prometheus_timeout_seconds", 5))

    # ── Rule accessors ────────────────────────────────────────────────────
    def _rule(self, name: str) -> dict[str, Any]:
        return self._raw.get("incident_rules", {}).get(name, {})

    def rule_enabled(self, name: str) -> bool:
        return bool(self._rule(name).get("enabled", True))

    def rule_for_seconds(self, name: str) -> float:
        return float(self._rule(name).get("for_seconds", 30))

    def rule_threshold(self, name: str, key: str = "threshold") -> float:
        return float(self._rule(name).get(key, 0))

    def rule_severity(self, name: str) -> str:
        return str(self._rule(name).get("severity", "medium"))

    def rule_description(self, name: str) -> str:
        return str(self._rule(name).get("description", ""))

    # ── Convenience bundles ───────────────────────────────────────────────
    @property
    def all_rule_names(self) -> list[str]:
        return list(self._raw.get("incident_rules", {}).keys())

    def raw_rule(self, name: str) -> dict[str, Any]:
        return dict(self._rule(name))


# ── Topology ──────────────────────────────────────────────────────────────

class TopologyConfig:
    """Loads topology.yml — service dependency map for Root Cause Agent context."""

    def __init__(self) -> None:
        self._raw: dict[str, Any] = {}
        self.load()

    def load(self, path: Path | None = None) -> None:
        p = path or TOPOLOGY_FILE
        if not p.exists():
            return   # topology is optional — agent works without it
        with p.open() as fh:
            self._raw = yaml.safe_load(fh) or {}

    def dependencies(self, service: str) -> list[str]:
        """Return direct dependencies of a service."""
        return self._raw.get("services", {}).get(service, {}).get("depends_on", [])

    def cascade_pattern(self, root_service: str) -> dict[str, Any] | None:
        """Return the cascade pattern for a root service failure, or None."""
        for name, pattern in self._raw.get("cascade_patterns", {}).items():
            if pattern.get("root") == root_service:
                return dict(pattern)
        return None

    def all_services(self) -> dict[str, Any]:
        return dict(self._raw.get("services", {}))

    def to_dict(self) -> dict[str, Any]:
        return dict(self._raw)


# Singletons — imported by all modules
rules    = RulesConfig()
topology = TopologyConfig()
