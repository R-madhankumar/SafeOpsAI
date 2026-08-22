"""
Incident Agent — Prometheus HTTP API Client
============================================
Thin, async wrapper around the Prometheus instant-query API.
Uses httpx.AsyncClient — no blocking calls.

All queries return float | None.
  float  — the scalar value
  None   — query returned no result, or Prometheus is unavailable

No exceptions propagate from public methods; callers always get a value
or None, plus the caller decides how to handle absence of data.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from .config import rules
from .models import MetricsSnapshot

log = logging.getLogger("incident_agent.prometheus")


class PrometheusClient:
    """
    Async Prometheus instant-query client.

    Parameters
    ----------
    base_url : Prometheus base URL, e.g. http://prometheus:9090
    timeout  : per-request timeout in seconds
    """

    def __init__(self, base_url: str, timeout: float | None = None) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout or rules.prometheus_timeout
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self._base,
                timeout=self._timeout,
                headers={"User-Agent": "SafeOpsAI-IncidentAgent/1.0"},
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    # ── Core query method ─────────────────────────────────────────────────

    async def query(self, expr: str) -> float | None:
        """
        Execute a Prometheus instant query.

        Returns the first scalar result as float, or None on any error.
        """
        try:
            client = await self._get_client()
            resp = await client.get(
                "/api/v1/query",
                params={"query": expr},
            )
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()

            results = data.get("data", {}).get("result", [])
            if not results:
                return None

            # Instant queries return [{metric:{...}, value:[timestamp, "value"]}]
            raw_value = results[0].get("value", [None, None])[1]
            if raw_value is None:
                return None

            val = float(raw_value)
            # Prometheus returns NaN for absent histograms etc.
            import math
            return None if math.isnan(val) or math.isinf(val) else val

        except httpx.TimeoutException:
            log.warning("Prometheus query timed out: %s", expr[:80])
            return None
        except httpx.HTTPStatusError as exc:
            log.warning("Prometheus HTTP error %s for query: %s", exc.response.status_code, expr[:80])
            return None
        except Exception as exc:
            log.warning("Prometheus query error (%s): %s", type(exc).__name__, exc)
            return None

    # ── Derived metric helpers ────────────────────────────────────────────

    async def backend_up(self) -> float | None:
        """up{job="safeops-backend"} — 1=up, 0=down"""
        return await self.query('up{job="safeops-backend"}')

    async def database_up(self) -> float | None:
        """up{job="postgres"} — 1=up, 0=down"""
        return await self.query('up{job="postgres"}')

    async def error_rate(self) -> float | None:
        """rate(application_errors_total[1m]) — errors per second"""
        return await self.query("rate(application_errors_total[1m])")

    async def request_rate(self) -> float | None:
        """sum(rate(http_requests_total[1m])) — requests per second"""
        return await self.query("sum(rate(http_requests_total[1m]))")

    async def ratio_5xx(self) -> float | None:
        """
        5xx requests / total requests (last 1 min).
        Returns None if total is 0 (division by zero guard in PromQL).
        """
        return await self.query(
            'rate(http_requests_total{status_code=~"5.."}[1m])'
            " / rate(http_requests_total[1m])"
        )

    async def p95_request_latency(self) -> float | None:
        """histogram_quantile(0.95, rate(http_request_duration_seconds_bucket[2m]))"""
        return await self.query(
            "histogram_quantile(0.95,"
            " rate(http_request_duration_seconds_bucket[2m]))"
        )

    async def p95_db_latency(self) -> float | None:
        """histogram_quantile(0.95, rate(db_query_duration_seconds_bucket[2m]))"""
        return await self.query(
            "histogram_quantile(0.95,"
            " rate(db_query_duration_seconds_bucket[2m]))"
        )

    # ── Snapshot (all metrics in one go) ─────────────────────────────────

    async def snapshot(self) -> MetricsSnapshot:
        """
        Fetch all monitored metrics concurrently and return a MetricsSnapshot.
        Individual failures return None without blocking the rest.
        """
        import asyncio
        (
            b_up, db_up, err_rate, req_rate,
            p95_req, p95_db, ratio
        ) = await asyncio.gather(
            self.backend_up(),
            self.database_up(),
            self.error_rate(),
            self.request_rate(),
            self.p95_request_latency(),
            self.p95_db_latency(),
            self.ratio_5xx(),
            return_exceptions=False,
        )
        return MetricsSnapshot(
            backend_up=b_up,
            database_up=db_up,
            error_rate=err_rate,
            request_rate=req_rate,
            p95_request_latency=p95_req,
            p95_db_latency=p95_db,
            ratio_5xx=ratio,
        )

    # ── Health check ──────────────────────────────────────────────────────

    async def is_healthy(self) -> bool:
        """Return True if Prometheus /-/healthy returns 200."""
        try:
            client = await self._get_client()
            r = await client.get("/-/healthy")
            return r.status_code == 200
        except Exception:
            return False
