"""
Fault: Database Unavailable
============================
Two-layer approach:

Layer 1 (soft)  — uses the existing backend /admin/fault API to set
                  `db_unavailable=True`.  The backend rejects all DB
                  calls with HTTP 503 immediately.  Fast, reversible,
                  no Docker operations needed.

Layer 2 (hard)  — stops the safeops-database container entirely.
                  More realistic: the backend loses its asyncpg
                  connection pool, retries, and returns errors.
                  Uses Docker SDK (same pattern as backend_down.py).

The CLI exposes --mode=soft (default) and --mode=hard.

Expected Prometheus effect:
  Soft mode:
    - application_errors_total{error_type="db_unavailable_fault"} rises
    - http_requests_total{status_code="503"} rises
    - HighErrorRate / High5xxRate alerts fire

  Hard mode:
    - up{job="postgres"} → 0  →  DatabaseDown alert fires (15 s)
    - Backend error rate also rises as pool connections time out

Safety:
  Hard mode restore waits for pg_isready before returning.
  PostgreSQL data volume is NEVER deleted.

Recovery:
  Soft: POST /admin/fault/reset
  Hard: docker start safeops-database  (handled by restore())
"""

import time
from datetime import datetime, timezone

import requests  # type: ignore
import docker  # type: ignore
from docker.errors import NotFound, APIError  # type: ignore

import config
from logger import log, GREEN, RED, YELLOW, get_event_logger

# ── Helpers ───────────────────────────────────────────────────────────────

def _client() -> docker.DockerClient:
    return docker.from_env()


def _get_container(name: str):
    if name not in config.ALLOWED_CONTAINERS:
        raise ValueError(f"Safety check: '{name}' not in ALLOWED_CONTAINERS.")
    try:
        return _client().containers.get(name)
    except NotFound:
        raise RuntimeError(f"Container '{name}' not found. Is the stack running?")


def _fault_url()  -> str: return config.BACKEND_URL + "/admin/fault"
def _reset_url()  -> str: return config.BACKEND_URL + "/admin/fault/reset"


def _soft_set(enabled: bool) -> dict:
    resp = requests.post(
        _fault_url(),
        json={"slow_queries": False, "high_error_rate": False, "db_unavailable": enabled},
        timeout=(config.HTTP_CONNECT_TIMEOUT, config.HTTP_READ_TIMEOUT),
    )
    resp.raise_for_status()
    return resp.json()


def _wait_db_ready(timeout: int = 60) -> bool:
    """Poll until postgres-exporter /metrics is reachable (DB is up)."""
    deadline = time.time() + timeout
    prom_pg_url = config.PROMETHEUS_URL + "/api/v1/query?query=up%7Bjob%3D%22postgres%22%7D"
    log.info(f"  Waiting for database to come back (timeout={timeout}s)…")
    while time.time() < deadline:
        try:
            r = requests.get(prom_pg_url, timeout=3)
            if r.status_code == 200:
                result = r.json().get("data", {}).get("result", [])
                if result and result[0].get("value", [None, "0"])[1] == "1":
                    return True
        except Exception:
            pass
        time.sleep(3)
    return False


# ── Public API ────────────────────────────────────────────────────────────

def inject(mode: str = "soft") -> dict:
    """
    Make the database unavailable.

    Parameters
    ----------
    mode : "soft" (API flag only) or "hard" (stop database container)
    """
    if mode not in ("soft", "hard"):
        raise ValueError(f"mode must be 'soft' or 'hard', got '{mode}'")

    started_at = datetime.now(timezone.utc).isoformat()
    log.info(YELLOW(f"[db-down] Injecting db_unavailable fault (mode={mode})…"))

    details: dict = {"mode": mode}

    if mode == "soft":
        try:
            result = _soft_set(True)
            details["backend_state"] = result.get("current_state", {})
        except requests.RequestException as exc:
            raise RuntimeError(f"Cannot reach backend: {exc}")
        log.info(RED("[db-down] SOFT: backend will reject all DB calls with HTTP 503."))

    else:  # hard
        container = _get_container(config.DATABASE_CONTAINER)
        try:
            container.stop(timeout=5)
        except APIError as exc:
            raise RuntimeError(f"Docker error stopping database: {exc}")
        details["container"] = config.DATABASE_CONTAINER
        log.info(RED(f"[db-down] HARD: container '{config.DATABASE_CONTAINER}' stopped."))
        log.info("  Prometheus → up{job='postgres'} will drop to 0 in ~15 s.")
        log.info("  DatabaseDown alert fires after 15 s.")

    event = {
        "event": "fault_started", "scenario": "db-down",
        "fault_type": "db_unavailable", "started_at": started_at,
        "ended_at": "", "duration_seconds": 0, "status": "active",
        "details": details,
    }
    get_event_logger().write(**event)
    return event


def restore(mode: str = "soft") -> dict:
    """Restore database availability."""
    started_at = datetime.now(timezone.utc).isoformat()
    log.info(YELLOW(f"[db-down] Restoring database availability (mode={mode})…"))

    success = True

    if mode == "soft":
        try:
            resp = requests.post(
                _reset_url(),
                timeout=(config.HTTP_CONNECT_TIMEOUT, config.HTTP_READ_TIMEOUT),
            )
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise RuntimeError(f"Failed to reset fault: {exc}")
        log.info(GREEN("[db-down] SOFT: backend fault cleared — DB calls restored."))

    else:  # hard
        container = _get_container(config.DATABASE_CONTAINER)
        try:
            container.start()
        except APIError as exc:
            raise RuntimeError(f"Docker error starting database: {exc}")
        success = _wait_db_ready(timeout=60)
        if success:
            log.info(GREEN(f"[db-down] HARD: database container back up and healthy."))
        else:
            log.warning(RED("[db-down] HARD: database did not become ready within 60 s."))

    ended_at = datetime.now(timezone.utc).isoformat()
    event = {
        "event": "fault_cleared", "scenario": "db-down",
        "fault_type": "db_unavailable", "started_at": started_at,
        "ended_at": ended_at, "duration_seconds": 0,
        "status": "recovered" if success else "recovery_timeout",
        "details": {"mode": mode},
    }
    get_event_logger().write(**event)
    return event


def run(duration: int = config.DEFAULT_DURATION_SECONDS, mode: str = "soft") -> dict:
    """inject → wait → restore."""
    started_at = datetime.now(timezone.utc).isoformat()
    inject(mode=mode)
    log.info(f"[db-down] Fault active for {duration} s…")
    time.sleep(duration)
    restore(mode=mode)
    ended_at = datetime.now(timezone.utc).isoformat()

    event = {
        "event": "scenario_completed", "scenario": "db-down",
        "fault_type": "db_unavailable", "started_at": started_at,
        "ended_at": ended_at, "duration_seconds": duration, "status": "completed",
        "details": {"mode": mode},
    }
    get_event_logger().write(**event)
    return event
