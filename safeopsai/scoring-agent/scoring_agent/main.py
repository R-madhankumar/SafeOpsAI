"""
SafeOpsAI — Scoring Agent: Entry Point
========================================
Starts two concurrent asyncio tasks:
  1. FastAPI HTTP server  (uvicorn)
  2. Scoring polling loop (agent.run_agent)

Both share the same event loop.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys

import uvicorn

from .agent import run_agent
from .api import app
from .config import API_HOST, API_PORT, LOG_LEVEL

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
for noisy in ("asyncpg", "uvicorn.access"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

log = logging.getLogger("scoring_agent.main")

_shutdown = asyncio.Event()


def _handle_signal(sig: int) -> None:
    log.info("Signal %s — shutting down", signal.Signals(sig).name)
    _shutdown.set()


async def _main() -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _handle_signal, sig)
        except NotImplementedError:
            pass  # Windows

    polling_task = asyncio.create_task(run_agent(), name="scoring-polling")

    config = uvicorn.Config(
        app=app,
        host=API_HOST,
        port=API_PORT,
        log_level=LOG_LEVEL.lower(),
        access_log=False,
    )
    server = uvicorn.Server(config)
    server_task = asyncio.create_task(server.serve(), name="scoring-api")

    log.info("Scoring Agent running — API on http://%s:%s", API_HOST, API_PORT)

    done, pending = await asyncio.wait(
        [polling_task, server_task, asyncio.create_task(_shutdown.wait())],
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