"""24-hour local training loop for the Meituan AutoSolver.

This script keeps the online-compatible single-courier output constraint,
generates synthetic cases similar to the weak online categories, and repeatedly
invokes ``autosolver_agent.train``.  It saves progress after every cycle so it
can be interrupted without losing the best solver.py written so far.

Enhanced with optional LLM analysis (Mimo-V2.5-pro) every N cycles.
"""

from __future__ import annotations

import argparse
import itertools
import json
import random
import shutil
import time
from pathlib import Path

from autosolver_agent import collect_cases, train
from benchmark import benchmark_case


ROOT = Path(__file__).resolve().parent
TRAINING_DIR = ROOT / "training_cases_auto"
LOG_PATH = ROOT / "long_training_log.jsonl"
BEST_SOLVER_PATH = ROOT / "solver_best_long.py"


def write_case(path: Path, num_tasks: int, num_couriers: int, seed: int, mode: str, sample_rate: float) -> None:
    rng = random.Random(seed)
    tasks = [f"T{i:04d}" for i in range(num_tasks)]
    couriers = [f"C{i:03d}" for i in range(num_couriers)]
    bundles = [(task,) for task in tasks]
    bundles.extend(itertools.combinations(tasks, 2))

    rows = ["task_id_list\tcourier_id\ttotal_score\twillingness"]
    for bundle in bundles:
        n = len(bundle)
        for courier in couriers:
            if rng.random() > sample_rate:
                continue
            if mode == "low_willingness":
                willingness = rng.betavariate(1.1, 8.0) * 0.74 + 0.01
                score = n * rng.uniform(18.0, 46.0) + rng.uniform(-2.0, 22.0)
                score += willingness * rng.uniform(35.0, 105.0)
            elif mode == "scarce_couriers":
                willingness = rng.betavariate(2.0, 3.1) * 0.86 + 0.035
                score = n * rng.uniform(16.0, 50.0) + rng.uniform(-4.0, 18.0)
                if n == 2:
                    score *= rng.uniform(0.62, 0.90)
            elif mode == "high_noise":
                willingness = rng.uniform(0.03, 0.94)
                score = n * rng.uniform(10.0, 64.0) + rng.gauss(8.0, 24.0)
                if rng.random() < 0.10:
                    score *= rng.uniform(0.32, 1.85)
            else:
                willingness = rng.betavariate(2.3, 3.8) * 0.88 + 0.025
                score = n * rng.uniform(17.0, 53.0) + rng.uniform(-5.0, 15.0)
            rows.append(f"{','.join(bundle)}\t{courier}\t{max(5.0, min(130.0, score)):.4f}\t{min(0.99, max(0.005, willingness)):.4f}")

    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def generate_cycle_cases(cycle: int, base_seed: int, llm_specs: list[dict] | None = None) -> list[Path]:
    TRAINING_DIR.mkdir(exist_ok=True)
    specs = [
        ("low_willingness", 30, 62, 0.76),
        ("low_willingness", 30, 48, 0.88),
        ("scarce_couriers", 40, 32, 0.94),
        ("scarce_couriers", 40, 38, 0.82),
        ("high_noise", 30, 70, 0.72),
        ("medium", 30, 60, 0.78),
    ]

    # Replace some specs with LLM suggestions if available
    if llm_specs:
        for i, spec in enumerate(llm_specs[:2]):
            mode = spec.get("mode", "medium")
            tasks = spec.get("num_tasks", 30)
            couriers = spec.get("num_couriers", 50)
            rate = spec.get("sample_rate", 0.8)
            if i < len(specs):
                specs[i] = (mode, tasks, couriers, rate)

    paths = []
    for index, (mode, tasks, couriers, rate) in enumerate(specs):
        seed = base_seed + cycle * 101 + index * 17
        path = TRAINING_DIR / f"auto_{mode}_{cycle:04d}_{seed}.txt"
        write_case(path, tasks, couriers, seed, mode, rate)
        paths.append(path)
    return paths


def benchmark_paths(paths: list[Path]) -> dict:
    results = [benchmark_case(path) for path in paths]
    avg_penalty = sum(result["penalty_score"] for result in results) / len(results)
    return {
        "avg_penalty": avg_penalty,
        "results": results,
    }


def run_llm_analysis(cycle: int, history: list[dict]) -> list[dict] | None:
    """Run LLM analysis every few cycles. Returns suggested case specs or None."""
    try:
        from agent.llm_analyzer import analyze_training_results, suggest_case_generation
        memory_path = ROOT.parent / "memory" / "training" / "experiments.sqlite"
        if not memory_path.exists():
            return None

        analysis = analyze_training_results(str(memory_path))
        perf_summary = {
            "cycle": cycle,
            "history": history[-5:],
            "analysis": analysis,
        }
        specs = suggest_case_generation(perf_summary)
        print(f"[llm] Cycle {cycle}: suggested {len(specs)} case specs")
        return specs
    except Exception as exc:
        print(f"[llm] Analysis failed: {exc}")
        return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=float, default=24.0)
    parser.add_argument("--target", type=float, default=600.0)
    parser.add_argument("--cycle-iterations", type=int, default=3)
    parser.add_argument("--seed", type=int, default=20260525)
    parser.add_argument("--llm-every", type=int, default=5, help="Run LLM analysis every N cycles (0=disabled)")
    args = parser.parse_args()

    deadline = time.time() + args.hours * 3600.0
    cycle = 0
    fixed_cases = [
        ROOT.parent / "cases" / "large_seed301.txt",
        ROOT.parent / "datasets_archive" / "training_cases" / "synthetic_low_willingness_30_seed501.txt",
        ROOT.parent / "datasets_archive" / "training_cases" / "synthetic_scarce_couriers_40_seed401.txt",
        ROOT.parent / "datasets_archive" / "training_cases" / "synthetic_high_noise_30_seed601.txt",
    ]
    fixed_cases = [path for path in fixed_cases if path.exists()]
    if not BEST_SOLVER_PATH.exists() and (ROOT / "solver.py").exists():
        shutil.copy2(ROOT / "solver.py", BEST_SOLVER_PATH)
    best_holdout = None
    history: list[dict] = []
    llm_specs: list[dict] | None = None

    while time.time() < deadline:
        # LLM analysis every N cycles
        if args.llm_every > 0 and cycle > 0 and cycle % args.llm_every == 0:
            llm_specs = run_llm_analysis(cycle, history)

        generated = generate_cycle_cases(cycle, args.seed, llm_specs)
        llm_specs = None  # Reset after use

        sampled_generated = [
            path for path in generated
            if "low_willingness" in path.name or "scarce_couriers" in path.name
        ][:3]
        sampled_generated.append(generated[(cycle + 4) % len(generated)])
        case_args = [str(path) for path in fixed_cases + sampled_generated]
        cases = collect_cases(case_args)

        summary = train(
            cases,
            iterations=args.cycle_iterations,
            seed=args.seed + cycle,
            solver_path=ROOT / "solver.py",
        )
        holdout_cases = fixed_cases + sampled_generated
        bench = benchmark_paths(holdout_cases)
        improved = best_holdout is None or bench["avg_penalty"] < best_holdout
        if improved:
            best_holdout = bench["avg_penalty"]
            shutil.copy2(ROOT / "solver.py", BEST_SOLVER_PATH)
        elif BEST_SOLVER_PATH.exists():
            shutil.copy2(BEST_SOLVER_PATH, ROOT / "solver.py")
        record = {
            "cycle": cycle,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "avg_penalty": bench["avg_penalty"],
            "best_rank": summary["best_rank"],
            "case_count": len(cases),
            "kept": improved,
        }
        history.append(record)
        with LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        print(json.dumps(record, ensure_ascii=False))

        if bench["avg_penalty"] <= args.target:
            break
        cycle += 1


if __name__ == "__main__":
    main()
