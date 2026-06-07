"""Fast official-like evaluator used by the learning loop."""
from __future__ import annotations

from typing import Any


def split_tasks(task_str: str) -> list[str]:
    return [task.strip() for task in task_str.split(",") if task.strip()]


def load_candidate_lookup(input_text: str) -> tuple[dict[tuple[str, str], tuple[float, float]], set[str]]:
    lines = input_text.strip().splitlines()
    start = 1 if lines and lines[0].strip().startswith("task_id_list") else 0
    lookup: dict[tuple[str, str], tuple[float, float]] = {}
    all_tasks: set[str] = set()
    for line in lines[start:]:
        parts = line.strip().split("\t")
        if len(parts) < 4:
            continue
        task_str, courier_id, score_str, willingness_str = parts[:4]
        try:
            score = float(score_str)
            willingness = float(willingness_str)
        except ValueError:
            continue
        key = (task_str.strip(), courier_id.strip())
        if key not in lookup or score < lookup[key][0]:
            lookup[key] = (score, willingness)
        all_tasks.update(split_tasks(task_str))
    return lookup, all_tasks


def evaluate_output(input_text: str, solution: list[Any]) -> dict[str, Any]:
    lookup, all_tasks = load_candidate_lookup(input_text)
    task_count: dict[str, int] = {}
    courier_count: dict[str, int] = {}
    total_score = 0.0
    penalty_score = 0.0
    parallel_penalty_score = 0.0
    expected_accepted_tasks = 0.0
    invalid: list[dict[str, Any]] = []
    malformed: list[Any] = []

    if not isinstance(solution, list):
        return {
            "valid": False,
            "covered_tasks": 0,
            "total_tasks": len(all_tasks),
            "missing_tasks": len(all_tasks),
            "total_score": float("inf"),
            "penalty_score": float("inf"),
            "parallel_penalty_score": float("inf"),
            "expected_accepted_tasks": 0.0,
            "solution_items": 0,
            "duplicate_tasks": [],
            "duplicate_couriers": [],
            "unknown_tasks": [],
            "invalid_candidate_count": 0,
            "malformed_item_count": 1,
        }

    for index, item in enumerate(solution):
        if not isinstance(item, (tuple, list)) or len(item) != 2:
            malformed.append(index)
            continue
        task_str, courier_ids = item
        if not isinstance(task_str, str) or not isinstance(courier_ids, list) or not courier_ids:
            malformed.append(index)
            continue
        task_ids = split_tasks(task_str)
        for task_id in task_ids:
            task_count[task_id] = task_count.get(task_id, 0) + 1
        reject_probability = 1.0
        weighted_score = 0.0
        first_accept_score = 0.0
        for courier_id in courier_ids:
            if not isinstance(courier_id, str):
                malformed.append(index)
                continue
            courier_count[courier_id] = courier_count.get(courier_id, 0) + 1
            candidate = lookup.get((task_str, courier_id))
            if candidate is None:
                invalid.append({"index": index, "task_str": task_str, "courier_id": courier_id})
                continue
            score, willingness = candidate
            total_score += score
            p = max(0.0, min(1.0, willingness))
            weighted_score += score * p
            first_accept_score += reject_probability * score * p
            reject_probability *= 1.0 - p
        accepted_probability = 1.0 - reject_probability
        expected_accepted_tasks += len(task_ids) * accepted_probability
        parallel_penalty_score += weighted_score + 100.0 * len(task_ids) * reject_probability
        penalty_score += first_accept_score + 100.0 * len(task_ids) * reject_probability

    covered = set(task_count) & all_tasks
    duplicate_tasks = sorted(t for t, count in task_count.items() if count > 1)
    duplicate_couriers = sorted(c for c, count in courier_count.items() if count > 1)
    unknown_tasks = sorted(set(task_count) - all_tasks)
    missing = sorted(all_tasks - covered)
    penalty_score += 100.0 * len(missing)
    parallel_penalty_score += 100.0 * len(missing)
    valid = not (duplicate_tasks or duplicate_couriers or unknown_tasks or invalid or malformed)

    return {
        "valid": valid,
        "covered_tasks": len(covered),
        "total_tasks": len(all_tasks),
        "missing_tasks": len(missing),
        "missing_task_ids": missing,
        "total_score": round(total_score, 6),
        "penalty_score": round(penalty_score, 6),
        "parallel_penalty_score": round(parallel_penalty_score, 6),
        "expected_accepted_tasks": round(expected_accepted_tasks, 6),
        "solution_items": len(solution),
        "duplicate_tasks": duplicate_tasks,
        "duplicate_couriers": duplicate_couriers,
        "unknown_tasks": unknown_tasks,
        "invalid_candidate_count": len(invalid),
        "malformed_item_count": len(malformed),
    }


def result_key(result: dict[str, Any]) -> tuple[int, int, float, float, float]:
    """Lower is better.  Enforce valid > covered tasks > expected penalty."""
    total = int(result.get("total_tasks", 0))
    covered = int(result.get("covered_tasks", 0))
    return (
        0 if result.get("valid") else 1,
        total - covered,
        float(result.get("penalty_score", float("inf"))),
        float(result.get("parallel_penalty_score", result.get("penalty_score", float("inf")))),
        float(result.get("total_score", float("inf"))),
    )


def is_better(new_result: dict[str, Any] | None, old_result: dict[str, Any] | None) -> bool:
    if new_result is None:
        return False
    if old_result is None:
        return True
    return result_key(new_result) < result_key(old_result)
