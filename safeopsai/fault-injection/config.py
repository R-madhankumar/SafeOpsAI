"""
Central configuration for the SafeOpsAI fault injection framework.

All values come from environment variables first, then fall back to
the defaults shown below.  This makes the framework work identically
when run as a bare Python script on the host OR as a Docker container
inside the Compose network.
"""

import os

# ── Target service URLs ────────────────────────────────────────────────────
# When running on the host:  http://localhost:8000
# When running in Docker:    http://backend:8000
BACKEND_URL: str = os.getenv("SAFEOPS_BACKEND_URL", "http://localhost:8000")
PROMETHEUS_URL: str = os.getenv("SAFEOPS_PROMETHEUS_URL", "http://localhost:9090")

# ── Docker container names  (must match docker-compose.yml container_name) ─
BACKEND_CONTAINER: str = os.getenv("SAFEOPS_BACKEND_CONTAINER", "safeops-backend")
DATABASE_CONTAINER: str = os.getenv("SAFEOPS_DB_CONTAINER", "safeops-database")
FRONTEND_CONTAINER: str = os.getenv("SAFEOPS_FRONTEND_CONTAINER", "safeops-frontend")

# Safety allowlist — the fault injector will REFUSE to target any container
# whose name is not in this set.
ALLOWED_CONTAINERS: frozenset = frozenset({
    BACKEND_CONTAINER,
    DATABASE_CONTAINER,
    FRONTEND_CONTAINER,
})

# ── Default fault durations / parameters ──────────────────────────────────
DEFAULT_DURATION_SECONDS: int = int(os.getenv("SAFEOPS_FAULT_DURATION", "60"))
DEFAULT_CPU_WORKERS: int = int(os.getenv("SAFEOPS_CPU_WORKERS", "2"))
DEFAULT_TRAFFIC_REQUESTS: int = int(os.getenv("SAFEOPS_TRAFFIC_REQUESTS", "120"))
DEFAULT_TRAFFIC_INTERVAL: float = float(os.getenv("SAFEOPS_TRAFFIC_INTERVAL", "0.5"))

# ── Logging ────────────────────────────────────────────────────────────────
# JSONL event log written to this file (relative to the working directory).
EVENT_LOG_PATH: str = os.getenv("SAFEOPS_EVENT_LOG", "logs/fault_events.jsonl")

# ── Prometheus query defaults ──────────────────────────────────────────────
PROM_QUERY_TIMEOUT: int = 5   # seconds per Prometheus HTTP request
PROM_ALERT_POLL_INTERVAL: int = 5   # seconds between alert polls

# ── HTTP client timeouts ───────────────────────────────────────────────────
HTTP_CONNECT_TIMEOUT: float = 5.0
HTTP_READ_TIMEOUT: float = 15.0

# ── Environment fingerprint ────────────────────────────────────────────────
# Used by safety checks to confirm we are talking to SafeOpsAI and not
# some arbitrary service.
BACKEND_IDENTITY_ENDPOINT: str = "/health"
BACKEND_IDENTITY_FIELD: str = "service"
BACKEND_IDENTITY_VALUE: str = "backend"
