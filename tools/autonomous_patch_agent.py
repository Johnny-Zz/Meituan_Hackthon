#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Autonomous safe patch loop for MeituanRSD_autosolver.

Pipeline:
1. pre-backup current solver and logs;
2. benchmark current solver on all available cases;
3. ask DeepSeek for a structured patch plan when API key exists;
4. fall back to local conservative patch plans when offline;
5. apply only allow-listed CONFIG patches to submission/solver.py;
6. generate unified diff and static audit;
7. run no-regression gate against the pre-patch benchmark;
8. accept, reject, or rollback automatically;
9. record every step in JSONL + Notes.md + Handover.md.

This is intentionally not a blind "LLM writes arbitrary code" tool. The agent
can modify solver.py, but the patch surface is constrained, audited, diffed, and
gated before acceptance.
"""
from __future__ import annotations

import argparse
import ast
import copy
import datetime as dt
import difflib
import hashlib
import json
import os
import pprint
import py_compile
import re
import subprocess
import sys
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
from deepseek_client import chat_json, env_config, parse_json_object  # noqa: E402

MEMORY = ROOT / "memory" / "studio"
CONFIG_DIR = ROOT / "config"
DOCS = ROOT / "docs"
LOGS = ROOT / "logs" / "studio"
PATCH_DIR = ROOT / "patches"
DIFF_DIR = MEMORY / "patch_diffs"
STATE_PATH = MEMORY / "current_state.json"
CONFIG_PATH = CONFIG_DIR / "training_config.json"
PATCH_LOG = MEMORY / "patch_reports.jsonl"
CHAT_PATH = MEMORY / "chat.jsonl"
AGENT_LOG_PATH = MEMORY / "agent_logs.jsonl"
TRAINING_LOG_PATH = LOGS / "training_rounds.jsonl"
TRIALS_PATH = MEMORY / "trials.jsonl"
NOTES_PATH = DOCS / "Notes.md"
HANDOVER_PATH = DOCS / "Handover.md"
SOLVER_PATH = ROOT / "submission" / "solver.py"

BANNED_TOKENS = [
    "os.system", "eval(", "exec(", "__import__", "pickle", "shutil.rmtree",
    "pathlib.Path('/", "pathlib.Path(\"/",
]
BANNED_IMPORTS = ["subprocess", "socket", "urllib", "requests", "openai", "deepseek", "pickle", "shutil"]
SIZE_LIMIT_BYTES = int(os.getenv("RSD_SOLVER_SIZE_LIMIT_BYTES", "100000"))
NO_REGRESSION_EPS = float(os.getenv("RSD_NO_REGRESSION_EPS", "1e-6"))

# Keys that may be modified directly in the CONFIG literal. The patcher refuses
# arbitrary function bodies by default.
ALLOW_CONFIG_KEYS = {
    "time_budget_ms", "safety_margin_ms", "auto_strategy_budget_ms", "local_search_budget_ms",
    "race_topology_repair_budget_ms", "normal_preview_backup_cap", "normal_preview_scan_per_primary",
    "normal_topology_top_k", "normal_topology_generated_limit", "backup_time_budget_ms",
    "backup_reallocation_budget_ms", "multi_primary_time_budget_ms", "enable_multi_courier_output",
    "acceptance_penalty", "max_extra_couriers_per_bundle", "min_backup_utility", "min_remaining_ms",
    "max_exact_replace_tasks", "max_candidates_per_mask", "special_max_candidates_per_mask",
    "special_courier_ratio_threshold", "pair_top_k", "triple_top_k", "try_triples", "multi_cost_mode",
    "strategies",
}


def iso_now() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def stamp() -> str:
    return dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def clock() -> str:
    return dt.datetime.now().strftime("%H:%M:%S")


def read_json(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return copy.deepcopy(default)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def append_jsonl(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def sha_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def run(cmd: List[str], timeout: int = 180) -> Dict[str, Any]:
    try:
        p = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=timeout)
        return {"ok": p.returncode == 0, "code": p.returncode, "stdout": p.stdout[-12000:], "stderr": p.stderr[-12000:], "cmd": cmd}
    except Exception as e:
        return {"ok": False, "code": -1, "stdout": "", "stderr": f"{type(e).__name__}: {e}", "cmd": cmd}


def append_agent(agent: str, typ: str, message: str, extra: Optional[Dict[str, Any]] = None) -> None:
    ev: Dict[str, Any] = {"time": clock(), "iso": iso_now(), "agent": agent, "type": typ, "message": message}
    if extra is not None:
        ev["extra"] = extra
    append_jsonl(AGENT_LOG_PATH, ev)
    st = read_json(STATE_PATH, {})
    st.setdefault("events", []).append(ev)
    st["events"] = st["events"][-180:]
    write_json(STATE_PATH, st)


def ensure_docs() -> None:
    DOCS.mkdir(exist_ok=True)
    if not NOTES_PATH.exists():
        NOTES_PATH.write_text("# Notes.md\n", encoding="utf-8")
    if not HANDOVER_PATH.exists():
        HANDOVER_PATH.write_text("# Handover.md\n", encoding="utf-8")


def is_small_tiny_objective_text(value: str) -> bool:
    v = (value or "").lower()
    return any(k in v for k in ["small/tiny", "small_seed", "tiny_seed", "tiny_small", "tiny/small"])

def find_case_files(objective: str = "") -> List[Path]:
    found: List[Path] = []
    roots = [ROOT / "cases", ROOT / "data", ROOT / "samples", ROOT / "tests", ROOT / "generated_cases"]
    for base in roots:
        if not base.exists():
            continue
        pattern = "**/*.txt" if base.name == "generated_cases" else "*.txt"
        for p in base.glob(pattern):
            if p.name.lower().startswith(("readme", "notes", "handover")):
                continue
            try:
                head = p.read_text(encoding="utf-8", errors="ignore")[:200].lower()
            except Exception:
                continue
            if "task_id_list" in head and "courier_id" in head:
                found.append(p)
    unique = sorted(set(found), key=lambda p: str(p.relative_to(ROOT)))
    if is_small_tiny_objective_text(objective):
        wanted = []
        for token in ["tiny_seed42", "small_seed100", "large_seed301"]:
            for p in unique:
                if token in str(p).replace("\\", "/"):
                    wanted.append(p)
                    break
        if len(wanted) >= 3:
            return wanted
    order = ["tiny", "small", "medium", "large", "scarce", "low", "high_noise"]
    return sorted(unique, key=lambda p: (next((i for i, k in enumerate(order) if k in p.name), 99), p.name))[:24]


def parse_json_result(text: str) -> Optional[Dict[str, Any]]:
    text = (text or "").strip()
    if not text:
        return None
    for line in reversed(text.splitlines()):
        if line.startswith("JSON_RESULT="):
            try:
                return json.loads(line.split("=", 1)[1])
            except Exception:
                return None
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def benchmark_solver(solver: Path, cfg: Dict[str, Any], objective: str = "") -> Dict[str, Any]:
    local_test = ROOT / "local_test.py"
    if not local_test.exists():
        return {"ok": False, "mode": "missing_local_test", "reason": "local_test.py not found", "cases": []}
    cases = find_case_files(objective)
    if not cases:
        return {"ok": False, "mode": "missing_cases", "reason": "no case files found", "cases": []}
    timeout = int((cfg.get("one_click_training") or {}).get("max_seconds_per_round", 120))
    out_cases: List[Dict[str, Any]] = []
    for case in cases:
        res = run([sys.executable, "local_test.py", str(solver), str(case), "--json"], timeout=timeout)
        parsed = parse_json_result(res.get("stdout", "")) or {}
        out_cases.append({
            "case": case.name,
            "ok": bool(parsed.get("ok", res["ok"])),
            "valid": bool(parsed.get("valid", parsed.get("ok", res["ok"]))),
            "total_score": parsed.get("total_score"),
            "score": parsed.get("total_score"),
            "covered_tasks": parsed.get("covered_tasks"),
            "total_tasks": parsed.get("total_tasks"),
            "assignments": parsed.get("assignments"),
            "couriers_used": parsed.get("couriers_used"),
            "avg_backups_per_bundle": parsed.get("avg_backups_per_bundle"),
            "raw_score_sum": parsed.get("raw_score_sum"),
            "time_sec": parsed.get("time_sec"),
            "errors": parsed.get("errors", []),
            "warnings": parsed.get("warnings", []),
            "stdout_tail": res.get("stdout", "")[-1800:],
            "stderr_tail": res.get("stderr", "")[-1800:],
            "returncode": res.get("code"),
        })
    ok = all(c.get("ok") and c.get("valid") and c.get("total_score") is not None for c in out_cases)
    return {"ok": ok, "mode": "local_test", "cases": out_cases, "case_count": len(out_cases)}


def extract_config_assignment(text: str) -> Tuple[ast.Assign, Dict[str, Any]]:
    mod = ast.parse(text)
    for node in mod.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "CONFIG":
                    raw = ast.get_source_segment(text, node.value)
                    if raw is None:
                        raise RuntimeError("CONFIG source segment not available")
                    cfg = ast.literal_eval(raw)
                    if not isinstance(cfg, dict):
                        raise RuntimeError("CONFIG is not a dict literal")
                    return node, cfg
    raise RuntimeError("CONFIG assignment not found in solver.py")


def normalize_config_value(key: str, value: Any) -> Any:
    if key == "strategies":
        if not isinstance(value, list):
            raise ValueError("strategies must be a list")
        cleaned = []
        for row in value[:12]:
            if not isinstance(row, (list, tuple)) or len(row) < 6:
                raise ValueError("each strategy must be a tuple/list with at least 6 numeric values")
            cleaned.append(tuple(float(x) if i < len(row)-1 else int(x) for i, x in enumerate(row)))
        return cleaned
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return float(value)
    if isinstance(value, str):
        if len(value) > 80:
            raise ValueError(f"string value too long for {key}")
        return value
    raise ValueError(f"unsupported value type for {key}: {type(value).__name__}")


def sanitize_plan(plan: Dict[str, Any], round_no: int) -> Dict[str, Any]:
    updates = plan.get("config_updates") or {}
    if not isinstance(updates, dict):
        updates = {}
    clean: Dict[str, Any] = {}
    rejected: Dict[str, str] = {}
    for key, value in updates.items():
        if key not in ALLOW_CONFIG_KEYS and not str(key).startswith("_agent_"):
            rejected[str(key)] = "key not allow-listed"
            continue
        try:
            clean[str(key)] = normalize_config_value(str(key), value)
        except Exception as e:
            rejected[str(key)] = str(e)
    out = dict(plan)
    out["config_updates"] = clean
    out["rejected_updates"] = rejected
    return out


def replace_config_literal(text: str, plan: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    node, cfg = extract_config_assignment(text)
    new_cfg = copy.deepcopy(cfg)
    for key, value in (plan.get("config_updates") or {}).items():
        new_cfg[key] = value
    lines = text.splitlines()
    start = node.lineno - 1
    end = node.end_lineno
    pretty = pprint.pformat(new_cfg, width=120, sort_dicts=False)
    replacement = "CONFIG = " + pretty
    new_text = "\n".join(lines[:start] + replacement.splitlines() + lines[end:]) + "\n"
    return new_text, new_cfg


def unified_diff(old: str, new: str, fromfile: str = "submission/solver.py@before", tofile: str = "submission/solver.py@after") -> str:
    return "".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True), fromfile=fromfile, tofile=tofile))


def static_audit_text(text: str) -> Dict[str, Any]:
    danger = [tok for tok in BANNED_TOKENS if tok in text]
    import_danger = []
    for name in BANNED_IMPORTS:
        if re.search(rf"^\s*(import|from)\s+{re.escape(name)}(\b|\.)", text, flags=re.M):
            import_danger.append("import " + name)
    has_solve = bool(re.search(r"^def\s+solve\s*\(", text, flags=re.M))
    size = len(text.encode("utf-8"))
    all_danger = danger + import_danger
    return {
        "ok": not all_danger and has_solve and size <= SIZE_LIMIT_BYTES,
        "dangerous_tokens": all_danger,
        "has_solve": has_solve,
        "size_bytes": size,
        "size_limit_bytes": SIZE_LIMIT_BYTES,
    }


def patch_objective_audit(original: str, patched: str, diff: str, objective: str) -> Dict[str, Any]:
    bad = []
    if any(line.lstrip().startswith("#") for line in patched.splitlines()):
        bad.append("solver comments are forbidden")
    if is_small_tiny_objective_text(objective):
        has_func = "def tiny_small_backup_polish" in patched
        has_call = "tiny_small_backup_polish(candidates" in patched
        changed_func = "tiny_small_backup_polish" in diff
        if not has_func or not has_call:
            bad.append("small/tiny patch lacks tiny_small_backup_polish function or call")
        if not changed_func:
            bad.append("small/tiny patch is metadata-only or does not touch tiny_small_backup_polish")
    return {"ok": not bad, "errors": bad}

def compile_audit(path: Path) -> Dict[str, Any]:
    try:
        py_compile.compile(str(path), doraise=True)
        return {"ok": True}
    except Exception:
        return {"ok": False, "error": traceback.format_exc(limit=3)}


def no_regression_gate(before: Dict[str, Any], after: Dict[str, Any]) -> Dict[str, Any]:
    checks: List[Dict[str, Any]] = []
    before_map = {c.get("case"): c for c in before.get("cases", [])}
    for c in after.get("cases", []):
        name = c.get("case")
        b = before_map.get(name)
        if not b:
            checks.append({"case": name, "ok": False, "reason": "missing baseline case"})
            continue
        b_score = b.get("total_score")
        a_score = c.get("total_score")
        valid = bool(c.get("ok")) and bool(c.get("valid")) and a_score is not None
        if not valid:
            checks.append({"case": name, "ok": False, "reason": "candidate invalid", "after": c})
            continue
        improved_or_equal = float(a_score) <= float(b_score) + NO_REGRESSION_EPS
        checks.append({
            "case": name,
            "ok": improved_or_equal,
            "before": b_score,
            "after": a_score,
            "delta": round(float(a_score) - float(b_score), 9),
            "reason": "pass" if improved_or_equal else "regression: lower-is-better score increased",
        })
    return {"ok": bool(checks) and all(x.get("ok") for x in checks), "checks": checks, "eps": NO_REGRESSION_EPS}


def build_deepseek_prompt(objective: str, cfg: Dict[str, Any], baseline: Dict[str, Any], solver_text: str) -> List[Dict[str, str]]:
    # Keep the prompt compact. We do not send the whole 70KB solver unless necessary;
    # the CONFIG and metrics are enough for the constrained patch surface.
    _, solver_cfg = extract_config_assignment(solver_text)
    summary = {
        "objective": objective,
        "available_cases": baseline.get("cases", []),
        "current_config": solver_cfg,
        "data_lab_scenes": cfg.get("scenes", {}),
        "constraints": {
            "lower_is_better": True,
            "only_config_updates_allowed": True,
            "must_preserve_solve_signature": "solve(input_text: str) -> list",
            "protected_cases": ["tiny_seed42", "small_seed100", "scarce_couriers_seed401"],
            "output_json_schema": {
                "title": "string",
                "source": "deepseek",
                "risk_level": "low|medium|high",
                "rationale": "string",
                "config_updates": {"CONFIG_KEY": "new_literal_value"},
                "expected_effect": "string",
                "rollback_plan": "string",
            },
        },
    }
    system = (
        "你是 MeituanRSD_autosolver 的 Patch Generator Agent。"
        "你只能输出一个 JSON object，不要 Markdown。"
        "你不能要求任意重写函数，只能给 solver.py 顶层 CONFIG 字典的键值更新。"
        "优化目标是本地 no-regression：所有 case valid，lower-is-better 分数不能变差。"
        "偏好低风险、可回滚、anchor-preserving 的小 patch。"
    )
    user = "请基于以下基线评测和 CONFIG 生成一个低风险结构化 patch plan：\n" + json.dumps(summary, ensure_ascii=False)[:45000]
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def local_fallback_plans(round_no: int, objective: str, baseline: Dict[str, Any]) -> List[Dict[str, Any]]:
    # First candidate is metadata-only: always safe and proves the modify/review/gate loop.
    # Later candidates are mild CONFIG adjustments. The gate decides whether to keep them.
    base_title = objective[:80] or "autonomous local fallback patch"
    return [
        {
            "title": "safe_metadata_trace_patch",
            "source": "local_fallback",
            "risk_level": "low",
            "rationale": "无 DeepSeek API Key 时，先写入可审查的 patch 元数据，验证自主改写、diff、审查、no-regression 与日志闭环，不改变求解行为。",
            "config_updates": {},
            "expected_effect": "求解结果应与 patch 前完全一致；用于确认自动改写链路可跑通。",
            "rollback_plan": "若静态审查或本地评测失败，立即恢复 patch 前 solver.py。",
        },
        {
            "title": base_title + " · backup scan small increase",
            "source": "local_fallback",
            "risk_level": "medium",
            "rationale": "小幅增加 normal 场景 backup scan，用 no-regression gate 验证是否改善 large/normal 样本。",
            "config_updates": {"normal_preview_scan_per_primary": 24, "backup_time_budget_ms": 900.0},
            "expected_effect": "可能为 large/normal 样本找到更优备份顺序；若耗时或分数变差会被 gate 拒绝。",
            "rollback_plan": "score increase 或 invalid 则自动恢复。",
        },
    ]


def propose_plans(objective: str, cfg: Dict[str, Any], baseline: Dict[str, Any], solver_text: str, round_no: int) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    env = env_config()
    messages = build_deepseek_prompt(objective, cfg, baseline, solver_text)
    ds = chat_json(messages)
    metadata = {"deepseek_ok": ds.ok, "model": ds.model, "base_url": ds.base_url, "error": ds.error}
    append_jsonl(CHAT_PATH, {"time": iso_now(), "role": "user", "message": messages[-1]["content"][:2000], "channel": "patch_generator"})
    if ds.ok:
        append_jsonl(CHAT_PATH, {"time": iso_now(), "role": "assistant", "message": ds.content[:5000], "channel": "patch_generator", "model": ds.model})
        try:
            plan = parse_json_object(ds.content)
            plan["source"] = plan.get("source") or "deepseek"
            return [plan] + local_fallback_plans(round_no, objective, baseline), metadata
        except Exception as e:
            metadata["parse_error"] = str(e)
            append_jsonl(CHAT_PATH, {"time": iso_now(), "role": "system", "message": f"DeepSeek patch JSON parse failed: {e}", "channel": "patch_generator"})
    else:
        append_jsonl(CHAT_PATH, {"time": iso_now(), "role": "assistant", "message": ds.error, "channel": "patch_generator", "model": ds.model, "ok": False})
    return local_fallback_plans(round_no, objective, baseline), metadata


def write_report(report: Dict[str, Any]) -> None:
    append_jsonl(PATCH_LOG, report)
    append_jsonl(TRIALS_PATH, {"round": report.get("round"), "time": iso_now(), "source": "autonomous_patch_agent", "change": report.get("objective"), "patch": report, "ok": report.get("accepted")})
    append_jsonl(TRAINING_LOG_PATH, {"round": report.get("round"), "time": iso_now(), "source": "autonomous_patch_agent", "change": report.get("objective"), "patch": report, "ok": report.get("accepted")})
    ensure_docs()
    diff_tail = report.get("diff", "")[:5000]
    NOTES_PATH.open("a", encoding="utf-8").write(
        f"\n## Autonomous Patch Round {report.get('round')} · {iso_now()}\n"
        f"- Objective: {report.get('objective')}\n"
        f"- Accepted: {report.get('accepted')}\n"
        f"- Plan: {report.get('plan', {}).get('title')} · risk={report.get('plan', {}).get('risk_level')}\n"
        f"- DeepSeek: {json.dumps(report.get('deepseek', {}), ensure_ascii=False)}\n"
        f"- Gate: `{json.dumps(report.get('gate', {}), ensure_ascii=False)[:2000]}`\n"
        f"- Reason: {report.get('decision_reason')}\n"
        f"\n### Solver diff\n```diff\n{diff_tail}\n```\n"
    )
    HANDOVER_PATH.open("a", encoding="utf-8").write(
        f"\n## Handover · Autonomous Patch Round {report.get('round')} · {iso_now()}\n"
        f"- 本轮目标：{report.get('objective')}\n"
        f"- 结论：{'已接受 patch' if report.get('accepted') else '已拒绝/回滚 patch'}\n"
        f"- 决策原因：{report.get('decision_reason')}\n"
        f"- Solver hash：before={report.get('before_hash', '')[:16]} after={report.get('after_hash', '')[:16]}\n"
        f"- 下一步：如需接入线上 judge，先在官方平台手动提交当前 `submission/solver.py`，把反馈截图或分数填回 DataLab。\n"
    )


def update_state(report: Dict[str, Any]) -> None:
    st = read_json(STATE_PATH, {})
    st["project"] = "MeituanRSD_autosolver"
    st["version"] = "3.2-autonomous-patch-gate"
    st.setdefault("candidate", {})
    st["candidate"].update({
        "round": report.get("round"),
        "status": "patch-accepted" if report.get("accepted") else "patch-rejected",
        "last_change": report.get("objective"),
        "last_patch_title": (report.get("plan") or {}).get("title"),
        "last_patch_gate": report.get("gate", {}).get("ok"),
        "updated_at": iso_now(),
    })
    after_cases = ((report.get("after_benchmark") or {}).get("cases") or [])
    for row in st.get("case_results", []):
        for c in after_cases:
            if Path(c.get("case", "")).stem == row.get("case"):
                row["candidate"] = c.get("total_score")
                if c.get("covered_tasks") is not None and c.get("total_tasks") is not None:
                    row["assigned"] = f"{c.get('covered_tasks')}/{c.get('total_tasks')}"
                if c.get("total_score") is not None and row.get("champion") is not None:
                    delta = round(float(c.get("total_score")) - float(row.get("champion")), 6)
                    hist = list(row.get("delta_history", [])); hist.append(delta); row["delta_history"] = hist[-12:]
                    row["last_delta"] = delta
                    row["status"] = "improved" if delta < 0 else "equal" if delta == 0 else row.get("status", "watch")
    for a in st.get("agents", []):
        if a.get("id") in {"strategy", "trainer", "evaluator", "auditor", "reflector"}:
            a["status"] = "patch-accepted" if report.get("accepted") else "guarded"
            a["last_action"] = f"Patch round {report.get('round')}: {report.get('decision_reason')}"
        if a.get("id") == "leader":
            a["last_action"] = f"Autonomous patch round {report.get('round')} completed"
    st.setdefault("patch_reports", [])
    st["patch_reports"].append({k: v for k, v in report.items() if k not in {"diff"}})
    st["patch_reports"] = st["patch_reports"][-12:]
    write_json(STATE_PATH, st)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--objective", default="Autonomously improve solver.py with safe CONFIG patch and no-regression gate")
    ap.add_argument("--source", default="cli")
    ap.add_argument("--no-pre-backup", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="Generate and audit patch but restore solver at end.")
    args = ap.parse_args()

    MEMORY.mkdir(exist_ok=True); LOGS.mkdir(exist_ok=True); PATCH_DIR.mkdir(exist_ok=True); DIFF_DIR.mkdir(exist_ok=True)
    cfg = read_json(CONFIG_PATH, {"one_click_training": {"max_seconds_per_round": 120}, "scenes": {}})
    state = read_json(STATE_PATH, {})
    round_no = int((state.get("candidate") or {}).get("round") or 0) + 1

    if not SOLVER_PATH.exists():
        raise FileNotFoundError("submission/solver.py not found")

    original = SOLVER_PATH.read_text(encoding="utf-8")
    before_hash = sha_text(original)
    append_agent("Patch Generator Agent", "start", f"Round {round_no}: autonomous patch requested", {"objective": args.objective, "source": args.source})

    backup_res = {"ok": True, "stdout": "pre-backup skipped"}
    if not args.no_pre_backup:
        backup_res = run([sys.executable, "tools/champion_guard.py", "backup", "--tag", "pre_autopatch", "--round", str(round_no), "--note", args.objective], timeout=180)
        append_agent("Auditor", "pre-autopatch-backup", "自主改写前备份完成" if backup_res["ok"] else "自主改写前备份失败", backup_res)
        if not backup_res["ok"]:
            print(json.dumps({"ok": False, "stage": "backup", "result": backup_res}, ensure_ascii=False, indent=2))
            sys.exit(1)

    baseline = benchmark_solver(SOLVER_PATH, cfg, args.objective)
    append_agent("Evaluator", "baseline", "Patch 前基线评测完成", baseline)
    if not baseline.get("ok"):
        report = {"round": round_no, "objective": args.objective, "accepted": False, "decision_reason": "baseline benchmark failed", "baseline": baseline, "before_hash": before_hash, "backup": backup_res}
        write_report(report); update_state(report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        sys.exit(1)

    plans, ds_meta = propose_plans(args.objective, cfg, baseline, original, round_no)
    append_agent("LLM Reflector", "patch-plan", "DeepSeek/本地规则已生成 patch plan 候选", {"deepseek": ds_meta, "plan_count": len(plans)})

    attempts: List[Dict[str, Any]] = []
    accepted_report: Optional[Dict[str, Any]] = None
    for i, raw_plan in enumerate(plans[:4], start=1):
        SOLVER_PATH.write_text(original, encoding="utf-8")
        plan = sanitize_plan(raw_plan, round_no)
        try:
            patched, new_cfg = replace_config_literal(original, plan)
            diff = unified_diff(original, patched)
            diff_path = DIFF_DIR / f"round_{round_no}_attempt_{i}.diff"
            diff_path.write_text(diff, encoding="utf-8")
            text_audit = static_audit_text(patched)
            SOLVER_PATH.write_text(patched, encoding="utf-8")
            comp = compile_audit(SOLVER_PATH)
            extra_audit = patch_objective_audit(original, patched, diff, args.objective)
            audit_ok = text_audit.get("ok") and extra_audit.get("ok") and comp.get("ok")
            after = benchmark_solver(SOLVER_PATH, cfg, args.objective) if audit_ok else {"ok": False, "cases": [], "reason": "static/compile/objective audit failed", "objective_audit": extra_audit}
            gate = no_regression_gate(baseline, after) if after.get("ok") else {"ok": False, "checks": [], "reason": after.get("reason", "after benchmark failed")}
            attempt = {"attempt": i, "plan": plan, "text_audit": text_audit, "objective_audit": extra_audit, "compile_audit": comp, "after_benchmark": after, "gate": gate, "diff_path": str(diff_path.relative_to(ROOT)), "diff": diff[:12000]}
            attempts.append(attempt)
            append_agent("Gate Keeper", "gate", f"Patch attempt {i}: {'PASS' if gate.get('ok') else 'FAIL'}", {"plan": plan.get("title"), "gate": gate})
            if text_audit.get("ok") and extra_audit.get("ok") and comp.get("ok") and gate.get("ok"):
                accepted_report = {
                    "ok": True,
                    "round": round_no,
                    "time": iso_now(),
                    "source": args.source,
                    "objective": args.objective,
                    "accepted": not args.dry_run,
                    "dry_run": bool(args.dry_run),
                    "decision_reason": "patch passed static audit, compile audit, local benchmark, and no-regression gate" if not args.dry_run else "dry-run accepted then restored",
                    "plan": plan,
                    "deepseek": ds_meta,
                    "backup": backup_res,
                    "baseline": baseline,
                    "after_benchmark": after,
                    "gate": gate,
                    "text_audit": text_audit,
                    "compile_audit": comp,
                    "objective_audit": extra_audit,
                    "before_hash": before_hash,
                    "after_hash": sha_text(patched),
                    "diff_path": str(diff_path.relative_to(ROOT)),
                    "diff": diff,
                    "attempts": [{k: v for k, v in a.items() if k != "diff"} for a in attempts],
                }
                if args.dry_run:
                    SOLVER_PATH.write_text(original, encoding="utf-8")
                break
        except Exception as e:
            SOLVER_PATH.write_text(original, encoding="utf-8")
            attempt = {"attempt": i, "plan": raw_plan, "error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc(limit=5)}
            attempts.append(attempt)
            append_agent("Auditor", "patch-error", f"Patch attempt {i} failed before gate", attempt)

    if accepted_report is None:
        SOLVER_PATH.write_text(original, encoding="utf-8")
        accepted_report = {
            "ok": False,
            "round": round_no,
            "time": iso_now(),
            "source": args.source,
            "objective": args.objective,
            "accepted": False,
            "decision_reason": "all patch attempts failed or regressed; solver.py restored to pre-patch content",
            "plan": attempts[-1].get("plan") if attempts else {},
            "deepseek": ds_meta,
            "backup": backup_res,
            "baseline": baseline,
            "after_benchmark": None,
            "gate": attempts[-1].get("gate") if attempts else {"ok": False, "reason": "no attempts"},
            "before_hash": before_hash,
            "after_hash": sha_text(original),
            "diff": attempts[-1].get("diff", "") if attempts else "",
            "attempts": [{k: v for k, v in a.items() if k != "diff"} for a in attempts],
        }

    write_report(accepted_report)
    update_state(accepted_report)
    append_agent("Leader", "patch-finished", accepted_report.get("decision_reason", "patch finished"), {"accepted": accepted_report.get("accepted")})
    print(json.dumps({k: v for k, v in accepted_report.items() if k != "diff"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
