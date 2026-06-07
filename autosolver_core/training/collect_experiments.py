"""Collect offline experiments for the self-learning AutoSolver Agent.

This script implements the behavior-distillation workflow recommended for this
project:
1. run the high-performance ``core_solver`` teacher on each instance;
2. run every registered strategy / budget variant;
3. score each trial with a teacher-relative reward;
4. write experiences to SQLite for ``train_selector.py``.

Usage examples:
    python training/collect_experiments.py case_bank/train --teacher single
    python training/collect_experiments.py case_bank/train --teacher best --allow-parallel
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import core_solver
from agent.evaluator import is_better
from agent.failure_analyzer import analyze_failure
from agent.feature_extractor import extract_features
from agent.memory import AgentMemory
from agent.reward import compute_reward, compute_teacher_relative_reward
from agent.strategy_registry import get_strategies, run_strategy, strategy_by_name

DEFAULT_MEMORY = str(Path(__file__).resolve().parents[2] / "memory" / "training" / "experiments.sqlite")


def collect_cases(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    return sorted(p for p in path.rglob("*.txt") if p.is_file())


def _result_without_solution(result: dict[str, Any]) -> tuple[dict[str, Any], list[Any]]:
    result = dict(result)
    solution = result.pop("solution", [])
    return result, solution


def run_teacher(input_text: str, teacher_mode: str, allow_parallel: bool, budget_ms: float) -> dict[str, Any] | None:
    if teacher_mode == "none":
        return None
    names: list[str]
    if teacher_mode == "single":
        names = ["core_single_teacher", "core_single_teacher@9200ms"]
    elif teacher_mode == "default":
        names = ["core_default_teacher", "core_default_teacher@9200ms"]
    elif teacher_mode == "parallel":
        names = ["core_parallel_teacher"]
    else:
        names = ["core_single_teacher", "core_default_teacher"]
        if allow_parallel:
            names.append("core_parallel_teacher")

    best: dict[str, Any] | None = None
    for name in names:
        strategy = strategy_by_name(name)
        if strategy is None:
            continue
        if strategy.allow_parallel_assignment and not allow_parallel:
            continue
        result = run_strategy(core_solver, input_text, strategy, budget_ms)
        clean, _solution = _result_without_solution(result)
        if is_better(clean, best):
            best = clean
    return best


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", help="Case file or directory containing .txt cases; subdirectories are scanned recursively.")
    parser.add_argument("--memory", default=DEFAULT_MEMORY)
    parser.add_argument("--budget-ms", type=float, default=0.0, help="If >0, override every strategy's own preferred budget.")
    parser.add_argument("--teacher-budget-ms", type=float, default=9200.0, help="Budget used to compute the teacher baseline.")
    parser.add_argument("--hard-budget-ms", type=float, default=10_000.0)
    parser.add_argument("--teacher", choices=["single", "default", "parallel", "best", "none"], default="single")
    parser.add_argument("--allow-parallel", action="store_true", help="Also evaluate multi-courier backup strategies.")
    parser.add_argument("--absolute-reward", action="store_true", help="Use absolute reward instead of teacher-relative reward.")
    args = parser.parse_args()

    memory = AgentMemory(args.memory)
    cases = collect_cases(Path(args.path))
    strategies = get_strategies(args.allow_parallel)
    for case in cases:
        input_text = case.read_text(encoding="utf-8")
        features = extract_features(input_text).to_dict()
        teacher_result = run_teacher(input_text, args.teacher, args.allow_parallel, args.teacher_budget_ms)
        teacher_desc = "none"
        if teacher_result:
            teacher_desc = (
                f"{teacher_result.get('strategy_name')} covered="
                f"{teacher_result.get('covered_tasks')}/{teacher_result.get('total_tasks')} "
                f"penalty={teacher_result.get('penalty_score')}"
            )
        print(f"CASE {case.name} scenario={features['scenario_type']} candidates={features['num_candidates']} teacher={teacher_desc}")

        best_result = None
        best_name = None
        case_results = []
        for strategy in strategies:
            trial_budget = args.budget_ms if args.budget_ms > 0 else strategy.preferred_budget_ms
            result = run_strategy(core_solver, input_text, strategy, trial_budget)
            clean, solution = _result_without_solution(result)
            tags = analyze_failure(features, clean, solution)
            if args.absolute_reward:
                reward = compute_reward(clean, clean.get("runtime_ms", 0.0), args.hard_budget_ms)
            else:
                reward = compute_teacher_relative_reward(clean, teacher_result, clean.get("runtime_ms", 0.0), args.hard_budget_ms)
            if is_better(clean, best_result):
                best_result = clean
                best_name = clean.get("strategy_name", strategy.name)
            case_results.append((strategy, clean, tags, reward))
            gap = ""
            if teacher_result and clean.get("covered_tasks") == teacher_result.get("covered_tasks"):
                gap = f" gap={float(clean.get('penalty_score', 0.0)) - float(teacher_result.get('penalty_score', 0.0)):.6f}"
            print(
                f"  {clean.get('strategy_name', strategy.name):<42} valid={clean['valid']} "
                f"covered={clean['covered_tasks']}/{clean['total_tasks']} "
                f"penalty={clean['penalty_score']} ms={clean['runtime_ms']} reward={reward:.3f}{gap} tags={tags}"
            )
        for strategy, result, tags, reward in case_results:
            memory.log_experiment(
                instance_id=case.stem,
                features=features,
                strategy_name=str(result.get("strategy_name", strategy.name)),
                strategy_params={**strategy.config_overrides, "preferred_budget_ms": strategy.preferred_budget_ms},
                result=result,
                reward=reward,
                failure_tags=tags,
                is_best=(result.get("strategy_name", strategy.name) == best_name),
            )
        print(f"  BEST {best_name}\n")
    memory.close()


if __name__ == "__main__":
    main()
