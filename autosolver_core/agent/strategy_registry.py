"""Strategy registry for the autonomous solver portfolio.

Each strategy is a safe, parameterized policy.  The online Agent chooses among
these strategies; it never executes arbitrary LLM-generated code.

Teacher strategies are the behavior-distillation anchor: they call the original
high-performance ``core_solver.py`` with either its default configuration or a
strict single-courier configuration.  Offline training learns when lighter
variants can match these teachers within the 10-second budget.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from typing import Any
import time

from agent.evaluator import evaluate_output


@dataclass(frozen=True)
class Strategy:
    name: str
    family: str
    description: str
    config_overrides: dict[str, Any] = field(default_factory=dict)
    scenario_prior: dict[str, float] = field(default_factory=dict)
    min_budget_ms: float = 700.0
    preferred_budget_ms: float = 2500.0
    allow_parallel_assignment: bool = False
    exploration_weight: float = 1.0
    base_strategy_name: str | None = None

    def to_features(self) -> dict[str, float]:
        return {
            "family_teacher": 1.0 if self.family == "teacher" else 0.0,
            "family_greedy": 1.0 if self.family == "greedy" else 0.0,
            "family_hybrid": 1.0 if self.family == "hybrid" else 0.0,
            "family_risk": 1.0 if self.family == "risk_aware" else 0.0,
            "family_exact": 1.0 if self.family == "exact" else 0.0,
            "allow_parallel_assignment": 1.0 if self.allow_parallel_assignment else 0.0,
            "preferred_budget_ms": float(self.preferred_budget_ms),
            "local_search_budget_ms": float(self.config_overrides.get("local_search_budget_ms", 0.0)),
            "auto_strategy_budget_ms": float(self.config_overrides.get("auto_strategy_budget_ms", 0.0)),
            "backup_time_budget_ms": float(self.config_overrides.get("backup_time_budget_ms", 0.0)),
        }


def _budget_variant(strategy: Strategy, budget_ms: float) -> Strategy:
    """Return a strategy clone whose name makes the time budget explicit.

    Offline logs and the selector can then distinguish e.g. ``balanced@2500ms``
    from ``balanced@5000ms`` instead of conflating short and long trials.
    """
    budget = int(round(budget_ms))
    return replace(
        strategy,
        name=f"{strategy.name}@{budget}ms",
        min_budget_ms=min(strategy.min_budget_ms, float(budget_ms)),
        preferred_budget_ms=float(budget_ms),
        base_strategy_name=strategy.base_strategy_name or strategy.name,
    )


_BASE_STRATEGIES: list[Strategy] = [
    Strategy(
        name="core_single_teacher",
        family="teacher",
        description="Original high-performance core_solver with strict single-courier assignment.",
        config_overrides={
            "force_single_courier_output": True,
            "enable_multi_courier_output": False,
        },
        scenario_prior={
            "normal": 1.30,
            "scarce_couriers": 1.25,
            "low_willingness": 1.05,
            "bundle_heavy": 1.20,
            "high_score_variance": 1.18,
            "sparse_candidates": 1.20,
        },
        min_budget_ms=4000.0,
        preferred_budget_ms=8500.0,
        exploration_weight=0.20,
    ),
    Strategy(
        name="core_default_teacher",
        family="teacher",
        description="Original high-performance core_solver default behavior.",
        config_overrides={},
        scenario_prior={
            "normal": 1.22,
            "scarce_couriers": 1.18,
            "low_willingness": 1.00,
            "bundle_heavy": 1.15,
            "high_score_variance": 1.12,
            "sparse_candidates": 1.14,
        },
        min_budget_ms=4000.0,
        preferred_budget_ms=8500.0,
        exploration_weight=0.25,
    ),
    Strategy(
        name="core_parallel_teacher",
        family="teacher",
        description="Original core_solver with multi-courier backup enabled; only use if the judge permits parallel assignment.",
        config_overrides={
            "force_single_courier_output": False,
            "enable_multi_courier_output": True,
            "_runtime_multi_cost_mode": "race",
        },
        scenario_prior={"low_willingness": 1.25, "normal": 0.40},
        min_budget_ms=4500.0,
        preferred_budget_ms=8500.0,
        allow_parallel_assignment=True,
        exploration_weight=0.20,
    ),
    Strategy(
        name="single_fast_greedy",
        family="greedy",
        description="Strict single-courier output, fast greedy + light generated strategies.",
        config_overrides={
            "force_single_courier_output": True,
            "enable_multi_courier_output": False,
            "auto_strategy_budget_ms": 180.0,
            "local_search_budget_ms": 450.0,
            "multi_primary_time_budget_ms": 0.0,
            "backup_time_budget_ms": 0.0,
            "backup_reallocation_budget_ms": 0.0,
            "ilp_time_limit_seconds": 0.0,
            "max_generated_strategies": 10,
        },
        scenario_prior={"normal": 0.20, "sparse_candidates": 0.10},
        min_budget_ms=700.0,
        preferred_budget_ms=1400.0,
    ),
    Strategy(
        name="single_balanced_search",
        family="hybrid",
        description="Strict single-courier output with balanced generated strategies and local repair.",
        config_overrides={
            "force_single_courier_output": True,
            "enable_multi_courier_output": False,
            "auto_strategy_budget_ms": 320.0,
            "local_search_budget_ms": 1800.0,
            "multi_primary_time_budget_ms": 0.0,
            "backup_time_budget_ms": 0.0,
            "backup_reallocation_budget_ms": 0.0,
            "ilp_time_limit_seconds": 0.0,
            "max_generated_strategies": 16,
        },
        scenario_prior={"normal": 0.55, "bundle_heavy": 0.25, "high_score_variance": 0.25},
        min_budget_ms=1500.0,
        preferred_budget_ms=3600.0,
    ),
    Strategy(
        name="single_scarce_bundle_repair",
        family="hybrid",
        description="Strict single-courier output, spends more time on bundle replacement for scarce riders.",
        config_overrides={
            "force_single_courier_output": True,
            "enable_multi_courier_output": False,
            "auto_strategy_budget_ms": 280.0,
            "local_search_budget_ms": 3200.0,
            "multi_primary_time_budget_ms": 0.0,
            "backup_time_budget_ms": 0.0,
            "backup_reallocation_budget_ms": 0.0,
            "ilp_time_limit_seconds": 0.0,
            "max_generated_strategies": 20,
            "pair_top_k": 36,
            "triple_top_k": 24,
            "try_triples": True,
        },
        scenario_prior={"scarce_couriers": 0.85, "bundle_heavy": 0.45},
        min_budget_ms=2200.0,
        preferred_budget_ms=4800.0,
    ),
    Strategy(
        name="single_ilp_micro",
        family="exact",
        description="Strict single-courier output with a small MILP time slice when scipy is available.",
        config_overrides={
            "force_single_courier_output": True,
            "enable_multi_courier_output": False,
            "auto_strategy_budget_ms": 180.0,
            "local_search_budget_ms": 800.0,
            "multi_primary_time_budget_ms": 0.0,
            "backup_time_budget_ms": 0.0,
            "backup_reallocation_budget_ms": 0.0,
            "ilp_time_limit_seconds": 0.8,
            "max_generated_strategies": 8,
        },
        scenario_prior={"sparse_candidates": 0.50, "high_score_variance": 0.20},
        min_budget_ms=1400.0,
        preferred_budget_ms=2500.0,
    ),
    Strategy(
        name="parallel_low_willingness",
        family="risk_aware",
        description="Allows parallel riders for low willingness cases; useful only if the judge permits multi-courier output.",
        config_overrides={
            "force_single_courier_output": False,
            "enable_multi_courier_output": True,
            "auto_strategy_budget_ms": 150.0,
            "local_search_budget_ms": 0.0,
            "multi_primary_time_budget_ms": 1000.0,
            "backup_time_budget_ms": 4300.0,
            "backup_reallocation_budget_ms": 2200.0,
            "ilp_time_limit_seconds": 0.0,
            "min_backup_utility": 0.0,
            "max_extra_couriers_per_bundle": 8,
            "_runtime_multi_cost_mode": "race",
        },
        scenario_prior={"low_willingness": 1.00},
        min_budget_ms=2500.0,
        preferred_budget_ms=7200.0,
        allow_parallel_assignment=True,
    ),
    Strategy(
        name="parallel_normal_tail_backup",
        family="risk_aware",
        description="Allows positive-utility backup riders after a strong primary solution.",
        config_overrides={
            "force_single_courier_output": False,
            "enable_multi_courier_output": True,
            "auto_strategy_budget_ms": 260.0,
            "local_search_budget_ms": 1800.0,
            "multi_primary_time_budget_ms": 2500.0,
            "backup_time_budget_ms": 1000.0,
            "backup_reallocation_budget_ms": 350.0,
            "ilp_time_limit_seconds": 0.0,
            "min_backup_utility": 0.0,
            "max_extra_couriers_per_bundle": 5,
            "_runtime_multi_cost_mode": "race",
        },
        scenario_prior={"normal": 0.40, "low_willingness": 0.35},
        min_budget_ms=2800.0,
        preferred_budget_ms=6200.0,
        allow_parallel_assignment=True,
    ),
]

# Explicit budget variants.  These are intentionally few: enough for the
# selector to learn short/medium/teacher-like behavior without bloating the
# online portfolio.
STRATEGIES: list[Strategy] = []
for _s in _BASE_STRATEGIES:
    STRATEGIES.append(_s)
    if _s.name == "core_single_teacher":
        STRATEGIES.append(_budget_variant(_s, 9200.0))
    elif _s.name == "core_default_teacher":
        STRATEGIES.append(_budget_variant(_s, 9200.0))
    elif _s.name == "single_balanced_search":
        STRATEGIES.extend([_budget_variant(_s, 2500.0), _budget_variant(_s, 5000.0)])
    elif _s.name == "single_scarce_bundle_repair":
        STRATEGIES.extend([_budget_variant(_s, 5000.0), _budget_variant(_s, 7000.0)])
    elif _s.name == "parallel_low_willingness":
        STRATEGIES.append(_budget_variant(_s, 8500.0))


def get_strategies(allow_parallel_assignment: bool = False) -> list[Strategy]:
    if allow_parallel_assignment:
        return list(STRATEGIES)
    return [s for s in STRATEGIES if not s.allow_parallel_assignment]


@contextmanager
def temporary_config(core_solver: Any, overrides: dict[str, Any], budget_ms: float | None = None):
    config = core_solver.CONFIG
    patch = dict(overrides)
    if budget_ms is not None:
        patch["time_budget_ms"] = max(250.0, float(budget_ms))
        patch["safety_margin_ms"] = min(450.0, max(60.0, float(budget_ms) * 0.08))
    saved = {k: config.get(k) for k in patch}
    config.update(patch)
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is None and key in config:
                config.pop(key, None)
            else:
                config[key] = value


def run_strategy(core_solver: Any, input_text: str, strategy: Strategy, budget_ms: float | None = None) -> dict[str, Any]:
    run_budget = float(strategy.preferred_budget_ms if budget_ms is None else budget_ms)
    start = time.perf_counter()
    with temporary_config(core_solver, strategy.config_overrides, run_budget):
        solution = core_solver.solve(input_text)
    runtime_ms = (time.perf_counter() - start) * 1000.0
    result = evaluate_output(input_text, solution)
    result["runtime_ms"] = round(runtime_ms, 3)
    result["strategy_name"] = strategy.name
    result["solution"] = solution
    result["budget_ms"] = round(run_budget, 3)
    return result


def strategy_by_name(name: str) -> Strategy | None:
    for strategy in STRATEGIES:
        if strategy.name == name:
            return strategy
    return None


def add_strategy(strategy: Strategy) -> bool:
    """Register a new strategy at runtime. Returns False if name already exists."""
    if strategy_by_name(strategy.name) is not None:
        return False
    STRATEGIES.append(strategy)
    return True


def remove_strategy(name: str) -> bool:
    """Remove a strategy by name. Returns False if not found."""
    for i, s in enumerate(STRATEGIES):
        if s.name == name:
            STRATEGIES.pop(i)
            return True
    return False


def list_strategy_names() -> list[str]:
    """Return all registered strategy names."""
    return [s.name for s in STRATEGIES]


def get_base_strategy_names() -> list[str]:
    """Return names of base strategies (non-budget-variant)."""
    return [s.name for s in _BASE_STRATEGIES]
