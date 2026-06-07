"""Automatic strategy injection from LLM-generated configurations.

Takes strategy dicts from llm_analyzer.generate_new_strategies(), validates
them on a small holdout set, and injects the survivors into the runtime
strategy registry. Also supports writing to the strategy_registry.py file
for persistence across restarts.
"""
from __future__ import annotations

import ast
import json
import shutil
import time
from pathlib import Path
from typing import Any

import core_solver
from agent.evaluator import evaluate_output, is_better
from agent.strategy_registry import (
    Strategy,
    add_strategy,
    remove_strategy,
    list_strategy_names,
    run_strategy,
    temporary_config,
)


def dict_to_strategy(d: dict[str, Any]) -> Strategy | None:
    """Convert an LLM-generated dict to a Strategy object.

    Returns None if required fields are missing or invalid.
    """
    try:
        name = str(d["name"]).strip()
        family = str(d["family"]).strip()
        description = str(d.get("description", ""))
        if not name or not family:
            return None
        if family not in ("greedy", "hybrid", "teacher", "risk_aware", "exact"):
            family = "hybrid"

        config_overrides = dict(d.get("config_overrides", {}))
        # Sanitize: only keep numeric/bool/string values
        clean_config = {}
        for k, v in config_overrides.items():
            if isinstance(v, (int, float, bool, str)):
                clean_config[k] = v
            elif isinstance(v, list):
                clean_config[k] = v  # strategies list

        scenario_prior = dict(d.get("scenario_prior", {}))
        preferred_budget_ms = float(d.get("preferred_budget_ms", 3000.0))
        min_budget_ms = float(d.get("min_budget_ms", 700.0))

        return Strategy(
            name=name,
            family=family,
            description=description,
            config_overrides=clean_config,
            scenario_prior={k: float(v) for k, v in scenario_prior.items()},
            preferred_budget_ms=max(500.0, min(9500.0, preferred_budget_ms)),
            min_budget_ms=max(200.0, min(min_budget_ms, preferred_budget_ms)),
        )
    except (KeyError, TypeError, ValueError):
        return None


def validate_strategy(
    strategy: Strategy,
    test_cases: list[tuple[str, dict[str, Any]]],
    baseline_results: list[dict[str, Any]] | None = None,
    budget_ms: float | None = None,
) -> dict[str, Any]:
    """Quickly validate a strategy on a small case set.

    Returns a validation report with avg_reward, coverage stats, and whether
    the strategy is at least as good as the baseline on each case.
    """
    budget = budget_ms or strategy.preferred_budget_ms
    results = []
    is_better_count = 0

    for i, (input_text, _features) in enumerate(test_cases):
        try:
            start = time.perf_counter()
            with temporary_config(core_solver, strategy.config_overrides, budget):
                solution = core_solver.solve(input_text)
            elapsed = (time.perf_counter() - start) * 1000.0

            result = evaluate_output(input_text, solution)
            result["runtime_ms"] = elapsed
            result["strategy_name"] = strategy.name
            results.append(result)

            if baseline_results and i < len(baseline_results):
                if is_better(result, baseline_results[i]):
                    is_better_count += 1
        except Exception as exc:
            results.append({
                "valid": False,
                "error": str(exc),
                "covered_tasks": 0,
                "total_tasks": 0,
                "penalty_score": float("inf"),
            })

    valid_count = sum(1 for r in results if r.get("valid"))
    total_tasks = sum(r.get("total_tasks", 0) for r in results)
    covered_tasks = sum(r.get("covered_tasks", 0) for r in results)
    avg_penalty = sum(r.get("penalty_score", 0) for r in results) / max(1, len(results))

    return {
        "strategy_name": strategy.name,
        "valid_rate": valid_count / max(1, len(results)),
        "coverage_rate": covered_tasks / max(1, total_tasks),
        "avg_penalty": avg_penalty,
        "better_than_baseline": is_better_count,
        "total_cases": len(results),
        "passed": valid_count >= len(results) * 0.8,
    }


def inject_strategies(
    strategy_dicts: list[dict[str, Any]],
    test_cases: list[tuple[str, dict[str, Any]]] | None = None,
    baseline_results: list[dict[str, Any]] | None = None,
    min_pass_rate: float = 0.8,
) -> list[Strategy]:
    """Convert, validate, and inject LLM-generated strategies.

    Args:
        strategy_dicts: Raw dicts from LLM output.
        test_cases: Optional (input_text, features) pairs for validation.
        baseline_results: Optional baseline results to compare against.
        min_pass_rate: Minimum fraction of valid runs to pass validation.

    Returns:
        List of successfully injected Strategy objects.
    """
    existing_names = set(list_strategy_names())
    injected = []

    for d in strategy_dicts:
        strategy = dict_to_strategy(d)
        if strategy is None:
            print(f"[injector] Skipped invalid strategy dict: {d.get('name', '?')}")
            continue
        if strategy.name in existing_names:
            print(f"[injector] Strategy '{strategy.name}' already exists, skipping")
            continue

        # Validate if test cases provided
        if test_cases:
            report = validate_strategy(strategy, test_cases, baseline_results)
            if not report["passed"]:
                print(f"[injector] Strategy '{strategy.name}' failed validation: {report}")
                continue
            print(f"[injector] Strategy '{strategy.name}' validated: coverage={report['coverage_rate']:.3f} penalty={report['avg_penalty']:.1f}")

        if add_strategy(strategy):
            injected.append(strategy)
            existing_names.add(strategy.name)
            print(f"[injector] Injected strategy: {strategy.name}")

    return injected


def write_strategies_to_file(
    strategies: list[Strategy],
    registry_path: str | Path | None = None,
) -> int:
    """Persist LLM-generated strategies to the strategy_registry.py file.

    Appends Strategy(...) definitions after the _BASE_STRATEGIES list.
    Returns the number of strategies written.
    """
    if registry_path is None:
        registry_path = Path(__file__).parent / "strategy_registry.py"
    else:
        registry_path = Path(registry_path)

    source = registry_path.read_text(encoding="utf-8")

    # Find the insertion point: after the closing ] of _BASE_STRATEGIES
    marker = "# LLM-generated strategies (auto-injected)"
    if marker in source:
        # Find the last injected strategy block
        parts = source.split(marker)
        insertion_after = parts[-1]
        # Find end of last injected Strategy(...)
        last_strat_end = insertion_after.rfind("),")
        if last_strat_end >= 0:
            insert_pos_in_after = last_strat_end + 2
            before = marker + insertion_after[:insert_pos_in_after]
            after = insertion_after[insert_pos_in_after:]
        else:
            before = parts[0] + marker + "\n"
            after = "\n" + "\n".join(parts[1:]) if len(parts) > 1 else ""
    else:
        # First injection: find end of STRATEGIES list construction
        marker2 = "STRATEGIES: list[Strategy] = []"
        if marker2 not in source:
            print(f"[injector] Could not find insertion point in {registry_path}")
            return 0
        before_split = source.split(marker2)
        before = before_split[0] + marker2
        after = "\n".join(before_split[1:])
        # Insert after the STRATEGIES population loop
        loop_end = after.find("\n\ndef ")
        if loop_end >= 0:
            before = before + after[:loop_end]
            after = after[loop_end:]
        else:
            before = before + after
            after = ""

    # Generate strategy code
    lines = [f"\n\n{marker}\n"]
    for s in strategies:
        lines.append(f"add_strategy(Strategy(")
        lines.append(f"    name={s.name!r},")
        lines.append(f"    family={s.family!r},")
        lines.append(f"    description={s.description!r},")
        lines.append(f"    config_overrides={json.dumps(s.config_overrides, ensure_ascii=False)},")
        if s.scenario_prior:
            lines.append(f"    scenario_prior={json.dumps(s.scenario_prior, ensure_ascii=False)},")
        lines.append(f"    min_budget_ms={s.min_budget_ms},")
        lines.append(f"    preferred_budget_ms={s.preferred_budget_ms},")
        lines.append(f"))\n")

    new_source = before + "\n".join(lines) + after

    # Backup original
    backup_path = registry_path.with_suffix(".py.bak")
    if not backup_path.exists():
        shutil.copy2(registry_path, backup_path)

    registry_path.write_text(new_source, encoding="utf-8")
    print(f"[injector] Wrote {len(strategies)} strategies to {registry_path}")
    return len(strategies)


def prune_underperformers(
    min_trials: int = 5,
    min_avg_reward: float = -1_000_000.0,
    memory_path: str = "memory/experiments.sqlite",
) -> list[str]:
    """Remove strategies that consistently underperform.

    Reads from the experiment database and removes strategies that have
    been tried enough times but have a very low average reward.
    """
    try:
        import sqlite3
        conn = sqlite3.connect(memory_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT strategy_name, AVG(reward) as avg_reward, COUNT(*) as n
            FROM experiments
            GROUP BY strategy_name
            HAVING n >= ? AND avg_reward < ?
        """, (min_trials, min_avg_reward)).fetchall()
        conn.close()
    except Exception:
        return []

    pruned = []
    for row in rows:
        name = row["strategy_name"]
        # Don't prune teacher strategies
        if "teacher" in name:
            continue
        if remove_strategy(name):
            pruned.append(name)
            print(f"[injector] Pruned underperformer: {name} (avg_reward={row['avg_reward']:.1f}, trials={row['n']})")

    return pruned
