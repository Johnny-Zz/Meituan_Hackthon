#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate mid-training TSV cases from official large_seed301-style candidate data.

The generator keeps the official TSV schema:
    task_id_list\tcourier_id\ttotal_score\twillingness

It creates deterministic scenario variants for DataLab / Agent training:
- high_noise_seed601: score perturbation / noisy ranking pressure
- large_seed302: large case with mild score and willingness drift
- low_willingness_seed501: lower acceptance probability distribution
- scarce_couriers_seed401: fewer available couriers
- medium_seed201/202/203: medium task/courier subsets
- small_seed100 / tiny_seed42: compact smoke-test subsets
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import os
import random
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config"
MEMORY = ROOT / "memory" / "studio"
GENERATED = ROOT / "generated_cases"
STATE_PATH = MEMORY / "current_state.json"
AGENT_LOG_PATH = MEMORY / "agent_logs.jsonl"
MANIFEST_PATH = MEMORY / "generated_cases_latest.json"
CONFIG_PATH = CONFIG / "training_config.json"

HEADER = ["task_id_list", "courier_id", "total_score", "willingness"]
SCENARIOS = [
    "high_noise_seed601",
    "large_seed301",
    "large_seed302",
    "low_willingness_seed501",
    "medium_seed201",
    "medium_seed202",
    "medium_seed203",
    "scarce_couriers_seed401",
    "small_seed100",
    "tiny_seed42",
]


def iso_now() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def clock() -> str:
    return dt.datetime.now().strftime("%H:%M:%S")


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def append_jsonl(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def append_event(agent: str, typ: str, message: str, extra: Dict[str, Any] | None = None) -> None:
    ev = {"time": clock(), "iso": iso_now(), "agent": agent, "type": typ, "message": message}
    if extra is not None:
        ev["extra"] = extra
    append_jsonl(AGENT_LOG_PATH, ev)
    st = read_json(STATE_PATH, {})
    st.setdefault("events", []).append(ev)
    st["events"] = st["events"][-180:]
    write_json(STATE_PATH, st)


def parse_rows(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8", errors="ignore", newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for r in reader:
            try:
                task_key = (r.get("task_id_list") or "").strip()
                courier = (r.get("courier_id") or "").strip()
                if not task_key or not courier:
                    continue
                rows.append({
                    "task_id_list": task_key,
                    "tasks": tuple(t.strip() for t in task_key.split(",") if t.strip()),
                    "courier_id": courier,
                    "total_score": float(r.get("total_score", "0")),
                    "willingness": float(r.get("willingness", "0")),
                })
            except Exception:
                continue
    return rows


def fmt(x: float) -> str:
    return ("%.4f" % x).rstrip("0").rstrip(".")


def write_case(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        f.write("\t".join(HEADER) + "\n")
        for r in rows:
            f.write("%s\t%s\t%s\t%s\n" % (r["task_id_list"], r["courier_id"], fmt(r["total_score"]), fmt(r["willingness"])))


def filter_subset(rows: List[Dict[str, Any]], *, task_count: int | None = None, courier_count: int | None = None, seed: int = 0) -> List[Dict[str, Any]]:
    rng = random.Random(seed)
    tasks = sorted({t for r in rows for t in r["tasks"]})
    couriers = sorted({r["courier_id"] for r in rows})
    if task_count is not None and task_count < len(tasks):
        chosen_tasks = set(rng.sample(tasks, task_count))
    else:
        chosen_tasks = set(tasks)
    if courier_count is not None and courier_count < len(couriers):
        chosen_couriers = set(rng.sample(couriers, courier_count))
    else:
        chosen_couriers = set(couriers)
    out = [dict(r) for r in rows if set(r["tasks"]).issubset(chosen_tasks) and r["courier_id"] in chosen_couriers]
    return out


def perturb(rows: List[Dict[str, Any]], *, seed: int, score_noise: float = 0.0, willingness_scale: float = 1.0, willingness_cap: float | None = None, score_bias: float = 0.0) -> List[Dict[str, Any]]:
    rng = random.Random(seed)
    out: List[Dict[str, Any]] = []
    for r in rows:
        nr = dict(r)
        noise = rng.gauss(0, score_noise) if score_noise else 0.0
        score = max(1.0, min(100.0, nr["total_score"] * (1.0 + noise) + score_bias))
        will_noise = rng.uniform(0.88, 1.12)
        will = max(0.01, min(0.9499, nr["willingness"] * willingness_scale * will_noise))
        if willingness_cap is not None:
            will = min(will, willingness_cap)
        nr["total_score"] = round(score, 4)
        nr["willingness"] = round(will, 4)
        out.append(nr)
    return out


def stats(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    tasks = sorted({t for r in rows for t in r["tasks"]})
    couriers = sorted({r["courier_id"] for r in rows})
    scores = [r["total_score"] for r in rows]
    wills = [r["willingness"] for r in rows]
    singles = sum(1 for r in rows if len(r["tasks"]) == 1)
    bundles = sum(1 for r in rows if len(r["tasks"]) > 1)
    def avg(vals: List[float]) -> float | None:
        return round(sum(vals)/len(vals), 4) if vals else None
    return {
        "rows": len(rows), "tasks": len(tasks), "couriers": len(couriers), "singles": singles, "bundles": bundles,
        "score_min": round(min(scores), 4) if scores else None,
        "score_avg": avg(scores),
        "score_max": round(max(scores), 4) if scores else None,
        "willingness_avg": avg(wills),
        "willingness_min": round(min(wills), 4) if wills else None,
        "willingness_max": round(max(wills), 4) if wills else None,
    }


def build_scenario(base: List[Dict[str, Any]], scenario: str, cfg: Dict[str, Any], seed: int) -> List[Dict[str, Any]]:
    scene_cfg = (cfg.get("scenes") or {}).get(scenario, {})
    if scenario == "large_seed301":
        return perturb(base, seed=seed, score_noise=0.0)
    if scenario == "large_seed302":
        return perturb(base, seed=302, score_noise=0.025, willingness_scale=1.0, score_bias=0.2)
    if scenario == "high_noise_seed601":
        noise = float(scene_cfg.get("score_noise", scene_cfg.get("noise_guard", 0.82)))
        return perturb(base, seed=601, score_noise=min(0.18, max(0.03, noise * 0.12)), willingness_scale=1.0)
    if scenario == "low_willingness_seed501":
        scale = float(scene_cfg.get("willingness_threshold", 0.18)) / 0.30
        return perturb(base, seed=501, score_noise=0.015, willingness_scale=max(0.18, min(0.65, scale)), willingness_cap=max(0.05, float(scene_cfg.get("willingness_threshold", 0.18))))
    if scenario == "scarce_couriers_seed401":
        ratio = float(scene_cfg.get("courier_ratio_gate", 0.95))
        subset = filter_subset(base, courier_count=max(24, int(80 * min(0.8, max(0.35, ratio * 0.55)))), seed=401)
        return perturb(subset, seed=401, score_noise=0.01)
    if scenario == "medium_seed201":
        return perturb(filter_subset(base, task_count=30, courier_count=60, seed=201), seed=201, score_noise=0.01)
    if scenario == "medium_seed202":
        return perturb(filter_subset(base, task_count=30, courier_count=60, seed=202), seed=202, score_noise=0.02, score_bias=0.1)
    if scenario == "medium_seed203":
        return perturb(filter_subset(base, task_count=30, courier_count=60, seed=203), seed=203, score_noise=0.015, willingness_scale=0.95)
    if scenario == "small_seed100":
        return perturb(filter_subset(base, task_count=15, courier_count=32, seed=100), seed=100, score_noise=0.008)
    if scenario == "tiny_seed42":
        return perturb(filter_subset(base, task_count=6, courier_count=14, seed=42), seed=42, score_noise=0.005)
    return list(base)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=str(ROOT / "cases" / "large_seed301.txt"))
    ap.add_argument("--target", default="all", help="scenario name or all")
    ap.add_argument("--output", default=str(GENERATED))
    ap.add_argument("--seed", type=int, default=301)
    ap.add_argument("--config", default=str(CONFIG_PATH))
    args = ap.parse_args()

    base_path = Path(args.base)
    if not base_path.exists():
        print(json.dumps({"ok": False, "error": f"base case not found: {base_path}"}, ensure_ascii=False, indent=2))
        return 2
    cfg = read_json(Path(args.config), {})
    base = parse_rows(base_path)
    targets = SCENARIOS if args.target == "all" else [args.target]
    out_root = Path(args.output)
    generated = []
    for scenario in targets:
        rows = build_scenario(base, scenario, cfg, args.seed)
        out = out_root / scenario / f"{scenario}.txt"
        write_case(out, rows)
        generated.append({"scenario": scenario, "path": str(out.relative_to(ROOT)), "stats": stats(rows)})

    manifest = {
        "ok": True,
        "generated_at": iso_now(),
        "base": str(base_path.relative_to(ROOT) if base_path.is_relative_to(ROOT) else base_path),
        "target": args.target,
        "seed": args.seed,
        "items": generated,
        "agent_training_hint": "These generated TSV cases are synthetic scenario variants for local mid-training and no-regression exploration only; official online scores remain source of truth.",
    }
    write_json(MANIFEST_PATH, manifest)
    # Keep a seed config artifact that DataLab can display.
    write_json(CONFIG / "seed_config_large_seed301.json", manifest)
    st = read_json(STATE_PATH, {})
    st["generated_cases"] = manifest
    for ag in st.get("agents", []):
        if ag.get("id") == "data_seed":
            ag["status"] = "generated"
            ag["last_action"] = f"已基于 large_seed301 生成 {len(generated)} 组场景训练样本。"
            ag.setdefault("key_data", {})["generated_cases"] = len(generated)
            ag.setdefault("key_data", {})["target"] = args.target
        if ag.get("id") == "trainer":
            ag.setdefault("key_data", {})["generated_data_ready"] = True
    write_json(STATE_PATH, st)
    append_event("Data Seed Agent", "generate-cases", f"已基于 large_seed301 生成 {len(generated)} 组随机种子训练样本。", manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
