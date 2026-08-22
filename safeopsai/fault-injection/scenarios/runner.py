"""
Scenario Runner
================
Orchestrates a complete, end-to-end fault injection scenario:

  [1] Verify environment is reachable (backend + Prometheus)
  [2] Start background traffic generation
  [3] Enable the selected fault
  [4] Hold the fault for the configured duration
  [5] Clear the fault
  [6] Verify the system begins recovering
  [7] Stop traffic and print summary

Writes a structured JSONL event for the whole scenario outcome.
The Incident Agent (Step 3) will later query Prometheus independently —
this runner intentionally does NOT write to the incidents DB table.

Supported scenarios
-------------------
  slow-db        → faults/slow_db.py
  high-errors    → faults/high_errors.py
  db-down        → faults/db_down.py  (--mode soft|hard)
  backend-down   → faults/backend_down.py
  cpu-stress     → faults/cpu_stress.py
  bad-db-config  → faults/bad_db_config.py
"""

import sys
import time
import threading
from datetime import datetime, timezone
from typing import Callable

import requests as req  # type: ignore

import config
from logger import log, GREEN, RED, YELLOW, CYAN, BOLD, section, step, get_event_logger
from traffic.generator import TrafficGenerator

# Lazy imports — only load a fault module when it is actually needed
import importlib

_FAULT_MODULES = {
    "slow-db":       "faults.slow_db",
    "high-errors":   "faults.high_errors",
    "db-down":       "faults.db_down",
    "backend-down":  "faults.backend_down",
    "cpu-stress":    "faults.cpu_stress",
    "bad-db-config": "faults.bad_db_config",
}

SCENARIO_NAMES = list(_FAULT_MODULES.keys())

# How long to wait after clearing a fault before calling the system "recovered"
RECOVERY_POLL_SECONDS  = 3
RECOVERY_MAX_WAIT      = 45   # seconds


# ── Environment checks ────────────────────────────────────────────────────

def _check_backend(url: str) -> tuple[bool, str]:
    """Return (ok, message)."""
    try:
        r = req.get(
            url + config.BACKEND_IDENTITY_ENDPOINT,
            timeout=(config.HTTP_CONNECT_TIMEOUT, 5),
        )
        if r.status_code == 200:
            data = r.json()
            if data.get(config.BACKEND_IDENTITY_FIELD) == config.BACKEND_IDENTITY_VALUE:
                return True, f"HTTP 200 — service={data['service']!r}"
        return False, f"Unexpected response: {r.status_code} {r.text[:80]}"
    except Exception as exc:
        return False, str(exc)


def _check_prometheus(url: str) -> tuple[bool, str]:
    """Return (ok, message)."""
    try:
        r = req.get(url + "/-/healthy", timeout=(config.HTTP_CONNECT_TIMEOUT, 5))
        if r.status_code == 200:
            return True, "HTTP 200 — Prometheus healthy"
        return False, f"Status {r.status_code}"
    except Exception as exc:
        return False, str(exc)


def _wait_recovery(backend_url: str, max_wait: int = RECOVERY_MAX_WAIT) -> bool:
    """
    Poll /health until the backend returns HTTP 200 with service='backend'.
    Returns True if recovered within max_wait seconds.
    """
    deadline = time.monotonic() + max_wait
    while time.monotonic() < deadline:
        ok, _ = _check_backend(backend_url)
        if ok:
            return True
        time.sleep(RECOVERY_POLL_SECONDS)
    return False


# ── Prometheus metric snapshot ────────────────────────────────────────────

def _prom_query(prom_url: str, expr: str) -> float | None:
    """Return the scalar value of a Prometheus instant query, or None."""
    try:
        r = req.get(
            prom_url + "/api/v1/query",
            params={"query": expr},
            timeout=config.PROM_QUERY_TIMEOUT,
        )
        results = r.json().get("data", {}).get("result", [])
        if results:
            return float(results[0]["value"][1])
    except Exception:
        pass
    return None


def _snapshot_metrics(prom_url: str) -> dict:
    """Grab a quick baseline/post-fault snapshot of key metrics."""
    return {
        "error_rate_per_s":    _prom_query(prom_url, "rate(application_errors_total[1m])"),
        "p95_request_latency": _prom_query(prom_url, "histogram_quantile(0.95, rate(http_request_duration_seconds_bucket[2m]))"),
        "p95_db_latency":      _prom_query(prom_url, "histogram_quantile(0.95, rate(db_query_duration_seconds_bucket[2m]))"),
        "backend_up":          _prom_query(prom_url, "up{job='safeops-backend'}"),
    }


# ── Main scenario runner ──────────────────────────────────────────────────

def run_scenario(
    scenario:        str,
    duration:        int   = config.DEFAULT_DURATION_SECONDS,
    workers:         int   = config.DEFAULT_CPU_WORKERS,
    traffic_n:       int   = config.DEFAULT_TRAFFIC_REQUESTS,
    traffic_interval: float = config.DEFAULT_TRAFFIC_INTERVAL,
    db_mode:         str   = "soft",
    backend_url:     str   = config.BACKEND_URL,
    prom_url:        str   = config.PROMETHEUS_URL,
    skip_traffic:    bool  = False,
) -> dict:
    """
    Run a complete fault injection scenario end-to-end.

    Parameters
    ----------
    scenario         : one of SCENARIO_NAMES
    duration         : seconds to hold the fault active
    workers          : CPU workers (cpu-stress only)
    traffic_n        : requests to send during fault window
    traffic_interval : seconds between traffic requests
    db_mode          : "soft" or "hard" (db-down only)
    backend_url      : override backend URL
    prom_url         : override Prometheus URL
    skip_traffic     : if True, skip the traffic generation step

    Returns
    -------
    dict  full scenario result written to the JSONL event log
    """
    if scenario not in _FAULT_MODULES:
        raise ValueError(
            f"Unknown scenario '{scenario}'. "
            f"Valid options: {', '.join(SCENARIO_NAMES)}"
        )

    total_steps = 7
    started_at  = datetime.now(timezone.utc).isoformat()

    section(f"SafeOpsAI Fault Injection — {scenario.upper()}")
    log.info(f"  Scenario : {BOLD(scenario)}")
    log.info(f"  Duration : {duration}s")
    log.info(f"  Backend  : {backend_url}")
    log.info(f"  Prometheus: {prom_url}")
    log.info("")

    outcome = {
        "scenario":          scenario,
        "started_at":        started_at,
        "ended_at":          "",
        "duration_seconds":  duration,
        "status":            "failed",
        "steps":             {},
        "baseline_metrics":  {},
        "post_fault_metrics":{},
        "recovery_metrics":  {},
        "traffic_summary":   {},
    }

    # ── Step 1: Check backend ─────────────────────────────────────────────
    step(1, total_steps, "Checking backend")
    ok, msg = _check_backend(backend_url)
    step(1, total_steps, f"Checking backend ({msg})", "PASS" if ok else "FAIL")
    outcome["steps"]["backend_check"] = {"ok": ok, "detail": msg}
    if not ok:
        log.error(RED(f"Backend not reachable at {backend_url}. Is 'docker compose up' running?"))
        outcome["status"] = "aborted_preflight"
        get_event_logger().write(event="scenario_aborted", **{k: v for k, v in outcome.items() if k not in ("steps","baseline_metrics","post_fault_metrics","recovery_metrics","traffic_summary")}, details=outcome)
        return outcome

    # ── Step 2: Check Prometheus ──────────────────────────────────────────
    step(2, total_steps, "Checking Prometheus")
    p_ok, p_msg = _check_prometheus(prom_url)
    step(2, total_steps, f"Checking Prometheus ({p_msg})", "PASS" if p_ok else "WARN")
    outcome["steps"]["prometheus_check"] = {"ok": p_ok, "detail": p_msg}
    # Not fatal — we can run without Prometheus, just no metric snapshots

    # ── Baseline metrics ──────────────────────────────────────────────────
    if p_ok:
        outcome["baseline_metrics"] = _snapshot_metrics(prom_url)
        log.info(f"  Baseline: {outcome['baseline_metrics']}")

    # ── Step 3: Start traffic ─────────────────────────────────────────────
    gen   = TrafficGenerator(base_url=backend_url)
    t_thread: threading.Thread | None = None

    if skip_traffic:
        step(3, total_steps, "Starting traffic (skipped)", "SKIP")
    else:
        step(3, total_steps, "Starting background traffic")
        t_thread = gen.run_background(n_requests=traffic_n, interval=traffic_interval)
        step(3, total_steps, f"Starting traffic ({traffic_n} req @ {traffic_interval}s)", "PASS")

    # ── Step 4: Inject fault ──────────────────────────────────────────────
    mod = importlib.import_module(_FAULT_MODULES[scenario])
    step(4, total_steps, f"Injecting '{scenario}' fault")

    fault_kwargs: dict = {}
    if scenario == "cpu-stress":
        fault_kwargs = {"workers": workers, "duration": duration}
    elif scenario == "db-down":
        fault_kwargs = {"mode": db_mode}
    elif scenario == "backend-down":
        fault_kwargs = {}

    try:
        if scenario == "cpu-stress":
            # cpu-stress is self-timed; run in a thread so we can proceed
            stress_thread = mod.inject_background(
                workers=workers, duration=duration
            )
        elif scenario == "backend-down":
            mod.inject()
        elif scenario == "bad-db-config":
            mod.inject()
        elif scenario == "db-down":
            mod.inject(mode=db_mode)
        else:
            # slow-db, high-errors
            mod.inject()
    except RuntimeError as exc:
        step(4, total_steps, f"Injecting '{scenario}' fault", "FAIL")
        log.error(RED(f"Fault injection failed: {exc}"))
        outcome["steps"]["fault_inject"] = {"ok": False, "detail": str(exc)}
        # Stop traffic
        if t_thread:
            gen.stop()
            t_thread.join(timeout=5)
        outcome["status"] = "aborted_injection"
        return outcome

    step(4, total_steps, f"'{scenario}' fault ACTIVE", "PASS")
    outcome["steps"]["fault_inject"] = {"ok": True}

    # ── Step 5: Hold fault for duration ───────────────────────────────────
    step(5, total_steps, f"Holding fault for {duration}s")
    # cpu-stress manages its own duration; for backend-down we still wait
    # so traffic keeps generating and Prometheus observes the outage.
    if scenario != "cpu-stress":
        _countdown(duration)
    else:
        # Wait for the stress thread to finish naturally
        stress_thread.join(timeout=duration + 10)

    step(5, total_steps, f"Fault duration complete ({duration}s)", "PASS")
    outcome["steps"]["fault_duration"] = {"ok": True, "duration_s": duration}

    # Post-fault metrics snapshot
    if p_ok:
        outcome["post_fault_metrics"] = _snapshot_metrics(prom_url)
        log.info(f"  Post-fault: {outcome['post_fault_metrics']}")

    # ── Step 6: Clear fault ───────────────────────────────────────────────
    step(6, total_steps, f"Clearing '{scenario}' fault")
    try:
        if scenario == "backend-down":
            mod.restore()
        elif scenario == "bad-db-config":
            mod.restore()
        elif scenario == "db-down":
            mod.restore(mode=db_mode)
        elif scenario == "cpu-stress":
            pass  # self-clearing
        else:
            mod.clear()
        step(6, total_steps, "Fault cleared", "PASS")
        outcome["steps"]["fault_clear"] = {"ok": True}
    except RuntimeError as exc:
        step(6, total_steps, "Fault clear", "FAIL")
        log.error(RED(f"Fault clear failed: {exc}"))
        outcome["steps"]["fault_clear"] = {"ok": False, "detail": str(exc)}

    # ── Step 7: Verify recovery ───────────────────────────────────────────
    step(7, total_steps, "Verifying recovery")
    recovered = _wait_recovery(backend_url, max_wait=RECOVERY_MAX_WAIT)
    step(7, total_steps, "Backend recovered" if recovered else "Recovery timeout", "PASS" if recovered else "WARN")
    outcome["steps"]["recovery"] = {"ok": recovered}

    if p_ok:
        time.sleep(5)  # let Prometheus scrape once
        outcome["recovery_metrics"] = _snapshot_metrics(prom_url)
        log.info(f"  Recovery: {outcome['recovery_metrics']}")

    # ── Stop traffic and collect summary ──────────────────────────────────
    if t_thread:
        gen.stop()
        t_thread.join(timeout=15)
        if gen.last_summary:
            outcome["traffic_summary"] = gen.last_summary.to_dict()
            gen.last_summary.print_summary()

    # ── Final status ──────────────────────────────────────────────────────
    outcome["status"] = "completed" if recovered else "completed_no_recovery"
    outcome["ended_at"] = datetime.now(timezone.utc).isoformat()

    _print_scenario_summary(scenario, outcome)

    event = {
        "event":            "scenario_completed",
        "scenario":         scenario,
        "fault_type":       scenario.replace("-", "_"),
        "started_at":       outcome["started_at"],
        "ended_at":         outcome["ended_at"],
        "duration_seconds": duration,
        "status":           outcome["status"],
        "details":          outcome,
    }
    get_event_logger().write(**event)
    return outcome


# ── Helpers ───────────────────────────────────────────────────────────────

def _countdown(seconds: int) -> None:
    """Show a simple countdown with tick marks every 10 s."""
    end = time.monotonic() + seconds
    while True:
        remaining = end - time.monotonic()
        if remaining <= 0:
            break
        next_tick = min(remaining, 10)
        time.sleep(next_tick)
        elapsed = seconds - (end - time.monotonic())
        log.info(f"  ⏱  {elapsed:.0f}s / {seconds}s elapsed…")


def _print_scenario_summary(scenario: str, outcome: dict) -> None:
    log.info("")
    log.info(BOLD("═" * 52))
    log.info(BOLD(f"  Scenario '{scenario}' complete"))
    log.info(BOLD("═" * 52))
    log.info(f"  Status     : {GREEN(outcome['status']) if 'completed' in outcome['status'] else RED(outcome['status'])}")
    log.info(f"  Duration   : {outcome['duration_seconds']}s")
    for name, result in outcome.get("steps", {}).items():
        ok_str = GREEN("PASS") if result.get("ok") else RED("FAIL")
        log.info(f"  {name:<25}: {ok_str}")
    traffic = outcome.get("traffic_summary", {})
    if traffic:
        log.info(f"  Traffic    : {traffic.get('total',0)} req, "
                 f"{traffic.get('error_rate_pct',0):.1f}% errors, "
                 f"avg {traffic.get('avg_latency_s',0):.3f}s")
    log.info("")
    log.info("  Next step: Check Prometheus/Grafana for the incident.")
    log.info(f"  Grafana  : http://localhost:3001  (admin / safeops123)")
    log.info(f"  Prometheus: http://localhost:9090/alerts")
    log.info(BOLD("═" * 52))
