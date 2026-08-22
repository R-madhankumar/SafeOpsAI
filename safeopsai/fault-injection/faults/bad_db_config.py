"""
Fault: Bad Database Configuration
===================================
Simulates a misconfigured database connection by restarting the backend
container with a modified DB_HOST environment variable pointing to a
non-existent host.

HOW IT WORKS
------------
1. Read the current environment of safeops-backend via Docker API.
2. Save the original env values (DB_HOST, DB_PORT) in memory.
3. Stop the container.
4. Start a new container with the same image + config but DB_HOST
   overridden to "invalid-host" (or a bad port like 9999).
5. The backend starts, fails to connect to the database, and begins
   returning 503 on all DB-touching endpoints.

RESTORE
-------
1. Stop the misconfigured container.
2. Start the original container spec (the Compose-managed one) with
   the correct environment — effectively `docker compose up backend`.
   We achieve this by stopping the temp container and running
   `docker compose up -d backend` via subprocess, which restores
   Compose's ownership of the container.

SAFETY
------
- The postgres-data volume is NEVER touched.
- The original backend image and Compose config are unchanged.
- The Compose project can always be restored with `docker compose up -d`.
- If the fault process is killed mid-run, `docker compose up -d backend`
  on the host will always restore the correct state.

Expected Prometheus effect:
  - application_errors_total rises (DB connection failures)
  - http_requests_total{status_code="503"} rises
  - HighErrorRate / High5xxRate alerts fire
  - db_query_duration_seconds stops being recorded (no queries succeed)
"""

import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import docker  # type: ignore
from docker.errors import NotFound, APIError  # type: ignore

import config
from logger import log, GREEN, RED, YELLOW, get_event_logger

# Path to the docker-compose.yml — two levels up from fault-injection/
_COMPOSE_DIR = str(Path(__file__).parent.parent.parent)


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


def _compose_restore() -> None:
    """Re-create the backend container under Compose management."""
    log.info("[bad-db-config] Running 'docker compose up -d backend' to restore…")
    result = subprocess.run(
        ["docker", "compose", "up", "-d", "backend"],
        cwd=_COMPOSE_DIR,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"docker compose up -d backend failed:\n{result.stderr}"
        )
    log.info(GREEN("[bad-db-config] Compose restored backend with correct config."))


def _wait_backend_healthy(timeout: int = 60) -> bool:
    """Poll /health until the backend responds correctly."""
    import requests as req  # type: ignore
    deadline = time.time() + timeout
    url = config.BACKEND_URL + config.BACKEND_IDENTITY_ENDPOINT
    while time.time() < deadline:
        try:
            r = req.get(url, timeout=3)
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
    bad_host: str = "invalid-db-host",
    bad_port: str = "9999",
) -> dict:
    """
    Restart the backend with a broken DB_HOST, causing all DB operations
    to fail with connection errors.

    Parameters
    ----------
    bad_host : The fake hostname the backend will try to connect to.
    bad_port : The fake port (used if you want a port-only misconfiguration).
    """
    started_at = datetime.now(timezone.utc).isoformat()
    log.info(YELLOW(f"[bad-db-config] Injecting bad DB config (host={bad_host!r})…"))

    container = _get_container(config.BACKEND_CONTAINER)

    # ── Capture current container config so we can reproduce it ──────────
    attrs      = container.attrs
    image      = attrs["Config"]["Image"]
    env_list   = attrs["Config"].get("Env", [])  # list of "KEY=VALUE" strings
    labels     = attrs["Config"].get("Labels", {})
    networks   = list(attrs["NetworkSettings"]["Networks"].keys())

    log.info(f"  Original image : {image}")
    log.info(f"  Overriding DB_HOST → {bad_host!r}")

    # ── Build new env list with patched DB_HOST (and bad port) ───────────
    new_env = []
    for entry in env_list:
        key = entry.split("=", 1)[0]
        if key == "DB_HOST":
            new_env.append(f"DB_HOST={bad_host}")
        elif key == "DB_PORT":
            new_env.append(f"DB_PORT={bad_port}")
        else:
            new_env.append(entry)

    # ── Stop the original container ───────────────────────────────────────
    log.info(f"[bad-db-config] Stopping '{config.BACKEND_CONTAINER}'…")
    try:
        container.stop(timeout=5)
    except APIError as exc:
        raise RuntimeError(f"Failed to stop backend: {exc}")

    # ── Start a replacement container with bad config ─────────────────────
    # We use the same container name so Docker Compose can still manage it
    # (Compose identifies containers by name, not by ID).
    log.info(f"[bad-db-config] Starting replacement container with bad DB env…")
    try:
        # Remove the stopped container first (Compose created it; we recreate)
        container.remove(force=True)
        client = _client()
        bad_container = client.containers.run(
            image=image,
            name=config.BACKEND_CONTAINER,
            environment=new_env,
            network=networks[0] if networks else "safeopsai_safeops-net",
            detach=True,
            labels=labels,
            # Replicate the port binding
            ports={"8000/tcp": 8000},
        )
        log.info(RED(f"[bad-db-config] Bad container running (id={bad_container.short_id})."))
        log.info("  Backend will fail to connect to the database.")
        log.info("  Watch: application_errors_total rising in Prometheus.")
    except APIError as exc:
        # If container creation fails, attempt immediate compose restore
        log.warning(RED(f"[bad-db-config] Failed to start bad container: {exc}"))
        log.warning("  Attempting immediate restore via docker compose…")
        _compose_restore()
        raise RuntimeError(f"Injection failed; environment restored. Original error: {exc}")

    event = {
        "event": "fault_started", "scenario": "bad-db-config",
        "fault_type": "bad_db_config", "started_at": started_at,
        "ended_at": "", "duration_seconds": 0, "status": "active",
        "details": {"bad_host": bad_host, "bad_port": bad_port, "image": image},
    }
    get_event_logger().write(**event)
    return event


def restore() -> dict:
    """
    Remove the misconfigured container and restore the Compose-managed
    backend with the correct database configuration.
    """
    started_at = datetime.now(timezone.utc).isoformat()
    log.info(YELLOW("[bad-db-config] Restoring correct backend configuration…"))

    # Stop and remove whatever is running under the backend container name
    try:
        c = _client().containers.get(config.BACKEND_CONTAINER)
        c.stop(timeout=5)
        c.remove(force=True)
        log.info(f"[bad-db-config] Removed misconfigured container.")
    except NotFound:
        log.info("[bad-db-config] Container already gone — proceeding with restore.")
    except APIError as exc:
        log.warning(f"[bad-db-config] Warning during cleanup: {exc}")

    # Bring the correct container back via Compose
    _compose_restore()

    # Wait for the correctly-configured backend to become healthy
    healthy = _wait_backend_healthy(timeout=60)
    if healthy:
        log.info(GREEN("[bad-db-config] Backend healthy with correct DB config."))
    else:
        log.warning(RED("[bad-db-config] Backend did not become healthy in 60 s after restore."))

    ended_at = datetime.now(timezone.utc).isoformat()
    event = {
        "event": "fault_cleared", "scenario": "bad-db-config",
        "fault_type": "bad_db_config", "started_at": started_at,
        "ended_at": ended_at, "duration_seconds": 0,
        "status": "recovered" if healthy else "recovery_timeout",
        "details": {},
    }
    get_event_logger().write(**event)
    return event


def run(duration: int = config.DEFAULT_DURATION_SECONDS) -> dict:
    """inject → wait → restore."""
    started_at = datetime.now(timezone.utc).isoformat()
    inject()
    log.info(f"[bad-db-config] Fault active for {duration} s…")
    time.sleep(duration)
    restore()
    ended_at = datetime.now(timezone.utc).isoformat()

    event = {
        "event": "scenario_completed", "scenario": "bad-db-config",
        "fault_type": "bad_db_config", "started_at": started_at,
        "ended_at": ended_at, "duration_seconds": duration, "status": "completed",
        "details": {},
    }
    get_event_logger().write(**event)
    return event
