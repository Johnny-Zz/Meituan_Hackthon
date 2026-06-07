"""Feature extraction and scenario classification for the Meituan AutoSolver Agent.

The online solver must decide very quickly which search policy to run.  This
module converts the raw candidate table into stable numerical features that can
be used by a contextual-bandit / strategy-selector model.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from hashlib import sha1
from math import sqrt
from statistics import mean
from typing import Any


def _split_tasks(task_str: str) -> tuple[str, ...]:
    return tuple(t.strip() for t in task_str.split(",") if t.strip())


def _safe_mean(values: list[float], default: float = 0.0) -> float:
    return sum(values) / len(values) if values else default


def _std(values: list[float]) -> float:
    if len(values) <= 1:
        return 0.0
    mu = _safe_mean(values)
    return sqrt(sum((v - mu) ** 2 for v in values) / len(values))


def _quantile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    pos = min(len(values) - 1, max(0, int(round(q * (len(values) - 1)))))
    return float(values[pos])


@dataclass(frozen=True)
class InstanceFeatures:
    instance_hash: str
    num_orders: int
    num_couriers: int
    num_candidates: int
    courier_order_ratio: float
    candidate_density: float
    avg_candidates_per_order: float
    avg_candidates_per_courier: float
    avg_willingness: float
    min_willingness: float
    max_willingness: float
    std_willingness: float
    q10_willingness: float
    q90_willingness: float
    avg_score: float
    min_score: float
    max_score: float
    std_score: float
    q10_score: float
    q90_score: float
    single_candidate_ratio: float
    bundle_candidate_ratio: float
    avg_tasks_per_candidate: float
    max_candidates_for_one_order: int
    min_candidates_for_one_order: int
    avg_order_degree: float
    max_candidates_for_one_courier: int
    avg_courier_degree: float
    score_per_task_avg: float
    score_per_task_std: float
    low_willingness_ratio: float
    high_willingness_ratio: float
    cheap_candidate_ratio: float
    scenario_type: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def parse_rows(input_text: str) -> list[tuple[str, str, float, float]]:
    rows: list[tuple[str, str, float, float]] = []
    lines = input_text.strip().splitlines()
    start = 1 if lines and lines[0].strip().startswith("task_id_list") else 0
    for line in lines[start:]:
        parts = line.strip().split("\t")
        if len(parts) < 4:
            continue
        task_str, courier_id, score_str, willingness_str = parts[:4]
        try:
            rows.append((task_str.strip(), courier_id.strip(), float(score_str), float(willingness_str)))
        except ValueError:
            continue
    return rows


def classify_scenario(features: dict[str, Any] | InstanceFeatures) -> str:
    f = features.to_dict() if isinstance(features, InstanceFeatures) else features
    avg_w = float(f.get("avg_willingness", 0.0))
    ratio = float(f.get("courier_order_ratio", 0.0))
    density = float(f.get("candidate_density", 0.0))
    bundle_ratio = float(f.get("bundle_candidate_ratio", 0.0))
    score_std = float(f.get("std_score", 0.0))

    if avg_w < 0.18:
        return "low_willingness"
    if ratio <= 1.05:
        return "scarce_couriers"
    if density < 6.0:
        return "sparse_candidates"
    if bundle_ratio > 0.55:
        return "bundle_heavy"
    if score_std > max(8.0, abs(float(f.get("avg_score", 0.0))) * 0.75):
        return "high_score_variance"
    return "normal"


def extract_features(input_text: str) -> InstanceFeatures:
    rows = parse_rows(input_text)
    digest = sha1(input_text.encode("utf-8", errors="ignore")).hexdigest()[:16]
    if not rows:
        return InstanceFeatures(
            instance_hash=digest,
            num_orders=0,
            num_couriers=0,
            num_candidates=0,
            courier_order_ratio=0.0,
            candidate_density=0.0,
            avg_candidates_per_order=0.0,
            avg_candidates_per_courier=0.0,
            avg_willingness=0.0,
            min_willingness=0.0,
            max_willingness=0.0,
            std_willingness=0.0,
            q10_willingness=0.0,
            q90_willingness=0.0,
            avg_score=0.0,
            min_score=0.0,
            max_score=0.0,
            std_score=0.0,
            q10_score=0.0,
            q90_score=0.0,
            single_candidate_ratio=0.0,
            bundle_candidate_ratio=0.0,
            avg_tasks_per_candidate=0.0,
            max_candidates_for_one_order=0,
            min_candidates_for_one_order=0,
            avg_order_degree=0.0,
            max_candidates_for_one_courier=0,
            avg_courier_degree=0.0,
            score_per_task_avg=0.0,
            score_per_task_std=0.0,
            low_willingness_ratio=0.0,
            high_willingness_ratio=0.0,
            cheap_candidate_ratio=0.0,
            scenario_type="empty",
        )

    order_degree: dict[str, int] = {}
    courier_degree: dict[str, int] = {}
    scores: list[float] = []
    willingness: list[float] = []
    tasks_per_candidate: list[int] = []
    score_per_task: list[float] = []
    single_count = 0
    bundle_count = 0

    for task_str, courier_id, score, prob in rows:
        task_ids = _split_tasks(task_str)
        if len(task_ids) <= 1:
            single_count += 1
        else:
            bundle_count += 1
        tasks_per_candidate.append(len(task_ids))
        scores.append(score)
        willingness.append(prob)
        score_per_task.append(score / max(1, len(task_ids)))
        courier_degree[courier_id] = courier_degree.get(courier_id, 0) + 1
        for task_id in task_ids:
            order_degree[task_id] = order_degree.get(task_id, 0) + 1

    num_orders = len(order_degree)
    num_couriers = len(courier_degree)
    num_candidates = len(rows)
    avg_score = _safe_mean(scores)
    cheap_threshold = _quantile(scores, 0.25)

    data: dict[str, Any] = {
        "instance_hash": digest,
        "num_orders": num_orders,
        "num_couriers": num_couriers,
        "num_candidates": num_candidates,
        "courier_order_ratio": num_couriers / max(1, num_orders),
        "candidate_density": num_candidates / max(1, num_orders * max(1, num_couriers)),
        "avg_candidates_per_order": num_candidates / max(1, num_orders),
        "avg_candidates_per_courier": num_candidates / max(1, num_couriers),
        "avg_willingness": _safe_mean(willingness),
        "min_willingness": min(willingness),
        "max_willingness": max(willingness),
        "std_willingness": _std(willingness),
        "q10_willingness": _quantile(willingness, 0.10),
        "q90_willingness": _quantile(willingness, 0.90),
        "avg_score": avg_score,
        "min_score": min(scores),
        "max_score": max(scores),
        "std_score": _std(scores),
        "q10_score": _quantile(scores, 0.10),
        "q90_score": _quantile(scores, 0.90),
        "single_candidate_ratio": single_count / max(1, num_candidates),
        "bundle_candidate_ratio": bundle_count / max(1, num_candidates),
        "avg_tasks_per_candidate": _safe_mean([float(x) for x in tasks_per_candidate]),
        "max_candidates_for_one_order": max(order_degree.values()) if order_degree else 0,
        "min_candidates_for_one_order": min(order_degree.values()) if order_degree else 0,
        "avg_order_degree": _safe_mean([float(x) for x in order_degree.values()]),
        "max_candidates_for_one_courier": max(courier_degree.values()) if courier_degree else 0,
        "avg_courier_degree": _safe_mean([float(x) for x in courier_degree.values()]),
        "score_per_task_avg": _safe_mean(score_per_task),
        "score_per_task_std": _std(score_per_task),
        "low_willingness_ratio": sum(1 for p in willingness if p < 0.2) / max(1, len(willingness)),
        "high_willingness_ratio": sum(1 for p in willingness if p >= 0.7) / max(1, len(willingness)),
        "cheap_candidate_ratio": sum(1 for s in scores if s <= cheap_threshold) / max(1, len(scores)),
    }
    data["scenario_type"] = classify_scenario(data)
    return InstanceFeatures(**data)
