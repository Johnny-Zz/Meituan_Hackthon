"""Online MetaController for the self-learning AutoSolver Agent.

The MetaController is the 10-second runtime brain:
1. extract features;
2. read the persistent experience memory / learned policy table;
3. rank candidate strategies;
4. allocate time budget and run the best few strategies;
5. evaluate, attribute failures, and write back experience.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import time
from typing import Any

from agent.evaluator import evaluate_output, is_better
from agent.failure_analyzer import analyze_failure, suggest_adjustments
from agent.feature_extractor import extract_features
from agent.memory import AgentMemory
from agent.reward import compute_reward
from agent.strategy_registry import Strategy, get_strategies, run_strategy, temporary_config


DEFAULT_MODEL_PATH = Path(os.environ.get("MEITUAN_AGENT_MODEL", "models/strategy_selector.json"))


class AutoSolverAgent:
    def __init__(
        self,
        *,
        model_path: str | Path | None = None,
        memory_path: str | Path | None = None,
        allow_parallel_assignment: bool | None = None,
        enable_memory: bool | None = None,
        total_budget_ms: float | None = None,
    ):
        if allow_parallel_assignment is None:
            allow_parallel_assignment = os.environ.get("MEITUAN_ALLOW_PARALLEL", "0") == "1"
        if enable_memory is None:
            enable_memory = (
                os.environ.get("MEITUAN_ENABLE_MEMORY", "0") == "1"
                and os.environ.get("MEITUAN_DISABLE_MEMORY", "0") != "1"
            )
        if total_budget_ms is None:
            total_budget_ms = float(os.environ.get("MEITUAN_AGENT_BUDGET_MS", "9200"))
        self.allow_parallel_assignment = bool(allow_parallel_assignment)
        self.total_budget_ms = float(total_budget_ms)
        self.model_path = Path(model_path) if model_path is not None else DEFAULT_MODEL_PATH
        self.policy_table = self._load_policy_table(self.model_path)
        self.rf_selector = self._load_rf_selector(self.model_path.with_name('strategy_selector_rf.joblib'))
        self.memory = AgentMemory(memory_path) if enable_memory else None
        self.last_trace: dict[str, Any] = {}

    @staticmethod
    def _load_policy_table(path: Path) -> dict[str, Any]:
        try:
            if path.exists():
                return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
        return {"scenarios": {}}


    @staticmethod
    def _load_rf_selector(path: Path) -> dict[str, Any] | None:
        try:
            if not path.exists():
                return None
            import joblib  # type: ignore
            payload = joblib.load(path)
            if isinstance(payload, dict) and "model" in payload and "feature_names" in payload:
                return payload
        except Exception:
            pass
        return None

    def _rf_probabilities(self, features: dict[str, Any]) -> dict[str, float]:
        if not self.rf_selector:
            return {}
        try:
            model = self.rf_selector["model"]
            names = self.rf_selector["feature_names"]
            row = [[float(features.get(name, 0.0)) for name in names]]
            if not hasattr(model, "predict_proba"):
                pred = str(model.predict(row)[0])
                return {pred: 1.0}
            probs = model.predict_proba(row)[0]
            return {str(label): float(prob) for label, prob in zip(model.classes_, probs)}
        except Exception:
            return {}

    def rank_strategies(self, features: dict[str, Any]) -> list[Strategy]:
        scenario = str(features.get("scenario_type", "normal"))
        strategies = get_strategies(self.allow_parallel_assignment)
        total_trials = 1
        memory_stats: dict[str, dict[str, Any]] = {}
        learned_stats: dict[str, dict[str, Any]] = {}
        rf_probs = self._rf_probabilities(features)

        if self.memory is not None:
            for strategy in strategies:
                stats = self.memory.get_strategy_stats(scenario, strategy.name)
                if stats:
                    memory_stats[strategy.name] = stats
                    total_trials += int(stats.get("trial_count", 0))

        learned_stats = self.policy_table.get("scenarios", {}).get(scenario, {}) if isinstance(self.policy_table, dict) else {}

        ranked: list[tuple[float, Strategy]] = []
        for strategy in strategies:
            score = strategy.scenario_prior.get(scenario, 0.0)
            # Stable priors for safe online behavior.  Teacher strategies are
            # the performance floor: without a trained policy table, the Agent
            # should stay close to the original high-performance solver.
            base_name = strategy.base_strategy_name or strategy.name
            if strategy.family == "teacher":
                score += 0.35
                if not self.allow_parallel_assignment and base_name == "core_single_teacher":
                    score += 0.20
                if self.allow_parallel_assignment and base_name == "core_parallel_teacher":
                    score += 0.20
            if base_name == "single_balanced_search":
                score += 0.18
            if scenario == "scarce_couriers" and base_name == "single_scarce_bundle_repair":
                score += 0.30
            if scenario == "low_willingness" and strategy.allow_parallel_assignment:
                score += 0.45
            if scenario == "low_willingness" and not self.allow_parallel_assignment:
                score += 0.12 if base_name == "single_balanced_search" else 0.0

            # Optional RandomForest selector produced by training/train_selector.py.
            if strategy.name in rf_probs:
                score += 0.55 * float(rf_probs[strategy.name])

            # Learned table produced by training/train_selector.py.
            learned = learned_stats.get(strategy.name)
            if isinstance(learned, dict):
                avg_reward = float(learned.get("avg_reward", 0.0))
                success_rate = float(learned.get("success_rate", 0.0))
                score += max(-1.5, min(1.5, avg_reward / 1000.0)) + success_rate * 0.25

            # Online contextual-bandit memory; UCB bonus only during local experiments.
            stats = memory_stats.get(strategy.name)
            if stats:
                avg_reward = float(stats.get("avg_reward", 0.0))
                trials = max(1, int(stats.get("trial_count", 1)))
                success_count = int(stats.get("success_count", 0))
                success_rate = success_count / trials
                score += max(-1.5, min(1.5, avg_reward / 1000.0)) + success_rate * 0.20
                if os.environ.get("MEITUAN_AGENT_EXPLORE", "0") == "1":
                    score += 0.10 * strategy.exploration_weight * math.sqrt(math.log(total_trials + 1) / trials)
            elif os.environ.get("MEITUAN_AGENT_EXPLORE", "0") == "1":
                score += 0.12 * strategy.exploration_weight

            ranked.append((score, strategy))

        ranked.sort(key=lambda pair: pair[0], reverse=True)
        return [strategy for _score, strategy in ranked]

    def allocate_budget(self, strategy: Strategy, remaining_ms: float, attempts_done: int) -> float:
        reserve = 550.0 if attempts_done == 0 else 350.0
        usable = max(0.0, remaining_ms - reserve)
        if usable <= strategy.min_budget_ms:
            return 0.0
        # First attempt gets a realistic budget. Later attempts are verification / rescue slices.
        if attempts_done == 0:
            return min(strategy.preferred_budget_ms, usable)
        decay = 0.72 ** attempts_done
        return min(max(strategy.min_budget_ms, strategy.preferred_budget_ms * decay), usable)

    def solve(self, input_text: str, *, core_solver: Any | None = None) -> list[Any]:
        if core_solver is None:
            import core_solver as core_solver  # type: ignore

        start = time.perf_counter()
        features_obj = extract_features(input_text)
        features = features_obj.to_dict()
        scenario = features["scenario_type"]
        ranked = self.rank_strategies(features)

        best_solution: list[Any] | None = None
        best_result: dict[str, Any] | None = None
        attempts: list[dict[str, Any]] = []
        failure_history: list[str] = []

        max_attempts = int(os.environ.get("MEITUAN_AGENT_MAX_ATTEMPTS", "3"))
        hard_budget_ms = self.total_budget_ms

        for attempts_done, strategy in enumerate(ranked[:max_attempts]):
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            remaining_ms = hard_budget_ms - elapsed_ms
            budget_ms = self.allocate_budget(strategy, remaining_ms, attempts_done)
            if budget_ms <= 0.0:
                break
            try:
                result = run_strategy(core_solver, input_text, strategy, budget_ms)
            except Exception as exc:
                result = {
                    "valid": False,
                    "covered_tasks": 0,
                    "total_tasks": int(features.get("num_orders", 0)),
                    "missing_tasks": int(features.get("num_orders", 0)),
                    "total_score": float("inf"),
                    "penalty_score": float("inf"),
                    "parallel_penalty_score": float("inf"),
                    "expected_accepted_tasks": 0.0,
                    "runtime_ms": round((time.perf_counter() - start) * 1000.0, 3),
                    "strategy_name": strategy.name,
                    "solution": [],
                    "error": repr(exc),
                }

            solution = result.pop("solution", [])
            failure_tags = analyze_failure(features, result, solution)
            failure_history.extend(failure_tags)
            reward = compute_reward(result, float(result.get("runtime_ms", 0.0)), hard_budget_ms)
            attempts.append({
                "strategy": strategy.name,
                "budget_ms": round(budget_ms, 3),
                "result": result,
                "failure_tags": failure_tags,
                "reward": reward,
            })
            if is_better(result, best_result):
                best_result = result
                best_solution = solution

            if self.memory is not None:
                self.memory.log_experiment(
                    instance_id=features["instance_hash"],
                    features=features,
                    strategy_name=strategy.name,
                    strategy_params=strategy.config_overrides,
                    result=result,
                    reward=reward,
                    failure_tags=failure_tags,
                    is_best=False,
                )

            # Stop early if strict full coverage and low penalty enough; leave safety margin.
            if result.get("valid") and result.get("missing_tasks", 0) == 0 and attempts_done >= 1:
                if (time.perf_counter() - start) * 1000.0 > hard_budget_ms * 0.45:
                    break

            if hard_budget_ms - (time.perf_counter() - start) * 1000.0 < 900.0:
                break

        if best_solution is None:
            # Emergency fallback: call the core solver once with conservative single-courier output.
            fallback_budget = max(500.0, hard_budget_ms - (time.perf_counter() - start) * 1000.0 - 150.0)
            with temporary_config(
                core_solver,
                {
                    "force_single_courier_output": not self.allow_parallel_assignment,
                    "enable_multi_courier_output": self.allow_parallel_assignment,
                },
                fallback_budget,
            ):
                best_solution = core_solver.solve(input_text)
            best_result = evaluate_output(input_text, best_solution)

        # Mark the best attempt as best in memory as a separate compact record.
        if self.memory is not None and best_result is not None:
            best_strategy = best_result.get("strategy_name", "fallback_core_solver")
            self.memory.log_experiment(
                instance_id=features["instance_hash"],
                features=features,
                strategy_name=str(best_strategy),
                strategy_params={"final_best": True},
                result=best_result,
                reward=compute_reward(best_result, float(best_result.get("runtime_ms", 0.0)), hard_budget_ms),
                failure_tags=analyze_failure(features, best_result, best_solution),
                is_best=True,
            )

        elapsed_ms = (time.perf_counter() - start) * 1000.0
        self.last_trace = {
            "instance_hash": features["instance_hash"],
            "scenario_type": scenario,
            "allow_parallel_assignment": self.allow_parallel_assignment,
            "ranked_strategies": [s.name for s in ranked],
            "attempts": attempts,
            "best_result": best_result,
            "failure_suggestions": suggest_adjustments(failure_history),
            "elapsed_ms": round(elapsed_ms, 3),
        }
        return best_solution


def solve_autonomous(input_text: str, core_solver: Any | None = None) -> list[Any]:
    return AutoSolverAgent().solve(input_text, core_solver=core_solver)
