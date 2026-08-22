"""
SafeOpsAI Evaluation — Strategies Package
==========================================
Includes SafeOpsAI black-box strategy, Naive Baseline restart strategy, and Ablation variants.
"""

from .base import BaseStrategy, StrategyResult
from .safeopsai import SafeOpsAIStrategy
from .naive_restart import NaiveRestartStrategy
from .ablation import (
    NoSandboxStrategy,
    NoMultiAgentStrategy,
    NoRollbackStrategy,
)

STRATEGY_REGISTRY = {
    "safeopsai": SafeOpsAIStrategy,
    "naive_restart": NaiveRestartStrategy,
    "no_sandbox": NoSandboxStrategy,
    "no_multi_agent": NoMultiAgentStrategy,
    "no_rollback": NoRollbackStrategy,
}


def get_strategy(strategy_name: str) -> BaseStrategy:
    name = strategy_name.lower().strip()
    if name not in STRATEGY_REGISTRY:
        raise KeyError(f"Unknown strategy '{strategy_name}'. Valid strategies: {list(STRATEGY_REGISTRY.keys())}")
    return STRATEGY_REGISTRY[name]()
