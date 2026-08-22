"""
Fault: Slow Database Queries
=============================
Activates the `slow_queries` fault flag on the backend via
POST /admin/fault.  The backend then adds a random 2–5 s delay
before every DB query, simulating a degraded or overloaded database.

Uses the existing /admin/fault API — no duplicate implementation.

Expected Prometheus effect:
  - db_query_duration_seconds p95 climbs above 1 s
    → SlowDatabaseQueries alert fires after 30 s
  - http_request_duration_seconds p95 climbs above 2 s
    → HighRequestLatency alert fires after 30 s

Recovery:
  - POST /admin/fault/reset   OR   POST /admin/fault {"slow_queries": false}
  - Prometheus metrics return to baseline within 2–3 scrape intervals (~15 s)
"""

import time
from datetime import datetime, timezone

import requests  # type: ignore

import config
from logger import log, GREEN, RED, YELLOW, get_event_logger

# ── Helpers ───────────────────────────────────────────────────────────────

def _fault_url() -> str:
    return config.BACKEND_URL + "/admin/fault"

def _reset_url() -> str:
    return config.BACKEND_URL + "/admin/fault/reset"

def _post_fault(payload: dict) -> requests.Response:
    return requests.post(
        _fault_url(),
        json=payload,
        timeout=(config.HTTP_CONNECT_TIMEOUT, config.HTTP_READ_TIMEOUT),
    )

# ── Public API ────────────────────────────────────────────────────────────

def inject(backend_url: str | None = None) -> dict:
    """
    Enable slow_queries on the backend.

    Parameters
    ----------
    backend_url : override the default backend URL (useful in tests)
    """
    if backend_url:
        config.BACKEND_URL = backend_url

    started_at = datetime.now(timezone.utc).isoformat()
    log.info(YELLOW("[slow-db] Enabling slow_queries fault…"))

    try:
        resp = _post_fault({"slow_queries": True, "high_error_rate": False, "db_unavailable": False})
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise RuntimeError(
            f"Cannot reach backend at {config.BACKEND_URL}. "
            f"Is the stack running? Error: {exc}"
        )

    state = resp.json().get("current_state", {})
    if not state.get("slow_queries"):
        raise RuntimeError(f"Backend reported fault not set. Response: {resp.text}")

    log.info(GREEN("[slow-db] slow_queries ACTIVE — DB queries now take 2–5 s each."))
    log.info("  Watch: Prometheus /graph → db_query_duration_seconds")
    log.info("  Alert: SlowDatabaseQueries fires after ~30 s of sustained latency")

    event = {
        "event":      "fault_started",
        "scenario":   "slow-db",
        "fault_type": "slow_queries",
        "started_at": started_at,
        "ended_at":   "",
        "duration_seconds": 0,
        "status":     "active",
        "details":    {"backend_state": state},
    }
    get_event_logger().write(**event)
    return event


def clear(backend_url: str | None = None) -> dict:
    """Disable slow_queries and restore normal DB latency."""
    if backend_url:
        config.BACKEND_URL = backend_url

    started_at = datetime.now(timezone.utc).isoformat()
    log.info(YELLOW("[slow-db] Clearing slow_queries fault…"))

    try:
        resp = requests.post(
            _reset_url(),
            timeout=(config.HTTP_CONNECT_TIMEOUT, config.HTTP_READ_TIMEOUT),
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise RuntimeError(f"Failed to reset fault: {exc}")

    log.info(GREEN("[slow-db] Fault cleared — DB latency returning to normal."))

    ended_at = datetime.now(timezone.utc).isoformat()
    event = {
        "event":      "fault_cleared",
        "scenario":   "slow-db",
        "fault_type": "slow_queries",
        "started_at": started_at,
        "ended_at":   ended_at,
        "duration_seconds": 0,
        "status":     "cleared",
        "details":    {},
    }
    get_event_logger().write(**event)
    return event


def run(duration: int = config.DEFAULT_DURATION_SECONDS) -> dict:
    """
    Convenience: inject → wait `duration` seconds → clear.
    Blocks the caller for `duration` seconds.
    """
    started_at = datetime.now(timezone.utc).isoformat()
    inject()
    log.info(f"[slow-db] Fault active for {duration} s…")
    time.sleep(duration)
    clear()
    ended_at = datetime.now(timezone.utc).isoformat()

    event = {
        "event":            "scenario_completed",
        "scenario":         "slow-db",
        "fault_type":       "slow_queries",
        "started_at":       started_at,
        "ended_at":         ended_at,
        "duration_seconds": duration,
        "status":           "completed",
        "details":          {},
    }
    get_event_logger().write(**event)
    return event
