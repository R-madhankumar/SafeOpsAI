"""
Fault: High Error Rate
=======================
Activates the `high_error_rate` fault flag, which causes the backend
middleware to randomly return HTTP 500 on ~50% of all requests.

This module also exposes a `generate_traffic()` helper that sends
a configurable burst of requests so the error rate actually becomes
visible in Prometheus (alerts need sustained traffic, not zero requests).

Expected Prometheus effect:
  - application_errors_total{error_type="injected_fault"} rises sharply
  - http_requests_total{status_code="500"} rises
  - rate(application_errors_total[1m]) > 0.5  → HighErrorRate alert
  - 5xx ratio > 0.1                            → High5xxRate alert

Recovery:
  - POST /admin/fault/reset
"""

import threading
import time
from datetime import datetime, timezone

import requests  # type: ignore

import config
from logger import log, GREEN, RED, YELLOW, get_event_logger

# ── Helpers ───────────────────────────────────────────────────────────────

def _fault_url()  -> str: return config.BACKEND_URL + "/admin/fault"
def _reset_url()  -> str: return config.BACKEND_URL + "/admin/fault/reset"
def _items_url()  -> str: return config.BACKEND_URL + "/items"
def _health_url() -> str: return config.BACKEND_URL + "/health"

def _set_flag(flag: bool) -> dict:
    resp = requests.post(
        _fault_url(),
        json={"slow_queries": False, "high_error_rate": flag, "db_unavailable": False},
        timeout=(config.HTTP_CONNECT_TIMEOUT, config.HTTP_READ_TIMEOUT),
    )
    resp.raise_for_status()
    return resp.json()

# ── Traffic generator (inline, simple) ───────────────────────────────────

def _burst(n: int = 60, interval: float = 0.5, stop_event: threading.Event | None = None) -> dict:
    """
    Fire n requests to /items and /health at `interval` seconds apart.
    Returns a summary dict.
    """
    successes = errors = 0
    latencies: list[float] = []
    urls = [_items_url(), _health_url()]

    for i in range(n):
        if stop_event and stop_event.is_set():
            break
        url = urls[i % len(urls)]
        t0 = time.monotonic()
        try:
            r = requests.get(url, timeout=5)
            latencies.append(time.monotonic() - t0)
            if r.status_code < 400:
                successes += 1
            else:
                errors += 1
        except Exception:
            errors += 1
        if interval > 0:
            time.sleep(interval)

    total = successes + errors
    avg   = round(sum(latencies) / len(latencies), 3) if latencies else 0
    return {"total": total, "success": successes, "error": errors, "avg_latency_s": avg}

# ── Public API ────────────────────────────────────────────────────────────

def inject(backend_url: str | None = None) -> dict:
    """Enable high_error_rate on the backend."""
    if backend_url:
        config.BACKEND_URL = backend_url

    started_at = datetime.now(timezone.utc).isoformat()
    log.info(YELLOW("[high-errors] Enabling high_error_rate fault…"))

    try:
        result = _set_flag(True)
    except requests.RequestException as exc:
        raise RuntimeError(f"Cannot reach backend: {exc}")

    state = result.get("current_state", {})
    if not state.get("high_error_rate"):
        raise RuntimeError(f"Backend did not confirm fault. Response: {result}")

    log.info(RED("[high-errors] high_error_rate ACTIVE — ~50% of requests will return HTTP 500."))
    log.info("  Watch: Prometheus → rate(application_errors_total[1m])")
    log.info("  Alert: HighErrorRate fires after ~30 s if traffic is flowing")

    event = {
        "event": "fault_started", "scenario": "high-errors",
        "fault_type": "high_error_rate", "started_at": started_at,
        "ended_at": "", "duration_seconds": 0, "status": "active",
        "details": {"backend_state": state},
    }
    get_event_logger().write(**event)
    return event


def clear(backend_url: str | None = None) -> dict:
    """Disable high_error_rate."""
    if backend_url:
        config.BACKEND_URL = backend_url

    started_at = datetime.now(timezone.utc).isoformat()
    log.info(YELLOW("[high-errors] Clearing high_error_rate fault…"))

    try:
        resp = requests.post(
            _reset_url(),
            timeout=(config.HTTP_CONNECT_TIMEOUT, config.HTTP_READ_TIMEOUT),
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise RuntimeError(f"Failed to reset fault: {exc}")

    log.info(GREEN("[high-errors] Fault cleared — error rate returning to normal."))

    ended_at = datetime.now(timezone.utc).isoformat()
    event = {
        "event": "fault_cleared", "scenario": "high-errors",
        "fault_type": "high_error_rate", "started_at": started_at,
        "ended_at": ended_at, "duration_seconds": 0, "status": "cleared",
        "details": {},
    }
    get_event_logger().write(**event)
    return event


def run(
    duration: int = config.DEFAULT_DURATION_SECONDS,
    traffic_requests: int = config.DEFAULT_TRAFFIC_REQUESTS,
    traffic_interval: float = config.DEFAULT_TRAFFIC_INTERVAL,
) -> dict:
    """
    Full fault run: inject → generate traffic concurrently → wait → clear.

    Traffic is generated in a background thread so it overlaps exactly
    with the fault window, ensuring Prometheus sees a sustained error rate.
    """
    started_at = datetime.now(timezone.utc).isoformat()
    stop_event = threading.Event()
    traffic_result: dict = {}

    def _traffic_worker():
        nonlocal traffic_result
        traffic_result = _burst(
            n=traffic_requests,
            interval=traffic_interval,
            stop_event=stop_event,
        )

    inject()

    t = threading.Thread(target=_traffic_worker, daemon=True)
    t.start()
    log.info(f"[high-errors] Sending traffic for {duration} s ({traffic_requests} req, {traffic_interval}s interval)…")
    time.sleep(duration)

    stop_event.set()
    t.join(timeout=10)
    clear()

    ended_at = datetime.now(timezone.utc).isoformat()
    log.info(f"[high-errors] Traffic summary: {traffic_result}")

    event = {
        "event": "scenario_completed", "scenario": "high-errors",
        "fault_type": "high_error_rate", "started_at": started_at,
        "ended_at": ended_at, "duration_seconds": duration, "status": "completed",
        "details": {"traffic": traffic_result},
    }
    get_event_logger().write(**event)
    return event
