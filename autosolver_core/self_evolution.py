"""End-to-end self-iterating agent pipeline with LLM-driven optimization.

This is the main entry point that replaces manual training orchestration.
It runs a continuous loop of:
  1. Generate/refresh training cases (LLM-guided)
  2. Collect experiment data (all strategies × all cases)
  3. Train strategy selector (policy table + optional RF)
  4. Parameter mutation search (core_solver CONFIG)
  5. LLM analysis → generate new strategies
  6. Auto-inject validated strategies
  7. Evolutionary solver training
  8. Benchmark validation
  9. LLM reflection → adjust next iteration
  10. Save best checkpoint

Usage:
    python self_evolution.py                          # Default: 10 rounds, 2 hours
    python self_evolution.py --max-rounds 3           # Quick test: 3 rounds
    python self_evolution.py --hours 6 --llm-every 3  # 6 hours, LLM every 3 rounds
    python self_evolution.py --resume                  # Resume from checkpoint
"""
from __future__ import annotations

import argparse
import copy
import itertools
import json
import random
import shutil
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
UNIFIED_ROOT = ROOT.parent

# --- Defaults ---
DEFAULT_HOURS = 2.0
DEFAULT_MAX_ROUNDS = 50
DEFAULT_LLM_EVERY = 3
DEFAULT_CASE_COUNT = 8
DEFAULT_EVOLVE_ITERATIONS = 5
DEFAULT_HOLDOUT_COUNT = 4
CONVERGENCE_PATIENCE = 6

# --- Paths ---
CHECKPOINT_PATH = ROOT / "self_evolution_checkpoint.json"
LOG_PATH = ROOT / "self_evolution_log.jsonl"
BEST_SOLVER_PATH = ROOT / "solver_best_evolved.py"
MEMORY_DB = UNIFIED_ROOT / "memory" / "training" / "experiments.sqlite"
SELECTOR_JSON = ROOT / "models" / "strategy_selector.json"
TRAINING_DIR = UNIFIED_ROOT / "datasets_archive" / "training_cases_evolution"


def log(msg: str) -> None:
    """Print and flush a progress message."""
    print(msg, flush=True)


# ============================================================
# Case Generation
# ============================================================

def generate_case(
    path: Path,
    num_tasks: int,
    num_couriers: int,
    seed: int,
    mode: str,
    sample_rate: float,
) -> None:
    """Generate a single synthetic training case."""
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
            elif mode == "sparse":
                willingness = rng.betavariate(2.5, 4.0) * 0.85 + 0.05
                score = n * rng.uniform(15.0, 45.0) + rng.uniform(-3.0, 12.0)
            elif mode == "bundle_heavy":
                willingness = rng.betavariate(2.8, 2.5) * 0.90 + 0.03
                score = n * rng.uniform(20.0, 55.0) + rng.uniform(0.0, 25.0)
                if n == 2:
                    score *= rng.uniform(1.1, 1.4)
            else:  # medium
                willingness = rng.betavariate(2.3, 3.8) * 0.88 + 0.025
                score = n * rng.uniform(17.0, 53.0) + rng.uniform(-5.0, 15.0)
            rows.append(
                f"{','.join(bundle)}\t{courier}\t{max(5.0, min(130.0, score)):.4f}"
                f"\t{min(0.99, max(0.005, willingness)):.4f}"
            )
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


DEFAULT_CASE_SPECS = [
    ("low_willingness", 30, 62, 0.76),
    ("low_willingness", 30, 48, 0.88),
    ("scarce_couriers", 40, 32, 0.94),
    ("scarce_couriers", 40, 38, 0.82),
    ("high_noise", 30, 70, 0.72),
    ("medium", 30, 60, 0.78),
    ("sparse", 35, 55, 0.70),
    ("bundle_heavy", 25, 50, 0.85),
]


def generate_training_cases(
    round_num: int,
    seed: int,
    llm_specs: list[dict[str, Any]] | None = None,
) -> list[Path]:
    """Generate training cases for this round.

    Uses LLM-suggested specs if available, otherwise defaults.
    """
    TRAINING_DIR.mkdir(exist_ok=True)
    specs = []

    if llm_specs:
        for spec in llm_specs[:4]:
            specs.append((
                spec.get("mode", "medium"),
                spec.get("num_tasks", 30),
                spec.get("num_couriers", 50),
                spec.get("sample_rate", 0.8),
            ))

    # Always include some defaults for stability
    for ds in DEFAULT_CASE_SPECS[:DEFAULT_CASE_COUNT - len(specs)]:
        specs.append(ds)

    paths = []
    for i, (mode, tasks, couriers, rate) in enumerate(specs[:DEFAULT_CASE_COUNT]):
        case_seed = seed + round_num * 101 + i * 17
        path = TRAINING_DIR / f"evo_{mode}_r{round_num:03d}_{case_seed}.txt"
        generate_case(path, tasks, couriers, case_seed, mode, rate)
        paths.append(path)

    return paths


def get_fixed_cases() -> list[Path]:
    """Return the fixed holdout/validation cases."""
    candidates = [
        UNIFIED_ROOT / "cases" / "large_seed301.txt",
        UNIFIED_ROOT / "datasets_archive" / "training_cases" / "synthetic_low_willingness_30_seed501.txt",
        UNIFIED_ROOT / "datasets_archive" / "training_cases" / "synthetic_scarce_couriers_40_seed401.txt",
        UNIFIED_ROOT / "datasets_archive" / "training_cases" / "synthetic_high_noise_30_seed601.txt",
        UNIFIED_ROOT / "datasets_archive" / "training_cases" / "synthetic_medium_30_seed201.txt",
        UNIFIED_ROOT / "datasets_archive" / "training_cases" / "synthetic_scarce_couriers_40_seed402.txt",
    ]
    return [p for p in candidates if p.exists()]


# ============================================================
# Pipeline Steps
# ============================================================

def step_collect_experiments(cases: list[Path]) -> None:
    """Run all strategies on all cases, log to SQLite."""
    log(f"  [collect] Running {len(cases)} cases through all strategies...")

    import core_solver
    from agent.evaluator import is_better
    from agent.failure_analyzer import analyze_failure
    from agent.feature_extractor import extract_features
    from agent.memory import AgentMemory
    from agent.reward import compute_teacher_relative_reward
    from agent.strategy_registry import get_strategies, run_strategy, strategy_by_name

    memory = AgentMemory(str(MEMORY_DB))
    strategies = get_strategies(allow_parallel_assignment=False)

    for case in cases:
        input_text = case.read_text(encoding="utf-8")
        features = extract_features(input_text).to_dict()

        # Run teacher baseline
        teacher_strategy = strategy_by_name("core_single_teacher")
        teacher_result = None
        if teacher_strategy:
            teacher_result = run_strategy(core_solver, input_text, teacher_strategy, 9200.0)
            teacher_result.pop("solution", None)

        for strategy in strategies:
            result = run_strategy(core_solver, input_text, strategy)
            solution = result.pop("solution", [])
            tags = analyze_failure(features, result, solution)
            reward = compute_teacher_relative_reward(
                result, teacher_result,
                result.get("runtime_ms", 0.0), 10_000.0,
            )
            memory.log_experiment(
                instance_id=case.stem,
                features=features,
                strategy_name=strategy.name,
                strategy_params={**strategy.config_overrides, "preferred_budget_ms": strategy.preferred_budget_ms},
                result=result,
                reward=reward,
                failure_tags=tags,
                is_best=False,
            )
    memory.close()
    log(f"  [collect] Done.")


def step_train_selector() -> dict[str, Any]:
    """Train strategy selector from experiment data."""
    log("  [selector] Training policy table...")
    from training.train_selector import build_policy_table, maybe_train_sklearn

    MEMORY_DB.parent.mkdir(parents=True, exist_ok=True)
    SELECTOR_JSON.parent.mkdir(parents=True, exist_ok=True)

    if not MEMORY_DB.exists():
        log("  [selector] No memory DB found, skipping.")
        return {}

    policy = build_policy_table(MEMORY_DB)
    SELECTOR_JSON.write_text(
        json.dumps(policy, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    maybe_train_sklearn(MEMORY_DB, SELECTOR_JSON.parent)
    log(f"  [selector] Wrote {SELECTOR_JSON}")
    return policy


def step_tune_params(cases: list[Path], rounds: int = 30) -> list[dict[str, Any]]:
    """Parameter mutation search."""
    log(f"  [tune] Running {rounds} mutation rounds...")
    import core_solver
    from agent.evaluator import evaluate_output, is_better
    from agent.feature_extractor import extract_features
    from agent.failure_analyzer import analyze_failure
    from agent.reward import compute_teacher_relative_reward
    from agent.strategy_registry import Strategy, run_strategy, strategy_by_name, temporary_config

    rng = random.Random(42)
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

    # Cache teacher results
    teacher_cache: dict[Path, dict[str, Any] | None] = {}
    for case in cases:
        teacher_strat = strategy_by_name("core_single_teacher")
        if teacher_strat:
            text = case.read_text(encoding="utf-8")
            result = run_strategy(core_solver, text, teacher_strat, 9200.0)
            result.pop("solution", None)
            teacher_cache[case] = result
        else:
            teacher_cache[case] = None

    best_rows: list[dict[str, Any]] = []
    for r in range(rounds):
        patch = dict(base)
        patch["auto_strategy_budget_ms"] = round(rng.uniform(80.0, 520.0), 3)
        patch["local_search_budget_ms"] = round(rng.uniform(0.0, 4800.0), 3)
        patch["max_generated_strategies"] = rng.randint(6, 28)
        patch["pair_top_k"] = rng.randint(12, 52)
        patch["triple_top_k"] = rng.randint(8, 36)
        patch["try_triples"] = rng.random() < 0.80
        patch["ilp_time_limit_seconds"] = round(rng.choice([0.0, 0.0, 0.4, 0.8, 1.2]), 3)

        total_reward = 0.0
        valid_count = 0
        for case in cases:
            text = case.read_text(encoding="utf-8")
            with temporary_config(core_solver, patch, 3500.0):
                solution = core_solver.solve(text)
            result = evaluate_output(text, solution)
            result["runtime_ms"] = 3500.0
            reward = compute_teacher_relative_reward(result, teacher_cache.get(case), 3500.0, 10_000.0)
            total_reward += reward
            if result.get("valid"):
                valid_count += 1

        avg_reward = total_reward / max(1, len(cases))
        row = {
            "avg_reward": avg_reward,
            "valid_rate": valid_count / max(1, len(cases)),
            "config": patch,
        }
        best_rows.append(row)
        best_rows.sort(key=lambda x: float(x["avg_reward"]), reverse=True)
        best_rows = best_rows[:10]

    log(f"  [tune] Best avg_reward={best_rows[0]['avg_reward']:.1f}" if best_rows else "  [tune] No results")
    return best_rows


def step_llm_analyze() -> dict[str, Any]:
    """LLM analysis of training results."""
    log("  [llm] Analyzing training results...")
    try:
        from agent.llm_analyzer import analyze_training_results
        analysis = analyze_training_results(str(MEMORY_DB))
        log(f"  [llm] Analysis complete. Weak scenarios: {len(analysis.get('weak_scenarios', []))}")
        return analysis
    except Exception as exc:
        log(f"  [llm] Analysis failed: {exc}")
        return {"error": str(exc)}


def step_llm_generate_strategies(analysis: dict[str, Any]) -> list[dict[str, Any]]:
    """LLM generates new strategy configurations."""
    log("  [llm] Generating new strategies...")
    try:
        from agent.llm_analyzer import generate_new_strategies
        from agent.strategy_registry import list_strategy_names
        strategies = generate_new_strategies(
            analysis,
            existing_strategy_names=list_strategy_names(),
            count=4,
        )
        log(f"  [llm] Generated {len(strategies)} strategies")
        return strategies
    except Exception as exc:
        log(f"  [llm] Strategy generation failed: {exc}")
        return []


def step_inject_strategies(
    strategy_dicts: list[dict[str, Any]],
    holdout_cases: list[Path],
) -> int:
    """Validate and inject LLM-generated strategies."""
    if not strategy_dicts:
        return 0
    log(f"  [inject] Validating {len(strategy_dicts)} strategies...")
    from agent.auto_strategy_injector import inject_strategies

    import core_solver
    from agent.feature_extractor import extract_features

    test_pairs = []
    for case in holdout_cases[:3]:
        text = case.read_text(encoding="utf-8")
        features = extract_features(text).to_dict()
        test_pairs.append((text, features))

    injected = inject_strategies(strategy_dicts, test_cases=test_pairs)
    log(f"  [inject] Injected {len(injected)} strategies")
    return len(injected)


def step_evolve_solver(cases: list[Path], iterations: int) -> dict[str, Any]:
    """Run evolutionary solver training."""
    log(f"  [evolve] Running {iterations} evolution iterations...")
    from autosolver_agent import collect_cases, train
    case_args = [str(p) for p in cases]
    all_cases = collect_cases(case_args)
    summary = train(all_cases, iterations=iterations, seed=20260530, solver_path=ROOT / "solver.py")
    log(f"  [evolve] Best rank={summary['best_rank']}")
    return summary


def step_benchmark(cases: list[Path]) -> list[dict[str, Any]]:
    """Benchmark the current solver on validation cases."""
    log(f"  [bench] Benchmarking on {len(cases)} cases...")
    from benchmark import benchmark_case
    results = [benchmark_case(case) for case in cases]
    avg_penalty = sum(r.get("penalty_score", 0) for r in results) / max(1, len(results))
    valid_rate = sum(1 for r in results if r.get("valid")) / max(1, len(results))
    log(f"  [bench] avg_penalty={avg_penalty:.1f} valid_rate={valid_rate:.2f}")
    return results


def step_reflect(
    benchmark_results: list[dict[str, Any]],
    round_num: int,
    history: list[dict[str, Any]],
) -> dict[str, Any]:
    """LLM reflection on benchmark results."""
    log("  [llm] Reflecting on results...")
    try:
        from agent.llm_analyzer import reflect_on_benchmark
        reflection = reflect_on_benchmark(benchmark_results, round_num, history)
        should_continue = reflection.get("should_continue", True)
        log(f"  [llm] Reflection: continue={should_continue}, focus={reflection.get('suggested_focus', 'N/A')}")
        return reflection
    except Exception as exc:
        log(f"  [llm] Reflection failed: {exc}")
        return {"should_continue": True, "error": str(exc)}


# ============================================================
# Checkpoint Management
# ============================================================

def save_checkpoint(state: dict[str, Any]) -> None:
    """Save iteration state to disk."""
    CHECKPOINT_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def load_checkpoint() -> dict[str, Any] | None:
    """Load checkpoint if exists."""
    if CHECKPOINT_PATH.exists():
        return json.loads(CHECKPOINT_PATH.read_text(encoding="utf-8"))
    return None


def append_log(record: dict[str, Any]) -> None:
    """Append a record to the JSONL log."""
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


# ============================================================
# Main Loop
# ============================================================

def run_evolution(
    hours: float = DEFAULT_HOURS,
    max_rounds: int = DEFAULT_MAX_ROUNDS,
    llm_every: int = DEFAULT_LLM_EVERY,
    evolve_iterations: int = DEFAULT_EVOLVE_ITERATIONS,
    seed: int = 20260530,
    resume: bool = False,
) -> dict[str, Any]:
    """Run the self-evolution loop.

    Args:
        hours: Total time budget in hours.
        max_rounds: Maximum number of evolution rounds.
        llm_every: Run LLM analysis every N rounds.
        evolve_iterations: Evolution iterations per round.
        seed: Random seed base.
        resume: Resume from checkpoint.

    Returns:
        Final summary dict.
    """
    deadline = time.time() + hours * 3600.0
    history: list[dict[str, Any]] = []
    best_penalty = None
    no_improve_count = 0
    start_round = 0

    # Resume from checkpoint
    if resume:
        checkpoint = load_checkpoint()
        if checkpoint:
            start_round = checkpoint.get("round", 0) + 1
            best_penalty = checkpoint.get("best_penalty")
            history = checkpoint.get("history", [])
            no_improve_count = checkpoint.get("no_improve_count", 0)
            log(f"[resume] Resuming from round {start_round}, best_penalty={best_penalty}")

    # Ensure best solver backup exists
    if not BEST_SOLVER_PATH.exists() and (ROOT / "solver.py").exists():
        shutil.copy2(ROOT / "solver.py", BEST_SOLVER_PATH)

    fixed_cases = get_fixed_cases()
    llm_specs: list[dict[str, Any]] | None = None
    total_injected = 0

    log(f"[start] Self-evolution: {hours}h budget, max {max_rounds} rounds, LLM every {llm_every} rounds")

    for round_num in range(start_round, max_rounds):
        if time.time() >= deadline:
            log(f"[done] Time budget exhausted at round {round_num}")
            break

        log(f"\n{'='*60}")
        log(f"[round {round_num}] Starting...")
        round_start = time.time()

        # 1. Generate training cases
        case_seed = seed + round_num * 137
        training_cases = generate_training_cases(round_num, case_seed, llm_specs)
        llm_specs = None  # Reset LLM specs after use

        # 2. Collect experiments
        try:
            step_collect_experiments(training_cases)
        except Exception as exc:
            log(f"  [error] Experiment collection failed: {exc}")

        # 3. Train selector
        try:
            step_train_selector()
        except Exception as exc:
            log(f"  [error] Selector training failed: {exc}")

        # 4. Tune params (lighter each round)
        try:
            tune_results = step_tune_params(training_cases[:6], rounds=20)
        except Exception as exc:
            log(f"  [error] Param tuning failed: {exc}")
            tune_results = []

        # 5-6. LLM analysis + strategy generation (every K rounds)
        injected = 0
        if round_num % llm_every == 0 and round_num > 0:
            try:
                analysis = step_llm_analyze()
                strategy_dicts = step_llm_generate_strategies(analysis)
                holdout = fixed_cases + training_cases[:3]
                injected = step_inject_strategies(strategy_dicts, holdout)
                total_injected += injected

                # Get LLM case generation suggestions for next round
                from agent.llm_analyzer import suggest_case_generation
                perf_summary = {
                    "round": round_num,
                    "best_penalty": best_penalty,
                    "tune_results": tune_results[:3] if tune_results else [],
                    "injected_strategies": injected,
                }
                llm_specs = suggest_case_generation(perf_summary)
            except Exception as exc:
                log(f"  [error] LLM pipeline failed: {exc}")

        # 7. Evolve solver
        try:
            evolve_summary = step_evolve_solver(training_cases, evolve_iterations)
        except Exception as exc:
            log(f"  [error] Solver evolution failed: {exc}")
            evolve_summary = {}

        # 8. Benchmark
        bench_cases = fixed_cases + training_cases[:DEFAULT_HOLDOUT_COUNT]
        try:
            bench_results = step_benchmark(bench_cases)
        except Exception as exc:
            log(f"  [error] Benchmark failed: {exc}")
            bench_results = []

        # Check improvement
        avg_penalty = sum(r.get("penalty_score", 0) for r in bench_results) / max(1, len(bench_results))
        improved = best_penalty is None or avg_penalty < best_penalty
        if improved:
            best_penalty = avg_penalty
            no_improve_count = 0
            if (ROOT / "solver.py").exists():
                shutil.copy2(ROOT / "solver.py", BEST_SOLVER_PATH)
            log(f"  [IMPROVED] New best avg_penalty={best_penalty:.1f}")
        else:
            no_improve_count += 1
            # Roll back to best
            if BEST_SOLVER_PATH.exists():
                shutil.copy2(BEST_SOLVER_PATH, ROOT / "solver.py")
            log(f"  [no improve] Count: {no_improve_count}/{CONVERGENCE_PATIENCE}")

        # 9. LLM reflection (every LLM round, after benchmark)
        reflection = {}
        if round_num % llm_every == 0 and round_num > 0:
            try:
                reflection = step_reflect(bench_results, round_num, history)
                if not reflection.get("should_continue", True):
                    log(f"  [llm] Recommending stop: {reflection.get('overall_assessment', '')}")
            except Exception as exc:
                log(f"  [error] Reflection failed: {exc}")

        # 10. Log and checkpoint
        round_elapsed = time.time() - round_start
        record = {
            "round": round_num,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "avg_penalty": avg_penalty,
            "best_penalty": best_penalty,
            "improved": improved,
            "no_improve_count": no_improve_count,
            "injected": injected,
            "total_injected": total_injected,
            "round_seconds": round(round_elapsed, 1),
            "training_cases": len(training_cases),
        }
        history.append(record)
        append_log(record)

        save_checkpoint({
            "round": round_num,
            "best_penalty": best_penalty,
            "history": history[-20:],
            "no_improve_count": no_improve_count,
            "total_injected": total_injected,
        })

        log(f"  [round {round_num}] Done in {round_elapsed:.0f}s. penalty={avg_penalty:.1f} best={best_penalty:.1f}")

        # Convergence check
        if no_improve_count >= CONVERGENCE_PATIENCE:
            log(f"[converged] No improvement for {CONVERGENCE_PATIENCE} rounds. Stopping.")
            break

        # LLM suggested stop
        if reflection and not reflection.get("should_continue", True):
            log("[stopped] LLM recommended stopping.")
            break

    # Final summary
    summary = {
        "total_rounds": len(history),
        "best_penalty": best_penalty,
        "total_strategies_injected": total_injected,
        "converged": no_improve_count >= CONVERGENCE_PATIENCE,
        "history": history,
    }

    log(f"\n{'='*60}")
    log(f"[complete] {len(history)} rounds, best_penalty={best_penalty}, injected={total_injected} strategies")
    return summary


# ============================================================
# CLI Entry
# ============================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description="End-to-end self-iterating agent with LLM-driven optimization",
    )
    parser.add_argument("--hours", type=float, default=DEFAULT_HOURS, help="Total time budget in hours")
    parser.add_argument("--max-rounds", type=int, default=DEFAULT_MAX_ROUNDS, help="Maximum evolution rounds")
    parser.add_argument("--llm-every", type=int, default=DEFAULT_LLM_EVERY, help="Run LLM analysis every N rounds")
    parser.add_argument("--evolve-iter", type=int, default=DEFAULT_EVOLVE_ITERATIONS, help="Evolution iterations per round")
    parser.add_argument("--seed", type=int, default=20260530, help="Random seed")
    parser.add_argument("--resume", action="store_true", help="Resume from checkpoint")
    args = parser.parse_args()

    summary = run_evolution(
        hours=args.hours,
        max_rounds=args.max_rounds,
        llm_every=args.llm_every,
        evolve_iterations=args.evolve_iter,
        seed=args.seed,
        resume=args.resume,
    )

    # Save final summary
    summary_path = ROOT / "self_evolution_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"[saved] Summary to {summary_path}")


if __name__ == "__main__":
    main()
