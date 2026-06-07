#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""One-click training orchestrator for MeituanRSD_autosolver.

This script is intentionally conservative:
1) backup previous round before any training attempt;
2) read config/training_config.json;
3) run local tests only when local_test.py and case files exist;
4) record every round into Notes.md, Handover.md, JSONL logs and agent logs;
5) never submit to official URL automatically.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MEMORY = ROOT / "memory" / "studio"
CONFIG = ROOT / "config"
DOCS = ROOT / "docs"
LOGS = ROOT / "logs" / "studio"
STATE_PATH = MEMORY / "current_state.json"
CONFIG_PATH = CONFIG / "training_config.json"
TRIALS_PATH = MEMORY / "trials.jsonl"
AGENT_LOG_PATH = MEMORY / "agent_logs.jsonl"
TRAINING_LOG_PATH = LOGS / "training_rounds.jsonl"
NOTES_PATH = DOCS / "Notes.md"
HANDOVER_PATH = DOCS / "Handover.md"


def iso_now():
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def clock():
    return dt.datetime.now().strftime("%H:%M:%S")


def read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def append_jsonl(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def default_config():
    return {
        "project_name": "MeituanRSD_autosolver",
        "one_click_training": {"enabled": True, "pre_backup": True, "mode": "local_test_with_autonomous_patch", "autonomous_patch_enabled": True},
        "scenes": {}
    }


def append_agent(agent, typ, message, extra=None):
    ev = {"time": clock(), "iso": iso_now(), "agent": agent, "type": typ, "message": message}
    if extra is not None:
        ev["extra"] = extra
    append_jsonl(AGENT_LOG_PATH, ev)
    st = read_json(STATE_PATH, {})
    st.setdefault("events", []).append(ev)
    st["events"] = st["events"][-160:]
    write_json(STATE_PATH, st)


def run(cmd, timeout=180):
    try:
        p = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=timeout)
        return {"ok": p.returncode == 0, "code": p.returncode, "stdout": p.stdout[-8000:], "stderr": p.stderr[-8000:], "cmd": cmd}
    except Exception as e:
        return {"ok": False, "code": -1, "stdout": "", "stderr": f"{type(e).__name__}: {e}", "cmd": cmd}


def find_case_files():
    candidates = []
    for folder in ["cases", "data", "samples", "tests", "."]:
        base = ROOT / folder
        if base.exists():
            for p in base.glob("*.txt"):
                if p.name.lower().startswith(("readme", "notes", "handover")):
                    continue
                candidates.append(p)
    # Keep canonical named cases first.
    order = ["tiny", "small", "medium", "large", "scarce", "low", "high_noise"]
    candidates = sorted(set(candidates), key=lambda p: (next((i for i, key in enumerate(order) if key in p.name), 99), p.name))
    return candidates[:20]


def parse_json_result(text: str):
    """Parse local_test.py JSON output. Supports pure JSON and JSON_RESULT=... lines."""
    text = text.strip()
    if not text:
        return None
    # Prefer the machine-readable marker emitted by local_test.py.
    for line in reversed(text.splitlines()):
        if line.startswith("JSON_RESULT="):
            try:
                return json.loads(line.split("=", 1)[1])
            except Exception:
                return None
    try:
        return json.loads(text)
    except Exception:
        return None


def parse_score(text: str):
    parsed = parse_json_result(text)
    if isinstance(parsed, dict):
        for key in ("total_score", "score", "cost"):
            if parsed.get(key) is not None:
                try:
                    return float(parsed[key])
                except Exception:
                    pass
    # Supports outputs like "total_score: 392.818", "score=...", or "cost ...".
    patterns = [r"total_score\s*[:=]\s*([0-9.]+)", r"score\s*[:=]\s*([0-9.]+)", r"cost\s*[:=]\s*([0-9.]+)"]
    for pat in patterns:
        m = re.search(pat, text, flags=re.I)
        if m:
            try:
                return float(m.group(1))
            except Exception:
                pass
    return None


def run_local_tests(cfg):
    local_test = ROOT / "local_test.py"
    if not local_test.exists():
        return {"mode": "dry_run", "reason": "未发现 local_test.py，本轮只执行备份、配置读取、日志与归因记录。", "cases": []}
    case_files = find_case_files()
    if not case_files:
        return {"mode": "dry_run", "reason": "未发现 case .txt 文件，本轮只执行备份、配置读取、日志与归因记录。", "cases": []}
    results = []
    for case in case_files:
        cmd = [sys.executable, "local_test.py", "submission/solver.py", str(case), "--json"]
        res = run(cmd, timeout=int(cfg.get("one_click_training", {}).get("max_seconds_per_round", 120)))
        merged = (res.get("stdout", "") or "") + "\n" + (res.get("stderr", "") or "")
        parsed = parse_json_result(res.get("stdout", "") or "") or {}
        case_result = {
            "case": case.name,
            "ok": bool(parsed.get("ok", res["ok"])),
            "valid": bool(parsed.get("valid", parsed.get("ok", res["ok"]))),
            "score": parse_score(merged),
            "covered_tasks": parsed.get("covered_tasks"),
            "total_tasks": parsed.get("total_tasks"),
            "assignments": parsed.get("assignments"),
            "couriers_used": parsed.get("couriers_used"),
            "avg_backups_per_bundle": parsed.get("avg_backups_per_bundle"),
            "time_sec": parsed.get("time_sec"),
            "lower_is_better": parsed.get("lower_is_better", True),
            "raw_score_sum": parsed.get("raw_score_sum"),
            "errors": parsed.get("errors", []),
            "warnings": parsed.get("warnings", []),
            "stdout_tail": res.get("stdout", "")[-1200:],
            "stderr_tail": res.get("stderr", "")[-1200:]
        }
        results.append(case_result)
    ok_count = sum(1 for r in results if r.get("ok") and r.get("valid"))
    return {"mode": "local_test", "reason": f"local_test.py detected; {ok_count}/{len(results)} cases valid", "cases": results}


def write_markdown(round_no, backup_stdout, eval_result, change, cfg, state):
    DOCS.mkdir(exist_ok=True)
    for p, title in [(NOTES_PATH, "# Notes.md\n"), (HANDOVER_PATH, "# Handover.md\n")]:
        if not p.exists():
            p.write_text(title, encoding="utf-8")

    forensics = state.get("forensics", [])[:3]
    forensic_md = []
    for f in forensics:
        forensic_md.append(
            f"### {f.get('scene','unknown')}\n"
            f"- 原因：{f.get('reason', f.get('finding',''))}\n"
            f"- 错误代码：\n```python\n{f.get('bad_code','')}\n```\n"
            f"- 修正方向：\n```python\n{f.get('patch','')}\n```\n"
        )

    eval_summary = json.dumps(eval_result, ensure_ascii=False, indent=2)
    cfg_focus = json.dumps(cfg.get("scenes", {}), ensure_ascii=False, indent=2)

    note = f"""
\n## Round {round_no} · {iso_now()}\n
**变更来源**：{change}\n
**训练前备份**：\n```text\n{backup_stdout.strip() or 'backup stdout empty'}\n```\n
**评测/训练结果**：\n```json\n{eval_summary}\n```\n
**本轮错误归因与错误代码记录**：\n{''.join(forensic_md)}\n
**DataLab 场景参数快照**：\n```json\n{cfg_focus}\n```\n"""
    with NOTES_PATH.open("a", encoding="utf-8") as f:
        f.write(note)

    handover = f"""
\n## Handover · Round {round_no} · {iso_now()}\n
- 项目：MeituanRSD_autosolver\n- 本轮改动：{change}\n- 一键训练模式：{eval_result.get('mode')}\n- 备份：{backup_stdout.strip() or '见 backups/'}\n- 当前建议：继续保持 V6 anchor-preserving；high_noise 只改 backup_order，medium202 只做 output-level swap，large302 暂缓。\n- 风险提醒：如果 protected case 触发退化，直接在“回滚”页选择上一轮 pre_train 备份并输入 ROLLBACK。\n"""
    with HANDOVER_PATH.open("a", encoding="utf-8") as f:
        f.write(handover)


def update_state_round(round_no, eval_result, change):
    st = read_json(STATE_PATH, {})
    st["project"] = "MeituanRSD_autosolver"
    st.setdefault("candidate", {})

    first_score = None
    case_results_by_stem = {}
    for r in eval_result.get("cases", []):
        stem = Path(r.get("case", "")).stem
        if not stem:
            continue
        case_results_by_stem[stem] = r
        if first_score is None and r.get("score") is not None:
            first_score = r.get("score")

    # Push local_test metrics back into Cockpit visual cards.
    for row in st.get("case_results", []):
        name = row.get("case")
        r = case_results_by_stem.get(name)
        if not r:
            continue
        score = r.get("score")
        row["candidate"] = score
        if r.get("covered_tasks") is not None and r.get("total_tasks") is not None:
            row["assigned"] = f"{r.get('covered_tasks')}/{r.get('total_tasks')}"
        row["last_local_time_sec"] = r.get("time_sec")
        row["avg_backups"] = r.get("avg_backups_per_bundle")
        row["valid"] = r.get("valid")
        if score is not None and row.get("champion") is not None:
            delta = round(float(score) - float(row.get("champion")), 6)
            row["last_delta"] = delta
            hist = list(row.get("delta_history", []))
            hist.append(delta)
            row["delta_history"] = hist[-12:]
            if delta <= 0:
                row["status"] = "improved" if delta < 0 else "equal"
            elif row.get("status") == "protected":
                row["status"] = "regressed-protected"
            else:
                row["status"] = "watch"

    st["candidate"].update({
        "round": round_no,
        "status": "evaluated" if eval_result.get("mode") == "local_test" else "dry-run-logged",
        "score": first_score,
        "lower_is_better": True,
        "last_change": change,
        "last_eval_mode": eval_result.get("mode"),
        "last_eval_reason": eval_result.get("reason"),
        "updated_at": iso_now()
    })
    # Update per-agent visible status.
    for a in st.get("agents", []):
        if a.get("id") == "trainer":
            a["status"] = "logged" if eval_result.get("mode") == "dry_run" else "evaluated"
            a["last_action"] = f"Round {round_no}: {eval_result.get('reason')}"
        if a.get("id") == "evaluator":
            a["last_action"] = f"Round {round_no}: local metrics synced to Cockpit"
        if a.get("id") == "auditor":
            a["last_action"] = f"Round {round_no}: pre-train backup manifest created"
        if a.get("id") == "leader":
            a["last_action"] = f"Round {round_no}: collected train result and wrote Handover"
    write_json(STATE_PATH, st)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="cli")
    parser.add_argument("--change", default="one-click training")
    args = parser.parse_args()

    MEMORY.mkdir(exist_ok=True)
    LOGS.mkdir(exist_ok=True)
    CONFIG.mkdir(exist_ok=True)
    DOCS.mkdir(exist_ok=True)
    cfg = read_json(CONFIG_PATH, default_config())
    state = read_json(STATE_PATH, {})
    last_round = int((state.get("candidate") or {}).get("round") or 0)
    round_no = last_round + 1

    append_agent("Leader", "plan", f"Round {round_no} plan: anchor-preserving + configured DataLab parameters", {"source": args.source})

    backup_res = {"ok": True, "stdout": "pre_backup disabled"}
    if cfg.get("one_click_training", {}).get("pre_backup", True):
        backup_res = run([sys.executable, "tools/champion_guard.py", "backup", "--tag", "pre_train", "--round", str(round_no), "--note", args.change], timeout=180)
        append_agent("Auditor", "pre-backup", "训练前备份已执行" if backup_res["ok"] else "训练前备份失败", backup_res)
        if not backup_res["ok"]:
            append_jsonl(TRAINING_LOG_PATH, {"round": round_no, "time": iso_now(), "ok": False, "stage": "backup", "result": backup_res})
            print(json.dumps({"ok": False, "stage": "backup", "result": backup_res}, ensure_ascii=False, indent=2))
            sys.exit(1)

    append_agent("Data Seed Agent", "seed", "读取 high_noise/large/low/medium/scarce 参数，准备种子策略", cfg.get("scenes", {}))

    patch_result = {"ok": None, "skipped": True, "reason": "autonomous_patch_enabled is false"}
    if cfg.get("one_click_training", {}).get("autonomous_patch_enabled", True):
        append_agent("Patch Generator Agent", "autopatch", "一键训练进入自主改写 solver.py 阶段：DeepSeek/本地规则 -> patch -> diff -> gate")
        patch_run = run([sys.executable, "tools/autonomous_patch_agent.py", "--source", "one_key_train", "--objective", args.change, "--no-pre-backup"], timeout=int(cfg.get("one_click_training", {}).get("max_seconds_per_round", 120)) + 220)
        parsed_patch = parse_json_result(patch_run.get("stdout", "") or "") or {}
        patch_result = {"ok": patch_run.get("ok"), "runner": patch_run, "report": parsed_patch}
        append_agent("Gate Keeper", "autopatch-result", "自主 patch 通过并保留" if parsed_patch.get("accepted") else "自主 patch 未通过或仅保留安全可审查变更", patch_result)

    append_agent("Trainer", "train", "执行本地训练/评测入口；若自主 patch 已完成 after_benchmark，则复用其评测结果以避免重复耗时")
    patch_report = (patch_result.get("report") or {}) if isinstance(patch_result, dict) else {}
    patch_after = patch_report.get("after_benchmark") or {}
    if patch_after.get("ok") and patch_after.get("cases"):
        eval_cases = []
        for c in patch_after.get("cases", []):
            eval_cases.append({
                "case": c.get("case"),
                "ok": c.get("ok"),
                "valid": c.get("valid"),
                "score": c.get("total_score", c.get("score")),
                "covered_tasks": c.get("covered_tasks"),
                "total_tasks": c.get("total_tasks"),
                "assignments": c.get("assignments"),
                "couriers_used": c.get("couriers_used"),
                "avg_backups_per_bundle": c.get("avg_backups_per_bundle"),
                "time_sec": c.get("time_sec"),
                "lower_is_better": True,
                "raw_score_sum": c.get("raw_score_sum"),
                "errors": c.get("errors", []),
                "warnings": c.get("warnings", []),
                "stdout_tail": c.get("stdout_tail", ""),
                "stderr_tail": c.get("stderr_tail", ""),
            })
        eval_result = {"mode": "local_test", "reason": "autonomous_patch after_benchmark reused; no-regression gate already passed", "cases": eval_cases}
    else:
        eval_result = run_local_tests(cfg)
    append_agent("Evaluator", "evaluate", eval_result.get("reason", "evaluation finished"), eval_result)
    append_agent("LLM Reflector", "forensics", "将错误原因、patch diff 与错误代码片段计入 Notes.md / Handover.md")

    trial_ok = True
    if eval_result.get("mode") == "local_test":
        trial_ok = all(bool(c.get("ok")) and bool(c.get("valid")) for c in eval_result.get("cases", []))
    trial = {
        "round": round_no,
        "time": iso_now(),
        "source": args.source,
        "change": args.change,
        "backup_stdout": backup_res.get("stdout", "").strip(),
        "eval": eval_result,
        "autonomous_patch": patch_result,
        "ok": trial_ok
    }
    append_jsonl(TRIALS_PATH, trial)
    append_jsonl(TRAINING_LOG_PATH, trial)
    write_markdown(round_no, backup_res.get("stdout", ""), eval_result, args.change, cfg, state)
    update_state_round(round_no, eval_result, args.change)
    append_agent("Leader", "handover", f"Round {round_no} finished; Notes.md and Handover.md updated")

    print(json.dumps(trial, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
