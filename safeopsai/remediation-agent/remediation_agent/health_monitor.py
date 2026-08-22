"""
SafeOpsAI — Continuous Post-Remediation Health Monitor & Recovery Score
========================================================================
Probes multi-signal health (/health, /ready, error rate, p95 latency, DB availability)
and computes a deterministic, normalized Recovery Health Score (0.0 to 1.0):

  health_score = w_avail * availability + w_err * error_rate + w_lat * latency + w_dep * dependency

All weights and thresholds are configurable.
"""

import logging
import time
from typing import Any, Dict, Optional, Tuple

import httpx

from .config import cfg
from .models import RecoveryScore

log = logging.getLogger("remediation_agent.health_monitor")


class HealthMonitor:
    def __init__(
        self,
        backend_url: Optional[str] = None,
        prometheus_url: Optional[str] = None,
        mock_mode: bool = False,
        mock_scenario: Optional[str] = None,
    ) -> None:
        self.backend_url = backend_url or cfg.backend_url
        self.prometheus_url = prometheus_url or cfg.prometheus_url
        self.mock_mode = mock_mode
        self.mock_scenario = mock_scenario

    async def evaluate_recovery(self) -> Tuple[RecoveryScore, Dict[str, Any]]:
        """
        Probe production service signals and compute deterministic Recovery Health Score.
        """
        if self.mock_mode:
            return self._mock_evaluate_recovery()

        health_ok = False
        ready_ok = False
        error_rate = 0.0
        p95_latency = 0.1

        # Probe HTTP health and readiness
        try:
            async with httpx.AsyncClient(timeout=cfg.health_timeout_seconds) as client:
                res_h = await client.get(f"{self.backend_url}/health")
                health_ok = res_h.status_code == 200

                res_r = await client.get(f"{self.backend_url}/ready")
                ready_ok = res_r.status_code == 200
        except Exception as exc:
            log.debug("Health monitor probe exception: %s", exc)
            health_ok = False
            ready_ok = False

        # Probe Prometheus for error rate & latency
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                res_err = await client.get(
                    f"{self.prometheus_url}/api/v1/query",
                    params={"query": 'rate(http_requests_total{status_code=~"5.."}[1m])'},
                )
                if res_err.status_code == 200:
                    data = res_err.json().get("data", {}).get("result", [])
                    if data:
                        error_rate = float(data[0].get("value", [0, 0])[1])

                res_lat = await client.get(
                    f"{self.prometheus_url}/api/v1/query",
                    params={"query": 'histogram_quantile(0.95, rate(http_request_duration_seconds_bucket[1m]))'},
                )
                if res_lat.status_code == 200:
                    data = res_lat.json().get("data", {}).get("result", [])
                    if data:
                        p95_latency = float(data[0].get("value", [0, 0])[1])
        except Exception:
            pass

        return self.compute_score(health_ok, ready_ok, error_rate, p95_latency)

    def compute_score(
        self,
        health_ok: bool,
        ready_ok: bool,
        error_rate: float,
        p95_latency: float,
    ) -> Tuple[RecoveryScore, Dict[str, Any]]:
        """
        Normalize signals into [0, 1] range and calculate weighted recovery score.
        """
        avail_score = 1.0 if health_ok else 0.0
        dep_score = 1.0 if ready_ok else 0.0

        if error_rate <= 0.0:
            err_score = 1.0
        else:
            err_score = max(0.0, 1.0 - (error_rate / (cfg.max_acceptable_error_rate * 2)))

        if p95_latency <= 0.3:
            lat_score = 1.0
        else:
            lat_score = max(0.0, 1.0 - ((p95_latency - 0.3) / cfg.max_acceptable_latency))

        w = cfg.weights
        total_score = (
            w.get("availability", 0.30) * avail_score
            + w.get("error_rate", 0.25) * err_score
            + w.get("latency", 0.25) * lat_score
            + w.get("dependency", 0.20) * dep_score
        )

        # Severe metric violations cap maximum recovery score below failure threshold
        if not health_ok or not ready_ok or error_rate > cfg.max_acceptable_error_rate or p95_latency > cfg.max_acceptable_latency:
            total_score = min(total_score, 0.50)

        total_score = round(max(0.0, min(1.0, total_score)), 2)

        score_obj = RecoveryScore(
            availability=round(avail_score, 2),
            error_rate=round(err_score, 2),
            latency=round(lat_score, 2),
            dependency=round(dep_score, 2),
            recovery_score=total_score,
        )

        raw_metrics = {
            "health_ok": health_ok,
            "ready_ok": ready_ok,
            "error_rate": error_rate,
            "p95_latency": p95_latency,
        }

        return score_obj, raw_metrics

    def _mock_evaluate_recovery(self) -> Tuple[RecoveryScore, Dict[str, Any]]:
        """Mock health evaluation for unit testing."""
        sc = self.mock_scenario or ""
        if sc in ("health_failure", "rollback_failure"):
            return self.compute_score(health_ok=False, ready_ok=False, error_rate=0.1, p95_latency=0.5)
        elif sc == "high_error_rate":
            return self.compute_score(health_ok=True, ready_ok=True, error_rate=0.45, p95_latency=0.4)
        elif sc == "high_latency":
            return self.compute_score(health_ok=True, ready_ok=True, error_rate=0.01, p95_latency=3.5)
        elif sc == "db_failure":
            return self.compute_score(health_ok=True, ready_ok=False, error_rate=0.6, p95_latency=4.0)
        elif sc == "degraded":
            # Return degraded score (0.70)
            score_obj = RecoveryScore(availability=1.0, error_rate=0.5, latency=0.5, dependency=1.0, recovery_score=0.70)
            return score_obj, {"mock": "degraded"}
        else:
            return self.compute_score(health_ok=True, ready_ok=True, error_rate=0.01, p95_latency=0.2)
