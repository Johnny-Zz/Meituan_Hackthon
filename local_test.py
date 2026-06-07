#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Local evaluator for MeituanRSD_autosolver.

Usage examples:
    python local_test.py submission/solver.py cases/large_seed301.txt
    python local_test.py --test submission/solver.py --case cases/large_seed301.txt

The official platform is the final source of truth. This local_test.py is a
strict offline harness for fast iteration: it imports solve(input_text), checks
output validity, and reports an official-like expected penalty cost where lower
is better.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sys
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple

PENALTY = 100.0


@dataclass(frozen=True)
class Candidate:
    task_str: str
    task_ids: Tuple[str, ...]
    norm_task_ids: Tuple[str, ...]
    courier_id: str
    score: float
    willingness: float

    @property
    def task_count(self) -> int:
        return len(self.norm_task_ids)


def split_tasks(task_str: str) -> Tuple[str, ...]:
    return tuple(t.strip() for t in str(task_str).split(",") if t.strip())


def norm_tasks(task_str_or_tasks) -> Tuple[str, ...]:
    if isinstance(task_str_or_tasks, str):
        tasks = split_tasks(task_str_or_tasks)
    else:
        tasks = tuple(str(t).strip() for t in task_str_or_tasks if str(t).strip())
    return tuple(sorted(tasks))


def parse_case(input_text: str):
    rows: List[Candidate] = []
    all_tasks = set()
    all_couriers = set()
    lines = input_text.strip().splitlines()
    start = 1 if lines and lines[0].strip().lower().startswith("task_id_list") else 0
    for line_no, line in enumerate(lines[start:], start=start + 1):
        parts = line.rstrip("\n").split("\t")
        if len(parts) < 4:
            continue
        task_str, courier_id, score_str, willingness_str = parts[:4]
        task_ids = split_tasks(task_str)
        if not task_ids or not courier_id.strip():
            continue
        try:
            score = float(score_str)
            willingness = float(willingness_str)
        except ValueError:
            continue
        c = Candidate(
            task_str=task_str.strip(),
            task_ids=tuple(task_ids),
            norm_task_ids=tuple(sorted(task_ids)),
            courier_id=courier_id.strip(),
            score=score,
            willingness=max(0.0, min(1.0, willingness)),
        )
        rows.append(c)
        all_tasks.update(c.norm_task_ids)
        all_couriers.add(c.courier_id)
    by_exact: Dict[Tuple[str, str], Candidate] = {}
    by_norm: Dict[Tuple[Tuple[str, ...], str], Candidate] = {}
    for c in rows:
        k1 = (c.task_str, c.courier_id)
        k2 = (c.norm_task_ids, c.courier_id)
        # Keep the lower expected single-courier cost when duplicates exist.
        if k1 not in by_exact or single_cost(c) < single_cost(by_exact[k1]):
            by_exact[k1] = c
        if k2 not in by_norm or single_cost(c) < single_cost(by_norm[k2]):
            by_norm[k2] = c
    return rows, by_exact, by_norm, all_tasks, all_couriers


def single_cost(c: Candidate) -> float:
    return c.score * c.willingness + PENALTY * c.task_count * (1.0 - c.willingness)


def race_group_cost(group: Sequence[Candidate]) -> float:
    """Expected cost if multiple couriers are assigned to the same task bundle.

    This mirrors the race-style calculation used by the uploaded solver: if
    several couriers accept simultaneously, each accepting courier has equal
    chance to win; if nobody accepts, uncovered penalty is charged.
    """
    if not group:
        return 0.0
    task_count = group[0].task_count
    probs = [max(0.0, min(1.0, c.willingness)) for c in group]
    fail_prob = 1.0
    for p in probs:
        fail_prob *= 1.0 - p
    expected_score = 0.0
    for i, c in enumerate(group):
        pi = probs[i]
        if pi <= 0.0:
            continue
        dist = [1.0]
        for j, pj in enumerate(probs):
            if i == j:
                continue
            nxt = [0.0] * (len(dist) + 1)
            for count, val in enumerate(dist):
                nxt[count] += val * (1.0 - pj)
                nxt[count + 1] += val * pj
            dist = nxt
        win_weight = sum(val / float(count + 1) for count, val in enumerate(dist))
        expected_score += c.score * pi * win_weight
    return expected_score + PENALTY * task_count * fail_prob


def load_solver(solver_path: Path):
    if not solver_path.exists():
        raise FileNotFoundError(f"solver file not found: {solver_path}")
    module_name = f"candidate_solver_{abs(hash(str(solver_path.resolve())))}_{int(time.time()*1000)}"
    spec = importlib.util.spec_from_file_location(module_name, str(solver_path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import solver from {solver_path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    solve = getattr(mod, "solve", None)
    if not callable(solve):
        raise AttributeError("solver must define callable solve(input_text: str) -> list")
    return solve


def normalize_solution_item(item, idx: int):
    if not isinstance(item, (tuple, list)) or len(item) != 2:
        raise ValueError(f"solution[{idx}] must be (task_id_list_str, [courier_id, ...])")
    task_str, couriers = item
    if isinstance(couriers, str):
        couriers = [couriers]
    if not isinstance(couriers, (tuple, list)):
        raise ValueError(f"solution[{idx}] couriers must be list/tuple/string")
    task_str = str(task_str).strip()
    courier_list = [str(c).strip() for c in couriers if str(c).strip()]
    if not task_str:
        raise ValueError(f"solution[{idx}] has empty task_id_list_str")
    if not courier_list:
        raise ValueError(f"solution[{idx}] has empty courier list")
    return task_str, courier_list


def evaluate_solution(solution, by_exact, by_norm, all_tasks):
    errors: List[str] = []
    warnings: List[str] = []
    used_tasks = set()
    used_couriers = set()
    groups: List[List[Candidate]] = []

    if not isinstance(solution, list):
        raise ValueError("solve() must return a list")

    for i, item in enumerate(solution):
        try:
            task_str, couriers = normalize_solution_item(item, i)
        except ValueError as e:
            errors.append(str(e))
            continue
        task_norm = norm_tasks(task_str)
        if not task_norm:
            errors.append(f"solution[{i}] has no valid task ids")
            continue
        overlap = used_tasks.intersection(task_norm)
        if overlap:
            errors.append(f"solution[{i}] repeats already assigned tasks: {sorted(overlap)}")
        used_tasks.update(task_norm)
        if len(couriers) != len(set(couriers)):
            errors.append(f"solution[{i}] repeats courier inside one bundle: {couriers}")

        group: List[Candidate] = []
        for courier_id in couriers:
            if courier_id in used_couriers:
                errors.append(f"courier {courier_id} appears in multiple bundles")
                continue
            used_couriers.add(courier_id)
            c = by_exact.get((task_str, courier_id)) or by_norm.get((task_norm, courier_id))
            if c is None:
                errors.append(f"invalid edge: task={task_str}, courier={courier_id} not found in case candidates")
            else:
                group.append(c)
        if group:
            # All couriers in a group must refer to the same normalized task bundle.
            if any(c.norm_task_ids != task_norm for c in group):
                errors.append(f"solution[{i}] has inconsistent task bundle after normalization")
            groups.append(group)

    covered = len(used_tasks.intersection(all_tasks))
    total_tasks = len(all_tasks)
    uncovered = max(0, total_tasks - covered)
    raw_score = sum(c.score for g in groups for c in g)
    expected_cost = sum(race_group_cost(g) for g in groups) + PENALTY * uncovered
    avg_backups = 0.0
    if groups:
        avg_backups = sum(max(0, len(g) - 1) for g in groups) / len(groups)
    if uncovered:
        warnings.append(f"{uncovered} tasks uncovered")
    return {
        "valid": not errors,
        "errors": errors[:30],
        "warnings": warnings[:30],
        "covered_tasks": covered,
        "total_tasks": total_tasks,
        "uncovered_tasks": uncovered,
        "assignments": len(groups),
        "couriers_used": len(used_couriers),
        "avg_backups_per_bundle": round(avg_backups, 4),
        "raw_score_sum": round(raw_score, 6),
        "total_score": round(expected_cost, 6),
        "lower_is_better": True,
    }


def run_one(solver_path: Path, case_path: Path, print_solution: bool = False):
    input_text = case_path.read_text(encoding="utf-8", errors="ignore")
    rows, by_exact, by_norm, all_tasks, all_couriers = parse_case(input_text)
    t0 = time.perf_counter()
    try:
        solve = load_solver(solver_path)
        solution = solve(input_text)
        elapsed = time.perf_counter() - t0
        metrics = evaluate_solution(solution, by_exact, by_norm, all_tasks)
        ok = bool(metrics["valid"])
        result = {
            "ok": ok,
            "case": case_path.name,
            "solver": str(solver_path),
            "time_sec": round(elapsed, 6),
            "candidate_rows": len(rows),
            "courier_count": len(all_couriers),
            **metrics,
        }
        if print_solution:
            result["solution_preview"] = solution[:20]
        return result
    except Exception as e:
        elapsed = time.perf_counter() - t0
        return {
            "ok": False,
            "case": case_path.name,
            "solver": str(solver_path),
            "time_sec": round(elapsed, 6),
            "error": f"{type(e).__name__}: {e}",
            "traceback_tail": traceback.format_exc()[-4000:],
        }


def main(argv=None):
    ap = argparse.ArgumentParser(description="Run MeituanRSD_autosolver local test")
    ap.add_argument("solver", nargs="?", help="path to solver.py")
    ap.add_argument("case", nargs="?", help="path to case .txt")
    ap.add_argument("--test", dest="test", help="judge_server-compatible solver path")
    ap.add_argument("--case", dest="case_opt", help="judge_server-compatible case path")
    ap.add_argument("--json", action="store_true", help="print only JSON")
    ap.add_argument("--print-solution", action="store_true")
    args = ap.parse_args(argv)

    solver_path = Path(args.test or args.solver or "submission/solver.py")
    case_path = Path(args.case_opt or args.case or "cases/large_seed301.txt")
    result = run_one(solver_path, case_path, print_solution=args.print_solution)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"case: {result.get('case')}")
        print(f"solver: {result.get('solver')}")
        print(f"time: {result.get('time_sec')}s")
        print(f"valid: {result.get('valid', result.get('ok'))}")
        if result.get("error"):
            print(f"error: {result['error']}")
        print(f"covered_tasks: {result.get('covered_tasks')}/{result.get('total_tasks')}")
        print(f"assignments: {result.get('assignments')}")
        print(f"couriers_used: {result.get('couriers_used')}")
        print(f"avg_backups_per_bundle: {result.get('avg_backups_per_bundle')}")
        print(f"total_score: {result.get('total_score')}")
        print(f"raw_score_sum: {result.get('raw_score_sum')}")
        print("lower_is_better: True")
        if result.get("warnings"):
            print("warnings:", "; ".join(result["warnings"]))
        if result.get("errors"):
            print("errors:", "; ".join(result["errors"][:8]))
        print("JSON_RESULT=" + json.dumps(result, ensure_ascii=False, sort_keys=True))
    sys.exit(0 if result.get("ok") else 1)


if __name__ == "__main__":
    main()
