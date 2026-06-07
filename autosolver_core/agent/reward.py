"""Reward shaping for the strategy-value model.

The online solver optimizes the official objective directly.  Offline training can
also use a teacher-relative reward, where the original high-performance
``core_solver.py`` acts as a behavior-distillation target.
"""
from __future__ import annotations

from typing import Any


INVALID_REWARD = -1_000_000_000.0
MISSING_TASK_PENALTY = 1_000_000.0


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def compute_reward(result: dict[str, Any], runtime_ms: float = 0.0, hard_time_limit_ms: float = 10_000.0) -> float:
    """Return an absolute scalar reward; larger is better.

    This is still useful for online memory and for cases where no teacher result
    has been collected.  Missing one task is intentionally much worse than any
    ordinary score improvement.
    """
    if not result or not result.get("valid", False):
        return INVALID_REWARD
    total_tasks = int(result.get("total_tasks", 0))
    covered_tasks = int(result.get("covered_tasks", 0))
    missed = max(0, total_tasks - covered_tasks)
    penalty = _safe_float(result.get("penalty_score"), 1e18)
    expected_gap = total_tasks - _safe_float(result.get("expected_accepted_tasks"), covered_tasks)
    timeout_penalty = max(0.0, runtime_ms - hard_time_limit_ms) * 1000.0
    return -missed * MISSING_TASK_PENALTY - penalty - expected_gap * 2500.0 - timeout_penalty


def compute_teacher_relative_reward(
    result: dict[str, Any],
    teacher_result: dict[str, Any] | None,
    runtime_ms: float = 0.0,
    hard_time_limit_ms: float = 10_000.0,
) -> float:
    """Reward for behavior distillation against the high-performance teacher.

    The reward is zero when the strategy matches the teacher on coverage and
    penalty, negative when it is worse, and positive only if it improves upon the
    teacher.  Coverage dominates penalty, matching the competition objective.
    """
    if teacher_result is None or not teacher_result:
        return compute_reward(result, runtime_ms, hard_time_limit_ms)
    if not result or not result.get("valid", False):
        return INVALID_REWARD

    teacher_total = int(teacher_result.get("total_tasks", result.get("total_tasks", 0)))
    teacher_covered = int(teacher_result.get("covered_tasks", 0))
    result_covered = int(result.get("covered_tasks", 0))
    coverage_gap = teacher_covered - result_covered

    reward = -max(0, coverage_gap) * MISSING_TASK_PENALTY
    # If the candidate covers more than the teacher, reward it symmetrically; this
    # lets genuine improvements survive instead of merely copying the teacher.
    if coverage_gap < 0:
        reward += abs(coverage_gap) * MISSING_TASK_PENALTY

    # Compare penalty only when both results cover the same number of tasks;
    # otherwise coverage dominates.
    if result_covered == teacher_covered:
        teacher_penalty = _safe_float(teacher_result.get("penalty_score"), 1e18)
        result_penalty = _safe_float(result.get("penalty_score"), 1e18)
        reward -= result_penalty - teacher_penalty

    # Also discourage giving up tasks relative to all known tasks, even if the
    # teacher itself is imperfect on a hard synthetic instance.
    missed_vs_total = max(0, teacher_total - result_covered)
    teacher_missed = max(0, teacher_total - teacher_covered)
    reward -= max(0, missed_vs_total - teacher_missed) * (MISSING_TASK_PENALTY * 0.25)

    reward -= max(0.0, runtime_ms - hard_time_limit_ms) * 1000.0
    return float(reward)
