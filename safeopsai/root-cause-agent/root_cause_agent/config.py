"""
Root Cause Agent — Configuration
===================================
Environment variables always override config.yml defaults.
The config.yml is loaded once at startup; restart to pick up changes.

Key env vars
------------
OLLAMA_URL              http://ollama:11434
OLLAMA_MODEL            llama3.2
PROMETHEUS_URL          http://prometheus:9090
DB_HOST / DB_PORT / DB_NAME / DB_USER / DB_PASS
API_PORT                8002
LOG_LEVEL               INFO
CONFIG_FILE             /app/root_cause_agent/config.yml
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

# ── Infrastructure — env-first ─────────────────────────────────────────────
OLLAMA_URL:   str = os.getenv("OLLAMA_URL",   "http://localhost:11434")
OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "llama3.2")

PROMETHEUS_URL: str = os.getenv("PROMETHEUS_URL", "http://localhost:9090")

DB_HOST: str = os.getenv("DB_HOST", "localhost")
DB_PORT: int = int(os.getenv("DB_PORT", "5432"))
DB_NAME: str = os.getenv("DB_NAME", "safeopsdb")
DB_USER: str = os.getenv("DB_USER", "safeops")
DB_PASS: str = os.getenv("DB_PASS", "safeops123")

API_HOST: str = os.getenv("API_HOST", "0.0.0.0")
API_PORT: int = int(os.getenv("API_PORT", "8002"))
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()

# ── Config file ────────────────────────────────────────────────────────────
_DEFAULT_CONFIG = Path(__file__).parent / "config.yml"
CONFIG_FILE: Path = Path(os.getenv("CONFIG_FILE", str(_DEFAULT_CONFIG)))

# ── Topology file (shared with incident-agent via volume mount) ────────────
TOPOLOGY_FILE: Path = Path(
    os.getenv("TOPOLOGY_FILE",
              str(Path(__file__).parent / "topology.yml"))
)


class AgentConfig:
    """
    Merges config.yml with environment variable overrides.
    Env vars for LLM settings:
        OLLAMA_URL, OLLAMA_MODEL, OLLAMA_TIMEOUT, OLLAMA_MAX_TOKENS,
        OLLAMA_TEMPERATURE, OLLAMA_FALLBACK
    """

    def __init__(self) -> None:
        self._raw: dict[str, Any] = {}
        self.load()

    def load(self, path: Path | None = None) -> None:
        p = path or CONFIG_FILE
        if p.exists():
            with p.open() as fh:
                self._raw = yaml.safe_load(fh) or {}
        # Env overrides for the most commonly tuned settings
        if os.getenv("OLLAMA_URL"):
            self._raw.setdefault("llm", {})["base_url"] = os.getenv("OLLAMA_URL")
        if os.getenv("OLLAMA_MODEL"):
            self._raw.setdefault("llm", {})["model"] = os.getenv("OLLAMA_MODEL")

    # ── Agent ────────────────────────────────────────────────────────────
    @property
    def poll_interval(self) -> float:
        return float(self._raw.get("agent", {}).get("poll_interval_seconds", 10))

    @property
    def max_concurrent(self) -> int:
        return int(self._raw.get("agent", {}).get("max_concurrent_diagnoses", 3))

    @property
    def diagnosis_delay(self) -> float:
        return float(self._raw.get("agent", {}).get("diagnosis_delay_seconds", 5))

    # ── LLM ─────────────────────────────────────────────────────────────
    @property
    def llm_url(self) -> str:
        return self._raw.get("llm", {}).get("base_url", OLLAMA_URL)

    @property
    def llm_model(self) -> str:
        return self._raw.get("llm", {}).get("model", OLLAMA_MODEL)

    @property
    def llm_max_tokens(self) -> int:
        return int(self._raw.get("llm", {}).get("max_tokens", 1024))

    @property
    def llm_temperature(self) -> float:
        return float(self._raw.get("llm", {}).get("temperature", 0.1))

    @property
    def llm_timeout(self) -> float:
        val = os.getenv("OLLAMA_TIMEOUT")
        if val:
            return float(val)
        return float(self._raw.get("llm", {}).get("timeout_seconds", 120))

    @property
    def llm_fallback(self) -> bool:
        val = os.getenv("OLLAMA_FALLBACK")
        if val is not None:
            return val.lower() not in ("0", "false", "no")
        return bool(self._raw.get("llm", {}).get("fallback_on_error", True))

    # ── Prometheus ───────────────────────────────────────────────────────
    @property
    def prom_url(self) -> str:
        return self._raw.get("prometheus", {}).get("base_url", PROMETHEUS_URL)

    @property
    def prom_timeout(self) -> float:
        return float(self._raw.get("prometheus", {}).get("timeout_seconds", 5))

    @property
    def history_window_minutes(self) -> int:
        return int(self._raw.get("prometheus", {}).get("history_window_minutes", 5))

    # ── Evidence ─────────────────────────────────────────────────────────
    @property
    def include_topology(self) -> bool:
        return bool(self._raw.get("evidence", {}).get("include_topology", True))


# Singleton
cfg = AgentConfig()
