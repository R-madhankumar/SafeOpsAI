"""
Incident Agent — Entry Point
==============================
Starts two concurrent asyncio tasks:
  1. The FastAPI HTTP server (uvicorn)
  2. The polling loop (agent.run_agent)

Both share the same event loop. The polling loop is a background task;
if it crashes it will be logged but will not take down the API server.
The API is used for liveness/readiness probes by Docker and Kubernetes.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys

import uvicorn

from .config import API_HOST, API_PORT, LOG_LEVEL
from .agent import run_agent
from .api import app

# ── Logging setup ─────────────────────────────────────────────────────────
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
# Reduce noise from third-party loggers
for noisy in ("httpx", "asyncpg", "uvicorn.access"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

log = logging.getLogger("incident_agent.main")


# ── Graceful shutdown ─────────────────────────────────────────────────────

_shutdown_event = asyncio.Event()


def _handle_signal(sig: int) -> None:
    log.info("Received signal %s — initiating shutdown", signal.Signals(sig).name)
    _shutdown_event.set()


# ── Main ──────────────────────────────────────────────────────────────────

async def _main() -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _handle_signal, sig)
        except NotImplementedError:
            # Windows — signal handlers via asyncio not supported
            pass

    # Start the polling loop as a background task
    polling_task = asyncio.create_task(run_agent(), name="polling-loop")

    # Configure and start uvicorn inline (non-blocking)
    config = uvicorn.Config(
        app=app,
        host=API_HOST,
        port=API_PORT,
        log_level=LOG_LEVEL.lower(),
        access_log=False,  # we handle our own request logging
    )
    server = uvicorn.Server(config)
    server_task = asyncio.create_task(server.serve(), name="api-server")

    log.info("Incident Agent running — API on http://%s:%s", API_HOST, API_PORT)

    # Wait for shutdown signal or either task to fail
    done, pending = await asyncio.wait(
        [polling_task, server_task, asyncio.create_task(_shutdown_event.wait())],
        return_when=asyncio.FIRST_COMPLETED,
    )

    log.info("Shutting down…")
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)


def main() -> None:
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
