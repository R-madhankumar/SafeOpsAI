"""
Root Cause Agent — Evidence Collector
=======================================
Gathers all observability data for a single incident before the LLM call:
  1. Current Prometheus metrics (fresh snapshot)
  2. Historical metric windows (min/max/avg over N minutes)
  3. Service topology context from topology.yml

Returns a structured Evidence object ready for prompt_builder.py.
"""

from __future__ import annotations

import asyncio
import logging
import math
from typing import Any

import httpx

from .config import cfg
from .models import Evidence, MetricHistory, MetricPoint, RCARequest

log = logging.getLogger("rca.evidence")


# ── Prometheus helpers ─────────────────────────────────────────────────────

async def _instant_query(client: httpx.AsyncClient, expr: str) -> float | None:
    """Single Prometheus instant query → float | None."""
    try:
        r = await client.get(
            "/api/v1/query",
            params={"query": expr},
            timeout=cfg.prom_timeout,
        )
        r.raise_for_status()
        results = r.json().get("data", {}).get("result", [])
        if not results:
            return None
        raw = results[0].get("value", [None, None])[1]
        if raw is None:
            return None
        val = float(raw)
        return None if (math.isnan(val) or math.isinf(val)) else val
    except Exception as exc:
        log.debug("Prometheus query failed (%s): %s", expr[:60], exc)
        return None


async def _range_stats(
    client: httpx.AsyncClient,
    expr: str,
    window_minutes: int,
) -> tuple[float | None, float | None, float | None]:
    """
    Query a metric over a time range and return (min, max, avg).
    Uses range_query + inline aggregation via PromQL avg_over_time / max_over_time.
    """
    window = f"{window_minutes}m"
    min_v = await _instant_query(client, f"min_over_time(({expr})[{window}:])")
    max_v = await _instant_query(client, f"max_over_time(({expr})[{window}:])")
    avg_v = await _instant_query(client, f"avg_over_time(({expr})[{window}:])")
    return min_v, max_v, avg_v


# ── Current metrics snapshot ───────────────────────────────────────────────

_CURRENT_METRICS = [
    ("backend_up",         'up{job="safeops-backend"}',               "",     "1=up 0=down"),
    ("database_up",        'up{job="postgres"}',                       "",     "1=up 0=down"),
    ("error_rate",         "rate(application_errors_total[1m])",       "err/s",""),
    ("request_rate",       "sum(rate(http_requests_total[1m]))",        "req/s",""),
    ("p95_request_latency","histogram_quantile(0.95, rate(http_request_duration_seconds_bucket[2m]))", "s", "P95"),
    ("p95_db_latency",     "histogram_quantile(0.95, rate(db_query_duration_seconds_bucket[2m]))",    "s", "P95"),
    ("ratio_5xx",
     'rate(http_requests_total{status_code=~"5.."}[1m]) / rate(http_requests_total[1m])',
     "fraction", ""),
    ("db_connections",     "db_active_connections",                     "conn",""),
]

_HISTORY_METRICS = [
    ("error_rate",          "rate(application_errors_total[1m])"),
    ("p95_request_latency", "histogram_quantile(0.95, rate(http_request_duration_seconds_bucket[2m]))"),
    ("p95_db_latency",      "histogram_quantile(0.95, rate(db_query_duration_seconds_bucket[2m]))"),
    ("ratio_5xx",           'rate(http_requests_total{status_code=~"5.."}[1m]) / rate(http_requests_total[1m])'),
]


async def collect(request: RCARequest) -> Evidence:
    """
    Collect all evidence for the given RCA request.
    Prometheus failures are swallowed — the agent falls back on the stored snapshot.
    """
    ev = Evidence(
        incident_id         = request.incident_id,
        incident_type       = request.incident_type,
        service             = request.service,
        detection_snapshot  = request.metrics_snapshot,
    )

    # ── Prometheus ────────────────────────────────────────────────────────
    try:
        async with httpx.AsyncClient(
            base_url=cfg.prom_url,
            timeout=cfg.prom_timeout,
            headers={"User-Agent": "SafeOpsAI-RCA/1.0"},
        ) as client:
            # Concurrent current-metric queries
            results = await asyncio.gather(
                *[_instant_query(client, expr) for _, expr, _, _ in _CURRENT_METRICS],
                return_exceptions=True,
            )
            for (name, _, unit, note), val in zip(_CURRENT_METRICS, results):
                if isinstance(val, Exception):
                    val = None
                ev.current_metrics.append(MetricPoint(name=name, value=val, unit=unit, note=note))

            # Concurrent history queries
            window = cfg.history_window_minutes
            hist_results = await asyncio.gather(
                *[_range_stats(client, expr, window) for _, expr in _HISTORY_METRICS],
                return_exceptions=True,
            )
            current_by_name = {m.name: m.value for m in ev.current_metrics}
            for (name, _), stats in zip(_HISTORY_METRICS, hist_results):
                if isinstance(stats, Exception):
                    stats = (None, None, None)
                mn, mx, avg = stats
                ev.metric_history.append(MetricHistory(
                    metric_name     = name,
                    window_minutes  = window,
                    min_val         = mn,
                    max_val         = mx,
                    avg_val         = avg,
                    current         = current_by_name.get(name),
                ))
    except Exception as exc:
        log.warning("Prometheus evidence collection failed: %s — using stored snapshot only", exc)
        # Fall back: populate current_metrics from stored detection snapshot
        for k, v in request.metrics_snapshot.items():
            if isinstance(v, (int, float)):
                ev.current_metrics.append(MetricPoint(name=k, value=float(v)))

    # ── Topology ──────────────────────────────────────────────────────────
    if cfg.include_topology:
        ev.service_deps     = _get_deps(request.service)
        ev.cascade_patterns = _get_cascade_hints(request.incident_type)

    return ev


# ── Topology helpers (reads topology.yml loaded in config.py) ─────────────

def _load_topology() -> dict[str, Any]:
    try:
        import yaml
        from .config import TOPOLOGY_FILE
        if TOPOLOGY_FILE.exists():
            with TOPOLOGY_FILE.open() as fh:
                return yaml.safe_load(fh) or {}
    except Exception:
        pass
    return {}


def _get_deps(service: str) -> list[str]:
    topo = _load_topology()
    return topo.get("services", {}).get(service, {}).get("depends_on", [])


def _get_cascade_hints(incident_type: str) -> list[str]:
    topo = _load_topology()
    hints = []
    for pattern_name, pattern in topo.get("cascade_patterns", {}).items():
        symptoms = pattern.get("likely_symptoms", [])
        if incident_type in symptoms:
            root = pattern.get("root", "")
            primary = pattern.get("primary_indicator", "")
            hints.append(
                f"Pattern '{pattern_name}': root={root}, "
                f"primary_indicator={primary}, symptoms={symptoms}"
            )
    return hints
