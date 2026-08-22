"""
Traffic Generator
==================
Sends a configurable stream of HTTP requests to the SafeOpsAI backend.
Records per-request status codes and latencies, then prints a summary.

Used by the scenario runner to ensure Prometheus alert thresholds are
crossed (many alerts require *sustained traffic*, not just a fault toggle).

Usage (standalone):
    python traffic/generator.py --requests 120 --interval 0.5

Usage (from Python):
    from traffic.generator import TrafficGenerator
    gen = TrafficGenerator(base_url="http://localhost:8000")
    summary = gen.run(requests=120, interval=0.5)
    print(summary)
"""

import statistics
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterator

import requests as req  # type: ignore

import config
from logger import log, GREEN, RED, YELLOW, CYAN


# ── Data classes ─────────────────────────────────────────────────────────

@dataclass
class RequestResult:
    url:         str
    method:      str
    status_code: int
    latency_s:   float
    error:       str = ""


@dataclass
class TrafficSummary:
    total:          int = 0
    successful:     int = 0
    failed:         int = 0
    avg_latency_s:  float = 0.0
    p50_latency_s:  float = 0.0
    p95_latency_s:  float = 0.0
    max_latency_s:  float = 0.0
    error_rate_pct: float = 0.0
    duration_s:     float = 0.0
    started_at:     str = ""
    ended_at:       str = ""
    results:        list[RequestResult] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "total":          self.total,
            "successful":     self.successful,
            "failed":         self.failed,
            "avg_latency_s":  round(self.avg_latency_s, 3),
            "p50_latency_s":  round(self.p50_latency_s, 3),
            "p95_latency_s":  round(self.p95_latency_s, 3),
            "max_latency_s":  round(self.max_latency_s, 3),
            "error_rate_pct": round(self.error_rate_pct, 1),
            "duration_s":     round(self.duration_s, 2),
            "started_at":     self.started_at,
            "ended_at":       self.ended_at,
        }

    def print_summary(self) -> None:
        ok_colour = GREEN if self.error_rate_pct < 10 else YELLOW if self.error_rate_pct < 50 else RED
        log.info(CYAN("─" * 52))
        log.info(CYAN("  Traffic Generator Summary"))
        log.info(CYAN("─" * 52))
        log.info(f"  Total requests  : {self.total}")
        log.info(f"  Successful      : {GREEN(str(self.successful))}")
        log.info(f"  Failed          : {RED(str(self.failed))}")
        log.info(f"  Error rate      : {ok_colour(f'{self.error_rate_pct:.1f}%')}")
        log.info(f"  Avg latency     : {self.avg_latency_s:.3f}s")
        log.info(f"  P50 latency     : {self.p50_latency_s:.3f}s")
        log.info(f"  P95 latency     : {self.p95_latency_s:.3f}s")
        log.info(f"  Max latency     : {self.max_latency_s:.3f}s")
        log.info(f"  Wall time       : {self.duration_s:.2f}s")
        log.info(CYAN("─" * 52))


# ── Endpoint catalogue ────────────────────────────────────────────────────

def _endpoints(base_url: str) -> list[tuple[str, str, dict | None]]:
    """
    Returns a rotating list of (method, url, json_body) tuples.
    Mix of read and write operations to generate realistic traffic.
    """
    return [
        ("GET",  f"{base_url}/health",  None),
        ("GET",  f"{base_url}/items",   None),
        ("GET",  f"{base_url}/health",  None),
        ("GET",  f"{base_url}/stats",   None),
        ("GET",  f"{base_url}/items",   None),
        ("POST", f"{base_url}/items",   {"name": "traffic-test", "description": "auto-generated", "value": 0.01}),
        ("GET",  f"{base_url}/health",  None),
        ("GET",  f"{base_url}/items",   None),
    ]


# ── Core generator ────────────────────────────────────────────────────────

class TrafficGenerator:
    """
    Sends HTTP requests to the SafeOpsAI backend at a configurable rate.
    Thread-safe: the `stop()` method can be called from another thread.
    """

    def __init__(self, base_url: str = config.BACKEND_URL) -> None:
        self._base_url = base_url.rstrip("/")
        self._stop_event = threading.Event()
        self._session = req.Session()
        self._session.headers.update({"User-Agent": "SafeOpsAI-TrafficGen/1.0"})

    def stop(self) -> None:
        """Signal the generator to stop after the current request."""
        self._stop_event.set()

    def _send_one(self, method: str, url: str, body: dict | None) -> RequestResult:
        t0 = time.monotonic()
        try:
            if method == "GET":
                r = self._session.get(url, timeout=(config.HTTP_CONNECT_TIMEOUT, config.HTTP_READ_TIMEOUT))
            else:
                r = self._session.post(url, json=body, timeout=(config.HTTP_CONNECT_TIMEOUT, config.HTTP_READ_TIMEOUT))
            return RequestResult(
                url=url, method=method,
                status_code=r.status_code,
                latency_s=time.monotonic() - t0,
            )
        except req.exceptions.Timeout:
            return RequestResult(url=url, method=method, status_code=0,
                                 latency_s=time.monotonic() - t0, error="timeout")
        except req.exceptions.ConnectionError:
            return RequestResult(url=url, method=method, status_code=0,
                                 latency_s=time.monotonic() - t0, error="connection_error")
        except Exception as exc:
            return RequestResult(url=url, method=method, status_code=0,
                                 latency_s=time.monotonic() - t0, error=str(exc))

    def run(
        self,
        n_requests: int = config.DEFAULT_TRAFFIC_REQUESTS,
        interval:   float = config.DEFAULT_TRAFFIC_INTERVAL,
        verbose:    bool = False,
    ) -> TrafficSummary:
        """
        Send `n_requests` requests at `interval` seconds apart.

        Parameters
        ----------
        n_requests : total number of requests to send
        interval   : seconds to wait between requests (0 = as fast as possible)
        verbose    : print each request result to stdout

        Returns
        -------
        TrafficSummary  with per-request results and aggregate stats
        """
        self._stop_event.clear()
        endpoints = _endpoints(self._base_url)
        n_ep = len(endpoints)
        results: list[RequestResult] = []
        started_at = datetime.now(timezone.utc).isoformat()
        wall_start = time.monotonic()

        log.info(CYAN(f"[traffic] Starting: {n_requests} requests @ {interval}s interval → {self._base_url}"))

        for i in range(n_requests):
            if self._stop_event.is_set():
                log.info("[traffic] Stop signal received — ending early.")
                break

            method, url, body = endpoints[i % n_ep]
            result = self._send_one(method, url, body)
            results.append(result)

            if verbose:
                status_str = GREEN(str(result.status_code)) if result.status_code < 400 else RED(str(result.status_code))
                log.info(f"  [{i+1:>4}/{n_requests}] {method:4} {url} → {status_str}  ({result.latency_s:.3f}s)")
            elif (i + 1) % 20 == 0:
                done_pct = (i + 1) / n_requests * 100
                log.info(f"  [traffic] {i+1}/{n_requests} requests sent ({done_pct:.0f}%)…")

            if interval > 0:
                time.sleep(interval)

        wall_end = time.monotonic()
        ended_at = datetime.now(timezone.utc).isoformat()

        # ── Compute summary ───────────────────────────────────────────────
        successful = sum(1 for r in results if 0 < r.status_code < 400)
        failed     = len(results) - successful
        latencies  = [r.latency_s for r in results if r.latency_s > 0]

        summary = TrafficSummary(
            total          = len(results),
            successful     = successful,
            failed         = failed,
            avg_latency_s  = statistics.mean(latencies) if latencies else 0,
            p50_latency_s  = statistics.median(latencies) if latencies else 0,
            p95_latency_s  = (sorted(latencies)[int(len(latencies) * 0.95)] if len(latencies) >= 2 else (latencies[0] if latencies else 0)),
            max_latency_s  = max(latencies) if latencies else 0,
            error_rate_pct = (failed / len(results) * 100) if results else 0,
            duration_s     = wall_end - wall_start,
            started_at     = started_at,
            ended_at       = ended_at,
            results        = results,
        )
        return summary

    def run_background(
        self,
        n_requests: int = config.DEFAULT_TRAFFIC_REQUESTS,
        interval:   float = config.DEFAULT_TRAFFIC_INTERVAL,
    ) -> threading.Thread:
        """
        Run traffic generation in a background thread.
        Call stop() to end it early; join the thread to wait for completion.
        """
        self._summary: TrafficSummary | None = None

        def _worker():
            self._summary = self.run(n_requests=n_requests, interval=interval)

        t = threading.Thread(target=_worker, daemon=True, name="traffic-generator")
        t.start()
        log.info(f"[traffic] Background generator started (tid={t.ident}).")
        return t

    @property
    def last_summary(self) -> "TrafficSummary | None":
        return getattr(self, "_summary", None)


# ── Standalone entry point ────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse, sys

    # Ensure the parent dir is on sys.path when run directly
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    parser = argparse.ArgumentParser(description="SafeOpsAI traffic generator")
    parser.add_argument("--url",      default=config.BACKEND_URL, help="Backend base URL")
    parser.add_argument("--requests", type=int,   default=config.DEFAULT_TRAFFIC_REQUESTS)
    parser.add_argument("--interval", type=float, default=config.DEFAULT_TRAFFIC_INTERVAL)
    parser.add_argument("--verbose",  action="store_true")
    args = parser.parse_args()

    gen = TrafficGenerator(base_url=args.url)
    summary = gen.run(n_requests=args.requests, interval=args.interval, verbose=args.verbose)
    summary.print_summary()
