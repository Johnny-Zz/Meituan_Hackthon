"""Time-boxed teacher distillation for the AutoSolver Agent.

The script is designed for the 1500 synthetic training cases under
``agent/meituan_1500_training_samples_by_scene``.  It cycles through scenes in
a stratified order, compares registered Agent strategies against the
high-performance solver teachers, logs teacher-relative rewards to SQLite, and
exports the learned selector plus submission artifacts at the end.

Example:
    python training/run_agent_distillation_2h.py --duration-seconds 7200
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import random
import shutil
import sys
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import core_solver
from agent.evaluator import evaluate_output, is_better
from agent.failure_analyzer import analyze_failure
from agent.feature_extractor import extract_features
from agent.meta_controller import AutoSolverAgent
from agent.memory import AgentMemory
from agent.reward import compute_teacher_relative_reward
from agent.strategy_registry import Strategy, run_strategy, strategy_by_name
from training.train_selector import build_policy_table


UNIFIED_ROOT = ROOT.parent
DEFAULT_DATA_ROOT = UNIFIED_ROOT / "datasets_archive" / "meituan_1500_training_samples_by_scene" / "case_bank" / "train"
DEFAULT_MEMORY = UNIFIED_ROOT / "memory" / "training" / "experiments.sqlite"
DEFAULT_MODEL = ROOT / "models" / "strategy_selector.json"
DEFAULT_OUTPUTS = ROOT / "models" / "agent_solver_outputs_1500.jsonl"
DEFAULT_STANDALONE = ROOT / "solver_submission_standalone.py"
DEFAULT_STATUS = ROOT / "models" / "distillation_2h_status.json"

TEACHER_NAMES = ("core_default_teacher", "core_single_teacher")
TRIAL_NAMES = (
    "core_default_teacher",
    "core_default_teacher@9200ms",
    "core_single_teacher",
    "single_fast_greedy",
    "single_balanced_search@2500ms",
    "single_balanced_search@5000ms",
    "single_scarce_bundle_repair@5000ms",
    "single_scarce_bundle_repair@7000ms",
    "single_ilp_micro",
)


def _scene_cases(data_root: Path, *, seed: int) -> list[Path]:
    data_root = data_root.resolve()
    scenes = []
    for scene_dir in sorted(path for path in data_root.iterdir() if path.is_dir()):
        cases = sorted(scene_dir.glob("*.txt"))
        stable_scene_salt = sum((idx + 1) * ord(ch) for idx, ch in enumerate(scene_dir.name))
        random.Random(seed + stable_scene_salt).shuffle(cases)
        scenes.append(cases)
    ordered: list[Path] = []
    max_len = max((len(cases) for cases in scenes), default=0)
    for index in range(max_len):
        for cases in scenes:
            if index < len(cases):
                ordered.append(cases[index])
    if not ordered:
        raise SystemExit(f"No training cases found under {data_root}")
    return ordered


def _load_strategy(name: str) -> Strategy:
    strategy = strategy_by_name(name)
    if strategy is None:
        raise RuntimeError(f"Strategy not found: {name}")
    return strategy


def _clean_result(result: dict[str, Any]) -> tuple[dict[str, Any], list[Any]]:
    result = dict(result)
    solution = result.pop("solution", [])
    return result, solution


def _run_best_teacher(input_text: str, teacher_budget_ms: float) -> dict[str, Any] | None:
    best: dict[str, Any] | None = None
    for name in TEACHER_NAMES:
        strategy = _load_strategy(name)
        result = run_strategy(core_solver, input_text, strategy, teacher_budget_ms)
        clean, _solution = _clean_result(result)
        if is_better(clean, best):
            best = clean
    return best


def _write_selector(memory_path: Path, model_path: Path) -> None:
    model_path.parent.mkdir(parents=True, exist_ok=True)
    policy = build_policy_table(memory_path)
    model_path.write_text(json.dumps(policy, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _write_standalone_solver(out_path: Path) -> None:
    source = (ROOT / "core_solver.py").read_text(encoding="utf-8")
    header = (
        '"""Standalone submission solver exported by training/run_agent_distillation_2h.py.\n\n'
        "This file is dependency-light and does not require the Agent package at judge time.\n"
        '"""\n\n'
    )
    out_path.write_text(header + source, encoding="utf-8")


def _write_agent_wrapper(out_path: Path) -> None:
    wrapper = '''"""Submission entry point for the trained AutoSolver Agent."""
from __future__ import annotations

import os
from typing import Any

os.environ.setdefault("MEITUAN_ENABLE_MEMORY", "0")
os.environ.setdefault("MEITUAN_AGENT_EXPLORE", "0")
os.environ.setdefault("MEITUAN_AGENT_MAX_ATTEMPTS", "3")
os.environ.setdefault("MEITUAN_AGENT_BUDGET_MS", "9200")

import core_solver
from agent.meta_controller import AutoSolverAgent

CONFIG = core_solver.CONFIG
_AGENT: AutoSolverAgent | None = None


def _get_agent() -> AutoSolverAgent:
    global _AGENT
    if _AGENT is None:
        _AGENT = AutoSolverAgent()
    return _AGENT


def solve(input_text: str) -> list:
    return _get_agent().solve(input_text, core_solver=core_solver)


def solve_core(input_text: str) -> list[Any]:
    return core_solver.solve(input_text)


def last_trace() -> dict[str, Any]:
    return dict(_get_agent().last_trace)
'''
    out_path.write_text(wrapper, encoding="utf-8")


def _export_agent_outputs(cases: list[Path], out_path: Path, limit: int, model_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    agent = AutoSolverAgent(model_path=model_path, enable_memory=False)
    with out_path.open("w", encoding="utf-8") as handle:
        for case in cases[:limit]:
            text = case.read_text(encoding="utf-8")
            solution = agent.solve(text, core_solver=core_solver)
            row = {
                "case": str(case.resolve().relative_to(ROOT)),
                "solution": solution,
                "metrics": evaluate_output(text, solution),
                "trace": agent.last_trace,
            }
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", default=str(DEFAULT_DATA_ROOT))
    parser.add_argument("--duration-seconds", type=float, default=7200.0)
    parser.add_argument("--memory", default=str(DEFAULT_MEMORY))
    parser.add_argument("--model-out", default=str(DEFAULT_MODEL))
    parser.add_argument("--status-out", default=str(DEFAULT_STATUS))
    parser.add_argument("--outputs-out", default=str(DEFAULT_OUTPUTS))
    parser.add_argument("--outputs-limit", type=int, default=10)
    parser.add_argument("--standalone-out", default=str(DEFAULT_STANDALONE))
    parser.add_argument("--solver-out", default=str(ROOT / "solver.py"))
    parser.add_argument("--seed", type=int, default=20260528)
    parser.add_argument("--reset-memory", action="store_true")
    parser.add_argument("--teacher-budget-ms", type=float, default=9200.0)
    parser.add_argument("--trial-budget-ms", type=float, default=0.0)
    parser.add_argument("--checkpoint-interval-seconds", type=float, default=300.0)
    parser.add_argument("--reserve-seconds", type=float, default=75.0)
    args = parser.parse_args()

    data_root = Path(args.data_root)
    memory_path = Path(args.memory)
    model_path = Path(args.model_out)
    status_path = Path(args.status_out)
    outputs_path = Path(args.outputs_out)
    standalone_path = Path(args.standalone_out)
    solver_out = Path(args.solver_out)

    os.environ.setdefault("MEITUAN_ENABLE_MEMORY", "0")
    os.environ.setdefault("MEITUAN_AGENT_EXPLORE", "0")

    memory_path.parent.mkdir(parents=True, exist_ok=True)
    model_path.parent.mkdir(parents=True, exist_ok=True)
    if args.reset_memory and memory_path.exists():
        backup = memory_path.with_suffix(f".backup.{int(time.time())}.sqlite")
        shutil.copy2(memory_path, backup)
        memory_path.unlink()
        print(f"Backed up old memory to {backup}", flush=True)

    cases = _scene_cases(data_root, seed=args.seed)
    strategies = [_load_strategy(name) for name in TRIAL_NAMES]
    memory = AgentMemory(memory_path)

    start = time.time()
    deadline = start + max(1.0, args.duration_seconds)
    next_checkpoint = start
    processed_cases = 0
    logged_trials = 0
    best_seen: dict[str, Any] | None = None
    best_case: str | None = None
    rng = random.Random(args.seed)

    print(
        f"Training on {len(cases)} cases from {data_root} for {args.duration_seconds:.0f}s",
        flush=True,
    )

    case_index = 0
    try:
        while time.time() < deadline - args.reserve_seconds:
            case = cases[case_index % len(cases)]
            case_index += 1
            text = case.read_text(encoding="utf-8")
            features = extract_features(text).to_dict()
            teacher = _run_best_teacher(text, args.teacher_budget_ms)
            case_results: list[tuple[Strategy, dict[str, Any], list[Any], list[str], float]] = []

            trial_order = list(strategies)
            rng.shuffle(trial_order)
            for strategy in trial_order:
                if time.time() >= deadline - args.reserve_seconds:
                    break
                trial_budget = args.trial_budget_ms if args.trial_budget_ms > 0 else strategy.preferred_budget_ms
                try:
                    result = run_strategy(core_solver, text, strategy, trial_budget)
                except Exception as exc:
                    result = {
                        "valid": False,
                        "covered_tasks": 0,
                        "total_tasks": int(features.get("num_orders", 0)),
                        "missing_tasks": int(features.get("num_orders", 0)),
                        "total_score": float("inf"),
                        "penalty_score": float("inf"),
                        "parallel_penalty_score": float("inf"),
                        "runtime_ms": 0.0,
                        "strategy_name": strategy.name,
                        "solution": [],
                        "error": repr(exc),
                    }
                clean, solution = _clean_result(result)
                tags = analyze_failure(features, clean, solution)
                reward = compute_teacher_relative_reward(clean, teacher, clean.get("runtime_ms", 0.0), 10_000.0)
                case_results.append((strategy, clean, solution, tags, reward))
                logged_trials += 1
                if is_better(clean, best_seen):
                    best_seen = clean
                    best_case = str(case.resolve().relative_to(ROOT))

            if case_results:
                best_name = max(case_results, key=lambda item: item[4])[1].get("strategy_name")
                for strategy, clean, _solution, tags, reward in case_results:
                    memory.log_experiment(
                        instance_id=case.stem,
                        features=features,
                        strategy_name=str(clean.get("strategy_name", strategy.name)),
                        strategy_params={**strategy.config_overrides, "preferred_budget_ms": strategy.preferred_budget_ms},
                        result=clean,
                        reward=reward,
                        failure_tags=tags,
                        is_best=(clean.get("strategy_name", strategy.name) == best_name),
                    )
            processed_cases += 1

            now = time.time()
            if now >= next_checkpoint:
                _write_selector(memory_path, model_path)
                status = {
                    "elapsed_seconds": round(now - start, 3),
                    "remaining_seconds": max(0.0, round(deadline - now, 3)),
                    "processed_cases": processed_cases,
                    "logged_trials": logged_trials,
                    "best_case": best_case,
                    "best_seen": best_seen,
                    "model_out": str(model_path),
                }
                status_path.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
                print(json.dumps(status, ensure_ascii=False), flush=True)
                next_checkpoint = now + args.checkpoint_interval_seconds
    finally:
        memory.close()

    _write_selector(memory_path, model_path)
    _write_agent_wrapper(solver_out)
    _write_standalone_solver(standalone_path)
    _export_agent_outputs(cases, outputs_path, args.outputs_limit, model_path)
    final_status = {
        "elapsed_seconds": round(time.time() - start, 3),
        "processed_cases": processed_cases,
        "logged_trials": logged_trials,
        "model_out": str(model_path),
        "solver_out": str(solver_out),
        "standalone_out": str(standalone_path),
        "outputs_out": str(outputs_path),
    }
    status_path.write_text(json.dumps(final_status, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(final_status, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
