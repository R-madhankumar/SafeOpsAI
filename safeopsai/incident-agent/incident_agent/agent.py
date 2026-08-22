"""
Incident Agent — Main Polling Loop
=====================================
Orchestrates:
  PrometheusClient  → MetricsSnapshot
  RuleBasedDetector → OpenResult / ResolveResult
  DB layer          → INSERT / UPDATE incidents

The loop runs as an asyncio task alongside the FastAPI server.
It handles all errors gracefully — Prometheus down, DB down, and
malformed responses all produce logged warnings, not crashes.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

from prometheus_client import Counter, Gauge, Histogram

from .config import PROMETHEUS_URL, rules
from .db import init_pool, close_pool, insert_incident, resolve_by_fingerprint, get_open_incidents
from .detector import RuleBasedDetector
from .models import MetricsSnapshot
from .prometheus_client import PrometheusClient

log = logging.getLogger("incident_agent.agent")

# ── Self-monitoring Prometheus metrics ────────────────────────────────────
_polls_total = Counter(
    "incident_agent_polls_total",
    "Total number of Prometheus polling cycles",
)
_poll_errors_total = Counter(
    "incident_agent_poll_errors_total",
    "Polling cycles that failed (Prometheus or DB error)",
)
_incidents_detected = Counter(
    "incidents_detected_total",
    "Total incidents detected by the agent",
    ["incident_type", "severity"],
)
_incidents_resolved = Counter(
    "incidents_resolved_total",
    "Total incidents resolved by the agent",
    ["incident_type"],
)
_active_incidents = Gauge(
    "active_incidents",
    "Currently open incidents tracked by the agent",
)
_detection_latency = Histogram(
    "incident_detection_latency_seconds",
    "Time between condition first seen and incident opened",
    buckets=[5, 10, 15, 20, 30, 45, 60, 90, 120],
)


# ── Agent state (shared with the API) ────────────────────────────────────

class AgentState:
    """Mutable state exposed to the /status API endpoint."""
    def __init__(self) -> None:
        self.started_at:          str   = datetime.now(timezone.utc).isoformat()
        self.last_poll_at:        str   = ""
        self.last_poll_ok:        bool  = False
        self.prometheus_healthy:  bool  = False
        self.total_polls:         int   = 0
        self.total_errors:        int   = 0
        self.active_fingerprints: list[str] = []
        self.rule_count:          int   = 0
        self.db_connected:        bool  = False


state = AgentState()


# ── Polling loop ──────────────────────────────────────────────────────────

async def run_agent() -> None:
    """
    Initialise DB + Prometheus client, then run the polling loop forever.
    Designed to be started as an asyncio task.
    """
    log.info("Incident Agent starting…")

    # DB init with retry
    try:
        await init_pool()
        state.db_connected = True
    except RuntimeError as exc:
        log.error("DB init failed: %s — proceeding without DB (incidents will not be stored)", exc)

    prom  = PrometheusClient(base_url=PROMETHEUS_URL, timeout=rules.prometheus_timeout)
    det   = RuleBasedDetector()
    state.rule_count = len(rules.all_rule_names)

    log.info(
        "Polling Prometheus at %s every %.0fs — %d rules loaded",
        PROMETHEUS_URL, rules.poll_interval, state.rule_count,
    )

    try:
        while True:
            cycle_start = time.monotonic()
            await _poll_cycle(prom, det)
            # Sleep for the remainder of the interval
            elapsed = time.monotonic() - cycle_start
            sleep_for = max(0.0, rules.poll_interval - elapsed)
            await asyncio.sleep(sleep_for)
    except asyncio.CancelledError:
        log.info("Polling loop cancelled — shutting down")
    finally:
        await prom.close()
        await close_pool()


async def _poll_cycle(prom: PrometheusClient, det: RuleBasedDetector) -> None:
    """Single polling cycle: snapshot → evaluate → act."""
    _polls_total.inc()
    state.total_polls += 1

    try:
        # ── 1. Health-check Prometheus ────────────────────────────────────
        state.prometheus_healthy = await prom.is_healthy()

        # ── 2. Fetch metrics snapshot ─────────────────────────────────────
        snap: MetricsSnapshot = await prom.snapshot()

        # ── 3. Evaluate rules ─────────────────────────────────────────────
        opens, resolves = det.evaluate(snap)

        # ── 4. Open new incidents ─────────────────────────────────────────
        for open_result in opens:
            inc = open_result.incident
            fingerprint = inc.fingerprint
            if not state.db_connected:
                log.warning(
                    "DB unavailable — incident not stored: %s", fingerprint
                )
                continue
            try:
                db_id = await insert_incident(inc)
                if db_id > 0:
                    det.mark_opened(fingerprint, db_id)
                    _incidents_detected.labels(
                        incident_type=inc.incident_type.value,
                        severity=inc.severity.value,
                    ).inc()
                    # Record detection latency (time from first seen to opened)
                    # We use the for_seconds as a proxy for minimum detection lag
                    _detection_latency.observe(rules.rule_for_seconds(
                        inc.incident_type.value.lower().replace("_", "_")  # same key
                    ))
            except Exception as exc:
                log.error("Failed to insert incident %s: %s", fingerprint, exc)
                _poll_errors_total.inc()

        # ── 5. Resolve cleared incidents ──────────────────────────────────
        for res in resolves:
            fp = res.fingerprint
            if not state.db_connected:
                det.mark_resolved(fp)
                continue
            try:
                resolved_id = await resolve_by_fingerprint(fp)
                det.mark_resolved(fp)
                if resolved_id:
                    # Derive incident_type from fingerprint for metric label
                    it_str = fp.split(":")[0] if ":" in fp else fp
                    _incidents_resolved.labels(incident_type=it_str).inc()
            except Exception as exc:
                log.error("Failed to resolve incident %s: %s", fp, exc)
                _poll_errors_total.inc()

        # ── 6. Update shared state ────────────────────────────────────────
        state.last_poll_at        = datetime.now(timezone.utc).isoformat()
        state.last_poll_ok        = True
        state.active_fingerprints = det.active_fingerprints()
        _active_incidents.set(det.active_count())

    except asyncio.CancelledError:
        raise
    except Exception as exc:
        log.error("Poll cycle error: %s", exc, exc_info=True)
        _poll_errors_total.inc()
        state.last_poll_ok  = False
        state.total_errors += 1
