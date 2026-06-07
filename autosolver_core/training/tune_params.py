"""Offline parameter mutation loop for teacher distillation.

It searches CONFIG variants around the high-performance solver's neighborhood,
logs every trial, and exports the best patches so they can be copied into
``agent/strategy_registry.py`` as distilled strategies.
"""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import core_solver
from agent.evaluator import evaluate_output, is_better
from agent.feature_extractor import extract_features
from agent.failure_analyzer import analyze_failure
from agent.memory import AgentMemory
from agent.reward import compute_teacher_relative_reward
from agent.strategy_registry import Strategy, run_strategy, strategy_by_name, temporary_config

DEFAULT_MEMORY = str(Path(__file__).resolve().parents[2] / "memory" / "training" / "experiments.sqlite")


def collect_cases(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    return sorted(p for p in path.rglob("*.txt") if p.is_file())


def mutate(base: dict[str, Any], rng: random.Random, scenario_hint: str = "") -> dict[str, Any]:
    patch = dict(base)
    patch["auto_strategy_budget_ms"] = round(rng.uniform(80.0, 520.0), 3)
    patch["local_search_budget_ms"] = round(rng.uniform(0.0, 4800.0), 3)
    patch["max_generated_strategies"] = rng.randint(6, 28)
    patch["pair_top_k"] = rng.randint(12, 52)
    patch["triple_top_k"] = rng.randint(8, 36)
    patch["try_triples"] = rng.random() < 0.80
    patch["ilp_time_limit_seconds"] = round(rng.choice([0.0, 0.0, 0.4, 0.8, 1.2]), 3)
    if "scarce" in scenario_hint:
        patch["local_search_budget_ms"] = round(rng.uniform(2400.0, 6200.0), 3)
        patch["pair_top_k"] = rng.randint(28, 60)
        patch["triple_top_k"] = rng.randint(18, 42)
        patch["try_triples"] = True
    if "low" in scenario_hint:
        patch["auto_strategy_budget_ms"] = round(rng.uniform(80.0, 280.0), 3)
        patch["local_search_budget_ms"] = round(rng.uniform(0.0, 2200.0), 3)
    patch["force_single_courier_output"] = True
    patch["enable_multi_courier_output"] = False
    return patch


def run_teacher(input_text: str, teacher_mode: str, budget_ms: float) -> dict[str, Any] | None:
    if teacher_mode == "none":
        return None
    names = ["core_single_teacher"] if teacher_mode == "single" else ["core_default_teacher"]
    if teacher_mode == "best":
        names = ["core_single_teacher", "core_default_teacher"]
    best = None
    for name in names:
        strategy = strategy_by_name(name)
        if strategy is None:
            continue
        result = run_strategy(core_solver, input_text, strategy, budget_ms)
        clean = dict(result)
        clean.pop("solution", None)
        if is_better(clean, best):
            best = clean
    return best


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("path")
    parser.add_argument("--memory", default=DEFAULT_MEMORY)
    parser.add_argument("--rounds", type=int, default=50)
    parser.add_argument("--budget-ms", type=float, default=3500.0)
    parser.add_argument("--teacher", choices=["single", "default", "best", "none"], default="single")
    parser.add_argument("--teacher-budget-ms", type=float, default=9200.0)
    parser.add_argument("--hard-budget-ms", type=float, default=10_000.0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--export-json", default="models/best_config_candidates.json")
    parser.add_argument("--top-k", type=int, default=10)
    args = parser.parse_args()

    rng = random.Random(args.seed)
    memory = AgentMemory(args.memory)
    cases = collect_cases(Path(args.path))
    if not cases:
        raise SystemExit(f"No .txt cases found under {args.path}")
    scenario_hint = str(Path(args.path)).lower()
    base = {
        "force_single_courier_output": True,
        "enable_multi_courier_output": False,
        "auto_strategy_budget_ms": 300.0,
        "local_search_budget_ms": 2800.0,
        "max_generated_strategies": 16,
        "pair_top_k": 28,
        "triple_top_k": 20,
        "try_triples": True,
        "ilp_time_limit_seconds": 0.0,
    }

    teacher_cache: dict[Path, dict[str, Any] | None] = {}
    for case in cases:
        teacher_cache[case] = run_teacher(case.read_text(encoding="utf-8"), args.teacher, args.teacher_budget_ms)

    best_rows: list[dict[str, Any]] = []
    for round_idx in range(args.rounds):
        patch = mutate(base, rng, scenario_hint)
        strategy_name = f"distilled_mutation_{round_idx:04d}@{int(args.budget_ms)}ms"
        strategy = Strategy(
            name=strategy_name,
            family="hybrid",
            description="Offline mutated CONFIG candidate distilled against teacher.",
            config_overrides=patch,
            min_budget_ms=args.budget_ms,
            preferred_budget_ms=args.budget_ms,
        )
        total_reward = 0.0
        total_gap = 0.0
        valid_count = 0
        best_case_result = None
        for case in cases:
            text = case.read_text(encoding="utf-8")
            features = extract_features(text).to_dict()
            with temporary_config(core_solver, patch, args.budget_ms):
                solution = core_solver.solve(text)
            result = evaluate_output(text, solution)
            result["runtime_ms"] = args.budget_ms
            result["strategy_name"] = strategy_name
            result["budget_ms"] = args.budget_ms
            tags = analyze_failure(features, result, solution)
            reward = compute_teacher_relative_reward(result, teacher_cache[case], args.budget_ms, args.hard_budget_ms)
            total_reward += reward
            if teacher_cache[case] and result.get("covered_tasks") == teacher_cache[case].get("covered_tasks"):
                total_gap += float(result.get("penalty_score", 0.0)) - float(teacher_cache[case].get("penalty_score", 0.0))
            if result.get("valid"):
                valid_count += 1
            memory.log_experiment(
                instance_id=case.stem,
                features=features,
                strategy_name=strategy_name,
                strategy_params={**patch, "preferred_budget_ms": args.budget_ms},
                result=result,
                reward=reward,
                failure_tags=tags,
                is_best=False,
            )
            if is_better(result, best_case_result):
                best_case_result = result
        avg_reward = total_reward / max(1, len(cases))
        avg_gap = total_gap / max(1, len(cases))
        row = {
            "strategy_name": strategy_name,
            "avg_reward": avg_reward,
            "avg_penalty_gap_when_coverage_matches": avg_gap,
            "valid_rate": valid_count / max(1, len(cases)),
            "budget_ms": args.budget_ms,
            "config_overrides": patch,
        }
        best_rows.append(row)
        best_rows.sort(key=lambda x: (float(x["avg_reward"]), float(x["valid_rate"])), reverse=True)
        best_rows = best_rows[: max(1, args.top_k)]
        if best_rows[0] is row:
            print(f"round={round_idx} NEW_BEST avg_reward={avg_reward:.3f} avg_gap={avg_gap:.6f} valid_rate={row['valid_rate']:.2f} patch={patch}")

    out_path = Path(args.export_json)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "description": "Best mutated CONFIG candidates. Copy selected configs into agent/strategy_registry.py as distilled strategies.",
        "source_path": str(args.path),
        "budget_ms": args.budget_ms,
        "teacher": args.teacher,
        "candidates": best_rows,
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    memory.close()
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
