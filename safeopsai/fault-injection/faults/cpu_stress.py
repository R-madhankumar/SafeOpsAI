"""
Fault: CPU Stress
==================
Runs `stress-ng` inside the safeops-backend container for a configurable
duration and worker count.  stress-ng is pre-installed in the backend
image (see backend/Dockerfile).

Expected Prometheus effect:
  - container CPU usage spikes (visible in Grafana node/cadvisor metrics
    if cAdvisor is added; the backend process itself will slow under load)
  - http_request_duration_seconds increases under CPU pressure
  - HighRequestLatency alert may fire if request processing stalls

Recovery:
  - stress-ng exits automatically after --timeout elapses
  - OR call interrupt() to send SIGTERM to the exec process
"""

import threading
import time
from datetime import datetime, timezone

import docker  # type: ignore
from docker.errors import NotFound, APIError  # type: ignore

import config
from logger import log, GREEN, RED, YELLOW, get_event_logger

_stress_thread: threading.Thread | None = None
_stop_event = threading.Event()


# ── Helpers ───────────────────────────────────────────────────────────────

def _client() -> docker.DockerClient:
    return docker.from_env()


def _get_container(name: str):
    if name not in config.ALLOWED_CONTAINERS:
        raise ValueError(
            f"Safety check: '{name}' not in ALLOWED_CONTAINERS."
        )
    try:
        return _client().containers.get(name)
    except NotFound:
        raise RuntimeError(
            f"Container '{name}' not found. Is the stack running?"
        )


# ── Public API ────────────────────────────────────────────────────────────

def inject(
    container_name: str = config.BACKEND_CONTAINER,
    workers: int = config.DEFAULT_CPU_WORKERS,
    duration: int = config.DEFAULT_DURATION_SECONDS,
) -> dict:
    """
    Run stress-ng in the backend container.

    This is blocking — it returns only after `duration` seconds or
    when the stress-ng process exits.  Run in a thread from the CLI
    if you need to do other work concurrently.

    Parameters
    ----------
    container_name : target container (must be safeops-backend)
    workers        : number of CPU stress worker processes
    duration       : seconds to run stress-ng
    """
    started_at = datetime.now(timezone.utc).isoformat()

    if container_name != config.BACKEND_CONTAINER:
        raise ValueError(
            f"cpu-stress only targets '{config.BACKEND_CONTAINER}', "
            f"not '{container_name}'. "
            "Running stress-ng in the database container would risk data loss."
        )

    container = _get_container(container_name)

    if container.status != "running":
        raise RuntimeError(
            f"Container '{container_name}' is not running (status={container.status}). "
            "Start the stack first."
        )

    cmd = [
        "stress-ng",
        "--cpu",    str(workers),
        "--timeout", f"{duration}s",
        "--metrics-brief",   # print a brief metrics summary on exit
        "--verbose",
    ]

    log.info(YELLOW(f"[cpu-stress] Executing in '{container_name}':"))
    log.info(f"  Command : {' '.join(cmd)}")
    log.info(f"  Workers : {workers}")
    log.info(f"  Duration: {duration}s")

    try:
        # exec_run blocks until the command finishes
        exit_code, output = container.exec_run(
            cmd=cmd,
            stdout=True,
            stderr=True,
            stream=False,
            demux=False,
        )
        output_text = output.decode("utf-8", errors="replace") if output else ""
        if exit_code == 0:
            log.info(GREEN(f"[cpu-stress] stress-ng completed normally (exit 0)."))
        else:
            log.warning(RED(f"[cpu-stress] stress-ng exited with code {exit_code}."))

        if output_text.strip():
            for line in output_text.strip().splitlines()[-8:]:  # last 8 lines
                log.info(f"  stress-ng: {line}")

    except APIError as exc:
        raise RuntimeError(f"Docker exec failed: {exc}")

    ended_at = datetime.now(timezone.utc).isoformat()
    event = {
        "event":            "fault_cleared",
        "scenario":         "cpu-stress",
        "fault_type":       "cpu_stress",
        "started_at":       started_at,
        "ended_at":         ended_at,
        "duration_seconds": duration,
        "status":           "completed",
        "details":          {
            "container": container_name,
            "workers":   workers,
            "duration":  duration,
            "exit_code": exit_code,
        },
    }
    get_event_logger().write(**event)
    return event


def inject_background(
    container_name: str = config.BACKEND_CONTAINER,
    workers: int = config.DEFAULT_CPU_WORKERS,
    duration: int = config.DEFAULT_DURATION_SECONDS,
) -> threading.Thread:
    """
    Run cpu stress in a background thread so the caller can do other
    work (e.g. generate traffic) while stress is active.
    """
    t = threading.Thread(
        target=inject,
        kwargs={"container_name": container_name, "workers": workers, "duration": duration},
        daemon=True,
        name="cpu-stress-thread",
    )
    t.start()
    log.info(f"[cpu-stress] Background stress thread started (tid={t.ident}).")
    return t
