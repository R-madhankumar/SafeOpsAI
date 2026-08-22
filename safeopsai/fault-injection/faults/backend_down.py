"""
Fault: Backend Container Failure
=================================
Stops (or kills) the safeops-backend Docker container to simulate a hard
service crash. Provides restore() to bring it back up and wait for healthy.

Expected Prometheus effect:
  - up{job="safeops-backend"} drops to 0
  - BackendDown alert fires after 15 s
  - http_requests_total stops incrementing

Recovery:
  - Container restarted via Docker API
  - Backend health endpoint polled until responsive
"""

import time
from datetime import datetime, timezone
from typing import Optional

import docker  # type: ignore
from docker.errors import NotFound, APIError  # type: ignore

import config
from logger import log, GREEN, RED, YELLOW, get_event_logger


# ── Helpers ───────────────────────────────────────────────────────────────

def _client() -> docker.DockerClient:
    return docker.from_env()


def _get_container(name: str):
    """Return a Docker container object; raise clearly if not found."""
    if name not in config.ALLOWED_CONTAINERS:
        raise ValueError(
            f"Safety check failed: '{name}' is not in ALLOWED_CONTAINERS. "
            f"Allowed: {sorted(config.ALLOWED_CONTAINERS)}"
        )
    try:
        return _client().containers.get(name)
    except NotFound:
        raise RuntimeError(
            f"Container '{name}' not found. "
            "Is 'docker compose up' running in safeopsai/?"
        )


def _wait_healthy(container_name: str, timeout: int = 60) -> bool:
    """Poll until the container's health status is 'healthy' or timeout."""
    deadline = time.time() + timeout
    import requests  # type: ignore

    backend_url = config.BACKEND_URL + config.BACKEND_IDENTITY_ENDPOINT
    log.info(f"  Waiting for {container_name} to become healthy (timeout={timeout}s)…")

    while time.time() < deadline:
        try:
            c = _client().containers.get(container_name)
            # Container must be running first
            if c.status != "running":
                time.sleep(2)
                continue
            # Then check the HTTP health probe
            r = requests.get(backend_url, timeout=3)
            if r.status_code == 200:
                data = r.json()
                if data.get(config.BACKEND_IDENTITY_FIELD) == config.BACKEND_IDENTITY_VALUE:
                    return True
        except Exception:
            pass
        time.sleep(2)

    return False


# ── Public API ────────────────────────────────────────────────────────────

def inject(
    container_name: str = config.BACKEND_CONTAINER,
    kill: bool = False,
) -> dict:
    """
    Stop (or kill) the backend container.

    Parameters
    ----------
    container_name : container to stop (must be in ALLOWED_CONTAINERS)
    kill           : if True use SIGKILL; if False use graceful stop (SIGTERM)

    Returns
    -------
    dict  event record written to the JSONL log
    """
    started_at = datetime.now(timezone.utc).isoformat()
    action = "kill" if kill else "stop"

    log.info(YELLOW(f"[backend-down] {action.upper()}ing container '{container_name}'…"))

    container = _get_container(container_name)

    try:
        if kill:
            container.kill()
        else:
            container.stop(timeout=5)
    except APIError as exc:
        raise RuntimeError(f"Docker API error while {action}ing '{container_name}': {exc}")

    log.info(RED(f"[backend-down] Container '{container_name}' is DOWN."))
    log.info("  Prometheus will detect this via BackendDown alert in ~15 s.")

    event = {
        "event":            "fault_started",
        "scenario":         "backend-down",
        "fault_type":       "container_down",
        "started_at":       started_at,
        "ended_at":         "",
        "duration_seconds": 0,
        "status":           "active",
        "details":          {"container": container_name, "action": action},
    }
    get_event_logger().write(**event)
    return event


def restore(
    container_name: str = config.BACKEND_CONTAINER,
    wait: bool = True,
) -> dict:
    """
    Start the backend container again and optionally wait until healthy.

    Parameters
    ----------
    container_name : container to start
    wait           : if True, block until HTTP /health responds correctly

    Returns
    -------
    dict  event record written to the JSONL log
    """
    started_at = datetime.now(timezone.utc).isoformat()
    log.info(YELLOW(f"[backend-down] Restoring container '{container_name}'…"))

    container = _get_container(container_name)

    try:
        container.start()
    except APIError as exc:
        raise RuntimeError(f"Docker API error starting '{container_name}': {exc}")

    log.info(f"[backend-down] Container '{container_name}' start command sent.")

    success = True
    if wait:
        success = _wait_healthy(container_name, timeout=60)
        if success:
            log.info(GREEN(f"[backend-down] '{container_name}' is healthy again."))
        else:
            log.warning(RED(f"[backend-down] '{container_name}' did not become healthy within 60 s."))

    ended_at = datetime.now(timezone.utc).isoformat()
    event = {
        "event":            "fault_cleared",
        "scenario":         "backend-down",
        "fault_type":       "container_down",
        "started_at":       started_at,
        "ended_at":         ended_at,
        "duration_seconds": 0,
        "status":           "recovered" if success else "recovery_timeout",
        "details":          {"container": container_name},
    }
    get_event_logger().write(**event)
    return event
