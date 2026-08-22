"""
SafeOpsAI Evaluation — Unified Command Line Interface
======================================================
CLI entry point supporting scenario listing, campaign execution, strategy comparison,
and research report generation.
"""

import argparse
import asyncio
import sys
import logging
from pathlib import Path
from typing import List

from .config import EvaluationConfig
from .scenarios import list_scenarios, get_scenario
from .runner import ExperimentController
from .stats import calculate_metric_summary, compare_strategies_statistically
from .visualizer import generate_all_plots
from .report_generator import generate_evaluation_report
from .db import export_runs_to_csv

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s - %(message)s")
log = logging.getLogger("safeopsai.evaluation.cli")


def cmd_list_scenarios(args: argparse.Namespace) -> int:
    """Lists all standard fault scenarios in the catalog."""
    scenarios = list_scenarios()
    print("\n============================================================")
    print(" SAFEOPSAI EVALUATION — FAULT SCENARIO CATALOG")
    print("============================================================\n")
    for s in scenarios:
        print(f"[{s.scenario_id}] {s.name}")
        print(f"  Target Service   : {s.target_service}")
        print(f"  Fault Type       : {s.fault_type}")
        print(f"  Expected Symptoms: {', '.join(s.expected_symptoms)}")
        print(f"  Timeout / Thresh : {s.timeout_seconds}s / {s.recovery_threshold}")
        print()
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    """Runs evaluation campaign or single experiment trial."""
    cfg = EvaluationConfig(args.config)
    if args.repetitions:
        cfg.repetitions = args.repetitions

    scenarios = [args.scenario] if args.scenario else None
    strategies = [args.strategy] if args.strategy else None

    controller = ExperimentController(config=cfg, mock_mode=args.mock)

    print(f"\nLaunching Evaluation Run (mock_mode={args.mock})...")
    records = asyncio.run(
        controller.run_campaign(
            experiment_id=args.experiment_id,
            selected_scenarios=scenarios,
            selected_strategies=strategies,
            repetitions=args.repetitions,
        )
    )

    print(f"\nExecution Complete: {len(records)} trials executed.")
    generate_all_plots(records)
    generate_evaluation_report(records)
    print("Visualizations & Evaluation Report generated in results/")
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    """Compares strategy metrics for a scenario."""
    cfg = EvaluationConfig(args.config)
    controller = ExperimentController(config=cfg, mock_mode=True)

    sc = get_scenario(args.scenario) if args.scenario else get_scenario("SCENARIO-02")
    print(f"\n============================================================")
    print(f" SAFEOPSAI STRATEGY COMPARISON — {sc.scenario_id}: {sc.name}")
    print(f"============================================================\n")

    # Run quick comparison trials
    records = asyncio.run(
        controller.run_campaign(
            experiment_id="EXP-COMPARE",
            selected_scenarios=[sc.scenario_id],
            selected_strategies=["safeopsai", "naive_restart", "no_sandbox", "no_multi_agent", "no_rollback"],
            repetitions=5,
        )
    )

    safe_mttrs = [r.mttr_seconds for r in records if r.strategy == "safeopsai"]
    base_mttrs = [r.mttr_seconds for r in records if r.strategy == "naive_restart"]

    comp = compare_strategies_statistically(base_mttrs, safe_mttrs, sc.scenario_id, "MTTR")

    print(f"Strategy        Mean MTTR   Success Rate   Rollback Rate")
    print("-----------------------------------------------------")
    for st in ["safeopsai", "naive_restart", "no_sandbox", "no_multi_agent", "no_rollback"]:
        sub = [r for r in records if r.strategy == st]
        m_mttr = calculate_metric_summary("mttr", sc.scenario_id, st, [r.mttr_seconds for r in sub]).mean
        succ = (sum(1 for r in sub if r.success) / max(1, len(sub))) * 100
        rb = (sum(1 for r in sub if r.rollback) / max(1, len(sub))) * 100
        print(f"{st:<15} {m_mttr:>8.2f}s   {succ:>11.1f}%   {rb:>12.1f}%")

    print(f"\nStatistical Significance: {comp.description}")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    """Generates evaluation report and visualization figures."""
    cfg = EvaluationConfig(args.config)
    controller = ExperimentController(config=cfg, mock_mode=True)

    print("\nGenerating Research Evaluation Report & Visualization Figures...")
    records = asyncio.run(
        controller.run_campaign(
            experiment_id=args.experiment or "EXP-REPORT",
            repetitions=3,
        )
    )

    fig_paths = generate_all_plots(records)
    rep_path = generate_evaluation_report(records)

    print(f"\nReport generated: {rep_path}")
    print(f"Figures generated ({len(fig_paths)} plots):")
    for fp in fig_paths:
        print(f"  - {fp.name}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m evaluation",
        description="SafeOpsAI Phase 8 — Experimental Evaluation & Research Harness CLI",
    )
    parser.add_argument("--config", help="Path to custom experiments.yaml config")
    parser.add_argument("--mock", action="store_true", help="Run in mock/dry-run mode for testing")

    subs = parser.add_subparsers(dest="command", metavar="COMMAND")
    subs.required = True

    # list-scenarios
    p_list = subs.add_parser("list-scenarios", help="List all standard fault scenarios in catalog")
    p_list.set_defaults(func=cmd_list_scenarios)

    # run
    p_run = subs.add_parser("run", help="Run evaluation experiment campaign")
    p_run.add_argument("--scenario", help="Target scenario ID (e.g. SCENARIO-02)")
    p_run.add_argument("--strategy", help="Target strategy name (e.g. safeopsai, naive_restart)")
    p_run.add_argument("--repetitions", type=int, help="Number of repetitions per scenario")
    p_run.add_argument("--experiment-id", help="Custom experiment campaign ID")
    p_run.add_argument("--mock", action="store_true", help="Run in mock/dry-run mode for testing")
    p_run.set_defaults(func=cmd_run)

    # compare
    p_comp = subs.add_parser("compare", help="Compare SafeOpsAI against baseline strategies")
    p_comp.add_argument("--scenario", help="Target scenario ID (e.g. SCENARIO-02)")
    p_comp.set_defaults(func=cmd_compare)

    # report
    p_rep = subs.add_parser("report", help="Generate research report and figures")
    p_rep.add_argument("--experiment", help="Experiment campaign ID to summarize")
    p_rep.set_defaults(func=cmd_report)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        sys.exit(args.func(args))
    except Exception as exc:
        log.error("CLI Execution Error: %s", exc, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
