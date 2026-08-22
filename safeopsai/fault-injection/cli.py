#!/usr/bin/env python3
"""
SafeOpsAI Fault Injector — Unified CLI
========================================
Entry point for all fault injection operations.

Usage examples
--------------
# Direct fault commands
python cli.py backend-down
python cli.py backend-down --restore
python cli.py cpu-stress --workers 2 --duration 60
python cli.py slow-db
python cli.py slow-db --restore
python cli.py slow-db --duration 45
python cli.py high-errors
python cli.py high-errors --restore
python cli.py db-down
python cli.py db-down --mode hard --restore
python cli.py bad-db-config
python cli.py bad-db-config --restore

# Traffic generator
python cli.py traffic --requests 120 --interval 0.5 --verbose

# Full scenario (recommended for testing)
python cli.py scenario slow-db
python cli.py scenario high-errors --duration 60
python cli.py scenario backend-down --duration 30 --skip-traffic
python cli.py scenario db-down --mode hard
python cli.py scenario cpu-stress --workers 4 --duration 45

# Status / safety
python cli.py status
python cli.py reset-all
"""

import argparse
import sys
import os

# Ensure the fault-injection package root is always on sys.path,
# regardless of how/where the script is invoked.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import config
from logger import log, GREEN, RED, YELLOW, CYAN, BOLD, section


# ── Safety guard ─────────────────────────────────────────────────────────

def _safety_check(backend_url: str) -> None:
    """
    Refuse to run if the target URL does not look like the SafeOpsAI
    backend — prevents accidental execution against the wrong service.
    """
    import requests as req  # type: ignore
    try:
        r = req.get(
            backend_url + config.BACKEND_IDENTITY_ENDPOINT,
            timeout=(config.HTTP_CONNECT_TIMEOUT, 5),
        )
        if r.status_code == 200:
            data = r.json()
            if data.get(config.BACKEND_IDENTITY_FIELD) == config.BACKEND_IDENTITY_VALUE:
                return  # looks like our backend
            log.error(RED(
                f"Safety check FAILED: '{config.BACKEND_IDENTITY_FIELD}' field in "
                f"/health response is '{data.get(config.BACKEND_IDENTITY_FIELD)}', "
                f"expected '{config.BACKEND_IDENTITY_VALUE}'."
            ))
        else:
            log.error(RED(f"Safety check FAILED: /health returned HTTP {r.status_code}."))
    except Exception as exc:
        log.error(RED(f"Safety check FAILED: cannot reach {backend_url} — {exc}"))
        log.error("  Is 'docker compose up' running in safeopsai/?")

    sys.exit(1)


# ── Sub-command handlers ──────────────────────────────────────────────────

def cmd_status(args: argparse.Namespace) -> int:
    """Show current fault state and service health."""
    import requests as req  # type: ignore
    section("SafeOpsAI — System Status")
    backend_url = args.url

    # Backend health
    try:
        r = req.get(backend_url + "/health", timeout=5)
        health = r.json()
        log.info(f"  Backend  : {GREEN('UP')}  (status={health.get('status')!r})")
    except Exception as exc:
        log.info(f"  Backend  : {RED('DOWN')}  ({exc})")

    # Current fault state
    try:
        r = req.get(backend_url + "/admin/fault", timeout=5)
        state = r.json().get("fault_state", {})
        log.info(f"  Faults   :")
        for key, val in state.items():
            indicator = RED("ACTIVE") if val else GREEN("off")
            log.info(f"    {key:<20}: {indicator}")
    except Exception as exc:
        log.warning(f"  Faults   : unable to read ({exc})")

    # Prometheus
    try:
        r = req.get(args.prometheus + "/-/healthy", timeout=5)
        log.info(f"  Prometheus: {GREEN('UP') if r.status_code == 200 else RED(str(r.status_code))}")
    except Exception as exc:
        log.info(f"  Prometheus: {RED('DOWN')}  ({exc})")

    # Event log
    from pathlib import Path
    log_path = Path(config.EVENT_LOG_PATH)
    if log_path.exists():
        lines = log_path.read_text().strip().splitlines()
        log.info(f"  Event log : {log_path}  ({len(lines)} events)")
    else:
        log.info(f"  Event log : {log_path}  (not yet created)")

    return 0


def cmd_reset_all(args: argparse.Namespace) -> int:
    """Clear all active soft faults — safe recovery command."""
    import requests as req  # type: ignore
    _safety_check(args.url)
    section("Reset All Faults")
    try:
        r = req.post(args.url + "/admin/fault/reset", timeout=10)
        r.raise_for_status()
        state = r.json().get("current_state", {})
        log.info(GREEN("All faults cleared."))
        for k, v in state.items():
            log.info(f"  {k}: {v}")
        return 0
    except Exception as exc:
        log.error(RED(f"Reset failed: {exc}"))
        return 1


def cmd_traffic(args: argparse.Namespace) -> int:
    """Run the standalone traffic generator."""
    _safety_check(args.url)
    from traffic.generator import TrafficGenerator
    gen = TrafficGenerator(base_url=args.url)
    summary = gen.run(
        n_requests=args.requests,
        interval=args.interval,
        verbose=args.verbose,
    )
    summary.print_summary()
    return 0


def cmd_backend_down(args: argparse.Namespace) -> int:
    from faults import backend_down as m
    if args.restore:
        m.restore()
    else:
        _safety_check(args.url)
        m.inject(kill=args.kill)
        if args.duration:
            import time
            log.info(f"[backend-down] Waiting {args.duration}s before auto-restore…")
            time.sleep(args.duration)
            m.restore()
    return 0


def cmd_cpu_stress(args: argparse.Namespace) -> int:
    _safety_check(args.url)
    from faults import cpu_stress as m
    m.inject(
        workers=args.workers,
        duration=args.duration,
    )
    return 0


def cmd_slow_db(args: argparse.Namespace) -> int:
    from faults import slow_db as m
    if args.restore:
        m.clear()
    elif args.duration:
        _safety_check(args.url)
        m.run(duration=args.duration)
    else:
        _safety_check(args.url)
        m.inject()
    return 0


def cmd_high_errors(args: argparse.Namespace) -> int:
    from faults import high_errors as m
    if args.restore:
        m.clear()
    elif args.duration:
        _safety_check(args.url)
        m.run(
            duration=args.duration,
            traffic_requests=args.requests,
            traffic_interval=args.interval,
        )
    else:
        _safety_check(args.url)
        m.inject()
    return 0


def cmd_db_down(args: argparse.Namespace) -> int:
    from faults import db_down as m
    if args.restore:
        m.restore(mode=args.mode)
    elif args.duration:
        _safety_check(args.url)
        m.run(duration=args.duration, mode=args.mode)
    else:
        _safety_check(args.url)
        m.inject(mode=args.mode)
    return 0


def cmd_bad_db_config(args: argparse.Namespace) -> int:
    from faults import bad_db_config as m
    if args.restore:
        m.restore()
    elif args.duration:
        _safety_check(args.url)
        m.run(duration=args.duration)
    else:
        _safety_check(args.url)
        m.inject()
    return 0


def cmd_scenario(args: argparse.Namespace) -> int:
    _safety_check(args.url)
    from scenarios.runner import run_scenario, SCENARIO_NAMES
    if args.name not in SCENARIO_NAMES:
        log.error(RED(f"Unknown scenario '{args.name}'. Valid: {', '.join(SCENARIO_NAMES)}"))
        return 1
    outcome = run_scenario(
        scenario=args.name,
        duration=args.duration,
        workers=args.workers,
        traffic_n=args.requests,
        traffic_interval=args.interval,
        db_mode=args.mode,
        backend_url=args.url,
        prom_url=args.prometheus,
        skip_traffic=args.skip_traffic,
    )
    return 0 if "completed" in outcome.get("status", "") else 1


# ── Argument parser ───────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument(
        "--url", default=config.BACKEND_URL,
        metavar="URL",
        help=f"Backend base URL (default: {config.BACKEND_URL})",
    )
    parent.add_argument(
        "--prometheus", default=config.PROMETHEUS_URL,
        metavar="URL",
        help=f"Prometheus base URL (default: {config.PROMETHEUS_URL})",
    )

    parser = argparse.ArgumentParser(
        prog="fault-injector",
        description="SafeOpsAI Fault Injection CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  python cli.py status
  python cli.py reset-all
  python cli.py traffic --requests 100 --interval 0.5
  python cli.py backend-down
  python cli.py backend-down --restore
  python cli.py cpu-stress --workers 2 --duration 60
  python cli.py slow-db --duration 45
  python cli.py slow-db --restore
  python cli.py high-errors --duration 30
  python cli.py db-down --mode soft
  python cli.py db-down --mode hard --restore
  python cli.py bad-db-config --duration 30
  python cli.py scenario slow-db --duration 60
  python cli.py scenario high-errors --duration 45 --requests 150
  python cli.py scenario backend-down --skip-traffic
  python cli.py scenario cpu-stress --workers 4 --duration 60
        """,
    )
    subs = parser.add_subparsers(dest="command", metavar="COMMAND")
    subs.required = True

    # ── status ────────────────────────────────────────────────────────────
    p_status = subs.add_parser("status", parents=[parent],
                                help="Show backend health and current fault state")
    p_status.set_defaults(func=cmd_status)

    # ── reset-all ─────────────────────────────────────────────────────────
    p_reset = subs.add_parser("reset-all", parents=[parent],
                               help="Clear all active soft faults immediately")
    p_reset.set_defaults(func=cmd_reset_all)

    # ── traffic ───────────────────────────────────────────────────────────
    p_traffic = subs.add_parser("traffic", parents=[parent],
                                 help="Run the standalone traffic generator")
    p_traffic.add_argument("--requests", type=int,   default=config.DEFAULT_TRAFFIC_REQUESTS,
                           help=f"Number of requests (default: {config.DEFAULT_TRAFFIC_REQUESTS})")
    p_traffic.add_argument("--interval", type=float, default=config.DEFAULT_TRAFFIC_INTERVAL,
                           help=f"Seconds between requests (default: {config.DEFAULT_TRAFFIC_INTERVAL})")
    p_traffic.add_argument("--verbose",  action="store_true",
                           help="Print each request result")
    p_traffic.set_defaults(func=cmd_traffic)

    # ── backend-down ──────────────────────────────────────────────────────
    p_bd = subs.add_parser("backend-down", parents=[parent],
                            help="Stop the backend container (simulate a crash)")
    p_bd.add_argument("--restore",  action="store_true", help="Restart the backend instead")
    p_bd.add_argument("--kill",     action="store_true", help="Use SIGKILL instead of graceful stop")
    p_bd.add_argument("--duration", type=int, default=0,
                      help="If set, auto-restore after this many seconds")
    p_bd.set_defaults(func=cmd_backend_down)

    # ── cpu-stress ────────────────────────────────────────────────────────
    p_cpu = subs.add_parser("cpu-stress", parents=[parent],
                             help="Run stress-ng inside the backend container")
    p_cpu.add_argument("--workers",  type=int, default=config.DEFAULT_CPU_WORKERS,
                       help=f"CPU worker processes (default: {config.DEFAULT_CPU_WORKERS})")
    p_cpu.add_argument("--duration", type=int, default=config.DEFAULT_DURATION_SECONDS,
                       help=f"Duration in seconds (default: {config.DEFAULT_DURATION_SECONDS})")
    p_cpu.set_defaults(func=cmd_cpu_stress)

    # ── slow-db ───────────────────────────────────────────────────────────
    p_sdb = subs.add_parser("slow-db", parents=[parent],
                             help="Enable slow DB query fault via /admin/fault")
    p_sdb.add_argument("--restore",  action="store_true", help="Clear the fault instead")
    p_sdb.add_argument("--duration", type=int, default=0,
                       help="If set, auto-clear after this many seconds")
    p_sdb.set_defaults(func=cmd_slow_db)

    # ── high-errors ───────────────────────────────────────────────────────
    p_he = subs.add_parser("high-errors", parents=[parent],
                            help="Enable high error rate fault (50%% of requests fail)")
    p_he.add_argument("--restore",  action="store_true", help="Clear the fault instead")
    p_he.add_argument("--duration", type=int, default=0,
                      help="If set, auto-clear after this many seconds")
    p_he.add_argument("--requests", type=int,   default=config.DEFAULT_TRAFFIC_REQUESTS)
    p_he.add_argument("--interval", type=float, default=config.DEFAULT_TRAFFIC_INTERVAL)
    p_he.set_defaults(func=cmd_high_errors)

    # ── db-down ───────────────────────────────────────────────────────────
    p_dd = subs.add_parser("db-down", parents=[parent],
                            help="Make the database unavailable (soft API flag or hard container stop)")
    p_dd.add_argument("--restore",  action="store_true", help="Restore DB availability")
    p_dd.add_argument("--mode",     choices=["soft", "hard"], default="soft",
                      help="soft=API flag, hard=stop database container (default: soft)")
    p_dd.add_argument("--duration", type=int, default=0,
                      help="If set, auto-restore after this many seconds")
    p_dd.set_defaults(func=cmd_db_down)

    # ── bad-db-config ─────────────────────────────────────────────────────
    p_bdc = subs.add_parser("bad-db-config", parents=[parent],
                             help="Restart backend with invalid DB_HOST to simulate misconfiguration")
    p_bdc.add_argument("--restore",  action="store_true", help="Restore correct config")
    p_bdc.add_argument("--duration", type=int, default=0,
                       help="If set, auto-restore after this many seconds")
    p_bdc.set_defaults(func=cmd_bad_db_config)

    # ── scenario ──────────────────────────────────────────────────────────
    p_sc = subs.add_parser("scenario", parents=[parent],
                            help="Run a complete end-to-end fault scenario with traffic and recovery check")
    p_sc.add_argument("name",
                      choices=["slow-db", "high-errors", "db-down",
                               "backend-down", "cpu-stress", "bad-db-config"],
                      help="Scenario to run")
    p_sc.add_argument("--duration",     type=int,   default=config.DEFAULT_DURATION_SECONDS,
                      help=f"Fault duration (default: {config.DEFAULT_DURATION_SECONDS}s)")
    p_sc.add_argument("--workers",      type=int,   default=config.DEFAULT_CPU_WORKERS,
                      help=f"CPU workers — cpu-stress only (default: {config.DEFAULT_CPU_WORKERS})")
    p_sc.add_argument("--requests",     type=int,   default=config.DEFAULT_TRAFFIC_REQUESTS,
                      help=f"Traffic requests (default: {config.DEFAULT_TRAFFIC_REQUESTS})")
    p_sc.add_argument("--interval",     type=float, default=config.DEFAULT_TRAFFIC_INTERVAL,
                      help=f"Traffic interval (default: {config.DEFAULT_TRAFFIC_INTERVAL}s)")
    p_sc.add_argument("--mode",         choices=["soft", "hard"], default="soft",
                      help="DB mode — db-down only (default: soft)")
    p_sc.add_argument("--skip-traffic", action="store_true",
                      help="Disable background traffic generation")
    p_sc.set_defaults(func=cmd_scenario)

    return parser


# ── Entry point ───────────────────────────────────────────────────────────

def main() -> None:
    parser = build_parser()
    args   = parser.parse_args()

    # Allow --url override to propagate to config before any module loads
    if hasattr(args, "url") and args.url != config.BACKEND_URL:
        config.BACKEND_URL = args.url
    if hasattr(args, "prometheus") and args.prometheus != config.PROMETHEUS_URL:
        config.PROMETHEUS_URL = args.prometheus

    try:
        exit_code = args.func(args)
    except KeyboardInterrupt:
        log.warning(YELLOW("\nInterrupted. Run 'python cli.py reset-all' to clear any active faults."))
        exit_code = 130
    except Exception as exc:
        log.error(RED(f"Unexpected error: {exc}"))
        import traceback
        traceback.print_exc()
        exit_code = 1

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
