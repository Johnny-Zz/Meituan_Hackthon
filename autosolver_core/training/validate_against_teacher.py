"""Validate the Agent against the high-performance teacher solver.

Reports coverage match rate, average/median/p95 penalty gap, invalid count, and
runtime.  This is the holdout check recommended before submission.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import statistics
import sys
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import core_solver
import solver
from agent.evaluator import evaluate_output, is_better
from agent.strategy_registry import run_strategy, strategy_by_name


def collect_cases(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    return sorted(p for p in path.rglob("*.txt") if p.is_file())


def run_teacher(text: str, mode: str, budget_ms: float) -> dict[str, Any]:
    names = ["core_single_teacher"] if mode == "single" else ["core_default_teacher"]
    if mode == "best":
        names = ["core_single_teacher", "core_default_teacher"]
    best = None
    for name in names:
        strategy = strategy_by_name(name)
        if strategy is None:
            continue
        result = run_strategy(core_solver, text, strategy, budget_ms)
        result.pop("solution", None)
        if is_better(result, best):
            best = result
    if best is None:
        raise RuntimeError("No teacher strategy available")
    return best


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    idx = min(len(values) - 1, max(0, int(round((len(values) - 1) * p))))
    return values[idx]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("path")
    parser.add_argument("--teacher", choices=["single", "default", "best"], default="single")
    parser.add_argument("--teacher-budget-ms", type=float, default=9200.0)
    args = parser.parse_args()

    cases = collect_cases(Path(args.path))
    gaps: list[float] = []
    runtimes: list[float] = []
    coverage_match = 0
    invalid = 0
    timeout_risk = 0
    for case in cases:
        text = case.read_text(encoding="utf-8")
        teacher_result = run_teacher(text, args.teacher, args.teacher_budget_ms)
        start = time.perf_counter()
        solution = solver.solve(text)
        runtime_ms = (time.perf_counter() - start) * 1000.0
        result = evaluate_output(text, solution)
        runtimes.append(runtime_ms)
        if not result.get("valid"):
            invalid += 1
        if runtime_ms > 9500:
            timeout_risk += 1
        same_coverage = result.get("covered_tasks") == teacher_result.get("covered_tasks")
        if same_coverage:
            coverage_match += 1
            gap = float(result.get("penalty_score", 0.0)) - float(teacher_result.get("penalty_score", 0.0))
            gaps.append(gap)
        else:
            missed = int(teacher_result.get("covered_tasks", 0)) - int(result.get("covered_tasks", 0))
            gap = missed * 1_000_000.0
            gaps.append(gap)
        print(
            f"{case.name:<45} teacher={teacher_result.get('covered_tasks')}/{teacher_result.get('total_tasks')} "
            f"agent={result.get('covered_tasks')}/{result.get('total_tasks')} "
            f"gap={gaps[-1]:.6f} valid={result.get('valid')} ms={runtime_ms:.1f}"
        )

    n = max(1, len(cases))
    print("\nSUMMARY")
    print(f"cases={len(cases)}")
    print(f"coverage_match_rate={coverage_match / n:.4f}")
    print(f"avg_penalty_gap={statistics.mean(gaps) if gaps else 0.0:.6f}")
    print(f"median_penalty_gap={statistics.median(gaps) if gaps else 0.0:.6f}")
    print(f"p95_penalty_gap={percentile(gaps, 0.95):.6f}")
    print(f"invalid_count={invalid}")
    print(f"timeout_risk_count={timeout_risk}")
    print(f"avg_runtime_ms={statistics.mean(runtimes) if runtimes else 0.0:.3f}")
    print(f"p95_runtime_ms={percentile(runtimes, 0.95):.3f}")


if __name__ == "__main__":
    main()
