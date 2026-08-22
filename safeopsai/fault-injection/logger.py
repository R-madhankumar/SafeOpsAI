"""
Structured event logger for the fault injection framework.

Writes JSONL (one JSON object per line) to a log file AND prints
human-readable messages to stdout.

Each event record has the shape expected by the evaluation harness:

{
  "event":            "fault_started" | "fault_cleared" | "scenario_completed" | ...,
  "scenario":         "slow-db",
  "fault_type":       "slow_queries",
  "started_at":       "2026-08-13T10:00:00.000Z",
  "ended_at":         "2026-08-13T10:01:00.000Z",
  "duration_seconds": 60,
  "status":           "completed" | "failed" | "aborted",
  "details":          { ... }  # optional extra context
}
"""

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ── Console logger ────────────────────────────────────────────────────────
_FMT = "%(asctime)s  %(levelname)-8s  %(message)s"
_DATE_FMT = "%H:%M:%S"

logging.basicConfig(
    level=logging.INFO,
    format=_FMT,
    datefmt=_DATE_FMT,
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("safeopsai.injector")

# Silence noisy third-party loggers
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("docker").setLevel(logging.WARNING)


# ── ANSI colours (auto-disabled when not a TTY) ───────────────────────────
def _colour(code: str, text: str) -> str:
    if sys.stdout.isatty():
        return f"\033[{code}m{text}\033[0m"
    return text


GREEN  = lambda t: _colour("32", t)
YELLOW = lambda t: _colour("33", t)
RED    = lambda t: _colour("31", t)
CYAN   = lambda t: _colour("36", t)
BOLD   = lambda t: _colour("1",  t)


# ── JSONL event log ───────────────────────────────────────────────────────
class EventLogger:
    """Writes structured JSONL events to a file for later analysis."""

    def __init__(self, path: str) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def _ts(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def write(
        self,
        event: str,
        scenario: str = "",
        fault_type: str = "",
        started_at: str = "",
        ended_at: str = "",
        duration_seconds: float = 0,
        status: str = "",
        details: dict[str, Any] | None = None,
    ) -> None:
        record: dict[str, Any] = {
            "event":            event,
            "scenario":         scenario,
            "fault_type":       fault_type,
            "started_at":       started_at or self._ts(),
            "ended_at":         ended_at,
            "duration_seconds": round(duration_seconds, 2),
            "status":           status,
        }
        if details:
            record["details"] = details
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")


# ── Module-level helpers ──────────────────────────────────────────────────
def _get_log_path() -> str:
    from config import EVENT_LOG_PATH
    return EVENT_LOG_PATH


_event_logger: EventLogger | None = None


def get_event_logger() -> EventLogger:
    global _event_logger
    if _event_logger is None:
        _event_logger = EventLogger(_get_log_path())
    return _event_logger


def section(title: str) -> None:
    width = 52
    log.info(BOLD(f"\n{'═' * width}"))
    log.info(BOLD(f"  {title}"))
    log.info(BOLD(f"{'═' * width}"))


def step(n: int, total: int, label: str, status: str = "") -> None:
    tag = GREEN(f"[{status}]") if status == "PASS" else \
          RED(f"[{status}]") if status in ("FAIL", "ERROR") else \
          YELLOW(f"[{status}]") if status else ""
    msg = f"  [{n}/{total}] {label}"
    if tag:
        msg = f"{msg} ... {tag}"
    log.info(msg)
