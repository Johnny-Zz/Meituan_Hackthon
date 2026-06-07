"""Rule-based failure attribution for autonomous strategy improvement."""
from __future__ import annotations

from typing import Any


def analyze_failure(features: dict[str, Any], result: dict[str, Any], solution: list[Any] | None = None) -> list[str]:
    tags: list[str] = []
    solution = solution or []

    if not result.get("valid", False):
        tags.append("invalid_solution")
    if result.get("duplicate_tasks"):
        tags.append("duplicate_tasks")
    if result.get("duplicate_couriers"):
        tags.append("duplicate_couriers")
    if result.get("invalid_candidate_count", 0) > 0:
        tags.append("invalid_candidates")
    if result.get("malformed_item_count", 0) > 0:
        tags.append("malformed_output")

    missing = int(result.get("missing_tasks", 0))
    if missing > 0:
        tags.append("unmatched_orders")
        if float(features.get("courier_order_ratio", 0.0)) <= 1.05:
            tags.append("courier_scarcity")
        elif float(features.get("candidate_density", 0.0)) < 6.0:
            tags.append("candidate_sparsity")
        else:
            tags.append("matching_not_aggressive")

    bundle_ratio = float(features.get("bundle_candidate_ratio", 0.0))
    used_bundles = 0
    used_items = 0
    multi_courier_items = 0
    for item in solution:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            continue
        task_str, couriers = item
        used_items += 1
        if isinstance(task_str, str) and "," in task_str:
            used_bundles += 1
        if isinstance(couriers, list) and len(couriers) > 1:
            multi_courier_items += 1

    used_bundle_ratio = used_bundles / max(1, used_items)
    if bundle_ratio > 0.35 and used_bundle_ratio < 0.15:
        tags.append("underused_bundles")
    if used_bundle_ratio > 0.65 and float(result.get("penalty_score", 0.0)) > 1000:
        tags.append("overused_bad_bundles")
    if float(features.get("avg_willingness", 1.0)) < 0.18 and multi_courier_items == 0:
        tags.append("low_willingness_without_backups")
    if float(result.get("expected_accepted_tasks", result.get("covered_tasks", 0))) + 0.5 < float(result.get("covered_tasks", 0)):
        tags.append("low_acceptance_quality")
    if multi_courier_items > 0:
        tags.append("uses_parallel_assignment")

    # Keep deterministic order and no duplicates.
    seen = set()
    ordered = []
    for tag in tags:
        if tag not in seen:
            seen.add(tag)
            ordered.append(tag)
    return ordered


def suggest_adjustments(failure_tags: list[str]) -> dict[str, float]:
    """Map failure causes to next-round parameter deltas."""
    delta = {
        "bundle_bonus": 0.0,
        "courier_saving_bonus": 0.0,
        "risk_penalty": 0.0,
        "prob_weight": 0.0,
        "local_search_budget_ms": 0.0,
    }
    tagset = set(failure_tags)
    if "courier_scarcity" in tagset or "underused_bundles" in tagset:
        delta["bundle_bonus"] += 0.25
        delta["courier_saving_bonus"] += 0.35
    if "overused_bad_bundles" in tagset:
        delta["bundle_bonus"] -= 0.25
    if "low_willingness_without_backups" in tagset or "low_acceptance_quality" in tagset:
        delta["prob_weight"] += 0.35
        delta["risk_penalty"] += 0.45
    if "local_search_stagnation" in tagset:
        delta["local_search_budget_ms"] -= 500.0
    return delta
