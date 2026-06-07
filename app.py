#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SwarmCell / OmniCell local studio server.

零额外依赖。本地提供：
- 可视化 Cockpit / Agent Office / Flow / DataLab / Training Logs / Rollback
- 一键训练入口：训练前自动备份，训练后记录 Notes.md、Handover.md、JSONL 日志
- DeepSeek-V4-pro 对话日志：仅做归因、DSL 建议和安全审计；Qwen 仅用于 OCR
- champion_guard：备份、列出、恢复、哈希校验
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import base64
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

try:
    sys.path.insert(0, str(Path(__file__).resolve().parent / "tools"))
    from qwen_client import config_status as qwen_config_status
except Exception:
    def qwen_config_status():
        return {"configured": False, "error": "qwen_client unavailable", "role": "ocr_only"}
try:
    from deepseek_client import env_config as deepseek_env_config
except Exception:
    def deepseek_env_config():
        return {"api_key": "", "base_url": "https://api.deepseek.com", "model": "DeepSeek-V4-pro"}

def deepseek_config_status():
    cfg = deepseek_env_config()
    return {
        "configured": bool(cfg.get("api_key")),
        "base_url": cfg.get("base_url", "https://api.deepseek.com"),
        "model": cfg.get("model", "DeepSeek-V4-pro"),
        "api_key_source": "env_or_config_env_local" if cfg.get("api_key") else "missing",
        "role": "llm_reflector_and_patch_planner"
    }

ROOT = Path(__file__).resolve().parent
MEMORY = ROOT / "memory" / "studio"
DASHBOARD = ROOT / "dashboard"
DOCS = ROOT / "docs"
CONFIG = ROOT / "config"
BACKUPS = ROOT / "backups" / "legacy"
LOGS = ROOT / "logs" / "studio"
STATE_PATH = MEMORY / "current_state.json"
CHAT_PATH = MEMORY / "chat.jsonl"
TRIALS_PATH = MEMORY / "trials.jsonl"
AGENT_LOG_PATH = MEMORY / "agent_logs.jsonl"
TRAINING_LOG_PATH = LOGS / "training_rounds.jsonl"
CONFIG_PATH = CONFIG / "training_config.json"
NOTES_PATH = DOCS / "Notes.md"
HANDOVER_PATH = DOCS / "Handover.md"
PATCH_LOG_PATH = MEMORY / "patch_reports.jsonl"
PATCH_DIFF_DIR = MEMORY / "patch_diffs"
SCORE_FEEDBACK_DIR = MEMORY / "score_feedback"
SCORE_FEEDBACK_HISTORY = MEMORY / "score_feedback_history.jsonl"
SCORE_FEEDBACK_LATEST = MEMORY / "score_feedback_latest.json"
NEXT_ROUND_PLAN_PATH = CONFIG / "next_round_plan.json"

DANGEROUS_KEYWORDS = [
    "import requests", "import openai", "import deepseek", "import sqlite", "import pandas",
    "import scipy", "os.system", "import socket", "import urllib", "eval(", "exec("
]


def iso_now() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def clock() -> str:
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


def read_jsonl(path: Path, limit=200):
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()[-limit:]
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except Exception:
            out.append({"raw": line})
    return out


def file_sha(path: Path, short=True) -> str:
    if not path.exists():
        return "missing"
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    digest = h.hexdigest()
    return digest[:16] if short else digest


def append_event(agent: str, typ: str, message: str, extra=None):
    st = read_json(STATE_PATH, default_state())
    ev = {"time": clock(), "iso": iso_now(), "agent": agent, "type": typ, "message": message}
    if extra is not None:
        ev["extra"] = extra
    st.setdefault("events", []).append(ev)
    st["events"] = st["events"][-160:]
    write_json(STATE_PATH, st)
    append_jsonl(AGENT_LOG_PATH, ev)
    return ev


def default_config():
    return {
        "project_name": "SwarmCell_OmniCell_autosolver",
        "one_click_training": {
            "enabled": True,
            "pre_backup": True,
            "auto_notes": True,
            "auto_handover": True,
            "mode": "local_test_with_autonomous_patch",
            "autonomous_patch_enabled": True,
            "patch_surface": "CONFIG_ONLY_WITH_DIFF_AND_GATE",
            "local_test_command": "python local_test.py submission/solver.py {case_file}",
            "submit_url": "https://hackathon.mykeeta.com/",
            "require_user_confirm_submit": True,
            "max_seconds_per_round": 120
        },
        "score_feedback": {
            "enabled": True,
            "upload_screenshot": True,
            "ocr_engine": "qwen_vl_openai_compatible",
            "qwen_base_url": "https://ws-bmzspe69czs7znm9.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1",
            "qwen_ocr_model": "qwen-vl-ocr",
            "history_file": "memory/studio/score_feedback_history.jsonl",
            "latest_file": "memory/studio/score_feedback_latest.json",
            "next_round_plan": "config/next_round_plan.json",
            "lower_is_better": True,
            "protected_cases": ["small_seed100", "tiny_seed42", "scarce_couriers_seed401"]
        },
        "data_generation": {
            "enabled": True,
            "base_case": "cases/large_seed301.txt",
            "output_dir": "generated_cases",
            "last_manifest": "memory/studio/generated_cases_latest.json",
            "run_after_param_save": False,
            "scenario_targets": ["high_noise_seed601", "large_seed301", "large_seed302", "low_willingness_seed501", "medium_seed201", "medium_seed202", "medium_seed203", "scarce_couriers_seed401", "small_seed100", "tiny_seed42"]
        },
        "scenes": {
            "high_noise_seed601": {"noise_guard": 0.82, "regret_weight": 0.74, "backup_order": 0.36, "lns_budget_ms": 650, "risk": "避免 score 噪声诱导的贪心陷阱，优先 regret + pair swap 小步修复"},
            "large_seed301": {"route_topology_lock": 0.9, "seed_auto": 301, "polish_budget_ms": 900, "backup_order": 0.42, "risk": "大规模算例保持 V4-A anchor，不做 broad topology repair"},
            "large_seed302": {"route_topology_lock": 0.95, "seed_auto": 302, "polish_budget_ms": 300, "backup_order": 0.28, "risk": "暂缓，仅在 hard-lock 稳定后再打开 gate"},
            "low_willingness_seed501": {"willingness_threshold": 0.18, "multi_courier": 0.88, "main_solver_budget_ms": 500, "backup_budget_ms": 3500, "risk": "低意愿场景重点校准 backup list / backup order"},
            "medium_seed202": {"remove2_repair": 0.75, "output_swap": 0.66, "lns_budget_ms": 700, "risk": "固定 champion 解后做小范围输出级替换"},
            "scarce_couriers_seed401": {"courier_ratio_gate": 0.95, "bundle_first": 0.8, "lns_budget_ms": 0, "risk": "保护场景，禁止激进 LNS；只允许节省骑手的保守 patch"}
        }
    }


def default_state():
    return {
        "project": "SwarmCell_OmniCell_autosolver",
        "version": "5.0-deepseek-llm-qwen-ocr-final-train",
        "champion": {"score": 716.74, "solver_path": "submission/solver.py", "solver_size_kb": 64.2, "status": "locked"},
        "candidate": {"score": None, "status": "idle", "round": 0},
        "protected_cases": {
            "scarce_couriers_seed401": {"threshold": 1558, "last": 1554.38},
            "small_seed100": {"threshold": 309, "last": 306.91},
            "tiny_seed42": {"threshold": 160, "last": 158.65}
        },
        "case_results": [
            {"case": "high_noise_seed601", "champion": 497.06, "candidate": None, "assigned": "30/30", "status": "watch", "delta_history": [0, -0.8, 1.6, -1.1, -0.2], "acceptance": 0.62, "x": 35, "y": 24},
            {"case": "large_seed301", "champion": 675.35, "candidate": None, "assigned": "40/40", "status": "anchor", "delta_history": [0, 2.2, -1.4, 0.5, -0.7], "acceptance": 0.74, "x": 72, "y": 32},
            {"case": "large_seed302", "champion": 639.68, "candidate": None, "assigned": "40/40", "status": "watch", "delta_history": [0, 3.4, 1.8, 0.2, 0.1], "acceptance": 0.69, "x": 76, "y": 52},
            {"case": "low_willingness_seed501", "champion": 1810.77, "candidate": None, "assigned": "30/30", "status": "watch", "delta_history": [0, -12, 8, -5, -2], "acceptance": 0.115, "x": 22, "y": 68},
            {"case": "medium_seed201", "champion": 494.86, "candidate": None, "assigned": "30/30", "status": "target", "delta_history": [0, -0.6, -1.1, -0.4, -0.9], "acceptance": 0.71, "x": 48, "y": 46},
            {"case": "medium_seed202", "champion": 527.59, "candidate": None, "assigned": "30/30", "status": "target", "delta_history": [0, 1.5, -0.2, -0.7, -1.3], "acceptance": 0.7, "x": 53, "y": 50},
            {"case": "medium_seed203", "champion": 508.65, "candidate": None, "assigned": "30/30", "status": "target", "delta_history": [0, -0.3, 0.8, -0.6, -0.4], "acceptance": 0.72, "x": 58, "y": 42},
            {"case": "scarce_couriers_seed401", "champion": 1554.38, "candidate": None, "assigned": "40/40", "status": "protected", "delta_history": [0, 16, 4, 0.3, -0.1], "acceptance": 0.54, "x": 31, "y": 50},
            {"case": "small_seed100", "champion": 306.91, "candidate": None, "assigned": "15/15", "status": "protected", "delta_history": [0, 89, 3, 0, 0], "acceptance": 0.78, "x": 44, "y": 75},
            {"case": "tiny_seed42", "champion": 158.65, "candidate": None, "assigned": "6/6", "status": "protected", "delta_history": [0, 12, 1, 0, 0], "acceptance": 0.82, "x": 62, "y": 74}
        ],
        "agents": [
            {"id": "leader", "name": "Leader Agent", "role": "总控指挥", "status": "watching", "energy": 92, "desk": [52, 18], "color": "#ff3d57", "last_action": "等待一键训练或用户冻结指令", "key_data": {"round": 0, "gate": "no-regression on"}, "actions": ["制定本轮训练目标", "收集各 Agent 结果", "写入 Handover"]},
            {"id": "data_seed", "name": "Data Seed Agent", "role": "数据种子生成", "status": "idle", "energy": 80, "desk": [22, 33], "color": "#2be88a", "last_action": "准备 high_noise/large/low 的 seed profile", "key_data": {"seed_pool": 1500, "focus": "large_seed301"}, "actions": ["生成 mid-training 样本", "标注场景特征", "输出 seed_config"]},
            {"id": "strategy", "name": "Strategy Agent", "role": "策略池分析", "status": "ready", "energy": 85, "desk": [48, 42], "color": "#a479ff", "last_action": "保留 V4-A anchor，禁止 broad repair", "key_data": {"active_patch": "backup-order calibration"}, "actions": ["候选策略排序", "风险分支隔离", "输出 DSL"]},
            {"id": "trainer", "name": "Trainer Agent", "role": "训练执行", "status": "idle", "energy": 70, "desk": [72, 33], "color": "#45a3ff", "last_action": "等待 One-Click Train", "key_data": {"mode": "dry_run/local_test auto"}, "actions": ["训练前备份", "执行本地评测", "记录 trial"]},
            {"id": "hyper", "name": "HyperParam Agent", "role": "超参调优", "status": "ready", "energy": 78, "desk": [26, 63], "color": "#ff9d42", "last_action": "调节 high_noise regret_weight 与 low backup_budget", "key_data": {"sliders": "DataLab"}, "actions": ["读取 DataLab 参数", "生成场景预算", "写入 compact config"]},
            {"id": "evaluator", "name": "Evaluator Agent", "role": "分数评估", "status": "ready", "energy": 88, "desk": [48, 72], "color": "#2ee9ff", "last_action": "维护每个 case 的分数曲线与热力图", "key_data": {"protected": "small/tiny/scarce"}, "actions": ["统计分数差异", "判定 no-regression", "输出可视化数据"]},
            {"id": "reflector", "name": "LLM Reflector", "role": "DeepSeek LLM 错误归因", "status": "ready-if-key", "energy": 64, "desk": [73, 64], "color": "#ff6bd6", "last_action": "记录 DeepSeek-V4-pro 对话到 memory/chat.jsonl", "key_data": {"model": "DeepSeek-V4-pro / qwen-vl-ocr"}, "actions": ["归因失败原因", "整理错误代码片段", "写 Notes", "根据 OCR 反馈更新 Forensics"]},
            {"id": "auditor", "name": "Auditor Agent", "role": "代码审核与裁剪", "status": "armed", "energy": 96, "desk": [86, 17], "color": "#d9e4ff", "last_action": "扫描 solver.py 大小/危险依赖/hash", "key_data": {"hard_limit": "100KB"}, "actions": ["代码巡查", "备份 manifest", "回滚前二次校验"]},
            {"id": "distiller", "name": "Distiller Agent", "role": "compact config 蒸馏", "status": "ready", "energy": 82, "desk": [13, 76], "color": "#35e0bd", "last_action": "把有效策略压缩为 config/seed_config*.json", "key_data": {"target": "small static solver"}, "actions": ["策略裁剪", "配置压缩", "导出 candidate"]},
            {"id": "submitter", "name": "Submit Agent", "role": "半自动打包提交", "status": "locked", "energy": 60, "desk": [86, 78], "color": "#ffe15a", "last_action": "官方 URL 只记录，不自动点击提交", "key_data": {"url": "https://hackathon.mykeeta.com/"}, "actions": ["生成提交包", "等待用户确认", "记录提交反馈"]}
        ],
        "flow_nodes": [
            {"id": "Start", "state": "ready", "code": "round_ctx = load_config(); assert project.startswith('SwarmCell_OmniCell')", "detail": "加载配置、读取上一轮 champion 与备份清单。"},
            {"id": "PreBackup", "state": "guard", "code": "python tools/champion_guard.py backup --tag pre_train_round_N", "detail": "每次训练前自动备份 solver/memory/config/docs，并生成 manifest/hash。"},
            {"id": "LoadCase", "state": "ready", "code": "features = extract_case_features(input_text)", "detail": "解析任务、骑手、分数、合单、willingness。"},
            {"id": "SceneRoute", "state": "ready", "code": "scene = route(features, avg_willingness, courier_ratio, noise_score)", "detail": "识别 high_noise / low / scarce / medium / large / small / tiny。"},
            {"id": "StrategyDispatch", "state": "live", "code": "candidate = apply_anchor_preserving_patch(champion, scene_config)", "detail": "V6 anchor-preserving：新分支不得丢掉已有收益。"},
            {"id": "Evaluate", "state": "ready", "code": "score = local_test(candidate); diff = score - champion_score", "detail": "记录每个 case 的分数、覆盖、耗时、风险标签。"},
            {"id": "ScoreFeedback", "state": "live", "code": "text = qwen_ocr(image); feedback = parse_scores(text); update_cockpit_and_forensics(feedback)", "detail": "用户上传线上提交截图后，Qwen OCR/LLM/本地 Agent 解析分数，与上一轮对比，实时更新 Cockpit 和错误归因。"},
            {"id": "Forensics", "state": "ready", "code": "bad_code = diff_slice(prev_solver, candidate_solver); write_notes(bad_code)", "detail": "归因错误原因与本轮改坏的代码片段。"},
            {"id": "NoRegression", "state": "guard", "code": "if protected_case_regresses: restore(champion_backup)", "detail": "small/tiny/scarce 红线退化即拒绝。"},
            {"id": "Distill", "state": "ready", "code": "compact_config = distill(accepted_patch); export_static_solver()", "detail": "将有效策略压缩为静态配置，避免 solver.py 膨胀。"},
            {"id": "Rollback", "state": "guard", "code": "restore(selected_backup) after double_confirm", "detail": "可视化选择版本，二次确认后恢复。"}
        ],
        "forensics": [
            {"scene": "large_seed301", "finding": "large polish 曾触发路线拓扑漂移，导致 anchor 解收益被覆盖。", "severity": "warning", "reason": "改动把 broad topology repair 放在 anchor fallback 之前，large_seed301 的稳定合单结构被重排。", "bad_code": "if scene.startswith('large'):\n    solution = broad_topology_repair(solution)  # 错：覆盖 anchor", "patch": "if scene == 'large_seed301':\n    solution = anchor_preserving_repair(solution, backup_order_only=True)"},
            {"scene": "high_noise_seed601", "finding": "高噪音场景不能只按 primary topology 继续微调。", "severity": "warning", "reason": "primary route 多轮不动，说明分数噪声下主路径已陷入局部最优；下一轮只改 backup list / backup order。", "bad_code": "rank = sorted(candidates, key=lambda x: x.primary_score)", "patch": "rank = regret_weighted_backup_order(candidates, noise_guard=0.82)"},
            {"scene": "small_seed100 / scarce_couriers_seed401", "finding": "protected case 曾被激进 LNS 误伤。", "severity": "danger", "reason": "保护场景不应共享 medium/high_noise 的 remove2/mini_lns patch。", "bad_code": "if time_left > 2: solution = mini_lns(solution)", "patch": "if scene in PROTECTED: return champion_solution"}
        ],
        "score_feedback": {"average_score": None, "avg_delta": None, "completed_cases": None, "analysis": {"summary": "等待上传线上分数截图。", "next_focus": []}, "cases": []},
        "score_feedback_history": [],
        "commands": []
    }


def ensure_bootstrap_files():
    MEMORY.mkdir(exist_ok=True)
    DOCS.mkdir(exist_ok=True)
    CONFIG.mkdir(exist_ok=True)
    BACKUPS.mkdir(exist_ok=True)
    LOGS.mkdir(exist_ok=True)
    SCORE_FEEDBACK_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_PATH.exists():
        write_json(CONFIG_PATH, default_config())
    if not STATE_PATH.exists():
        st = default_state()
        write_json(STATE_PATH, st)
    if not NOTES_PATH.exists():
        NOTES_PATH.write_text("# Notes.md\n\n用于记录每轮训练、错误归因、错误代码片段与修正建议。\n", encoding="utf-8")
    if not HANDOVER_PATH.exists():
        HANDOVER_PATH.write_text("# Handover.md\n\n用于交接当前 champion、candidate、风险分支、回滚点与下一轮建议。\n", encoding="utf-8")


def audit_solver():
    solver_path = ROOT / "submission" / "solver.py"
    if not solver_path.exists():
        return {"exists": False, "size_kb": 0, "risk": "missing", "dangerous": [], "sha256": "missing"}
    text = solver_path.read_text(encoding="utf-8", errors="ignore")
    size_kb = solver_path.stat().st_size / 1024.0
    dangerous = [kw for kw in DANGEROUS_KEYWORDS if kw in text]
    fn_count = text.count("def ")
    lines = text.count("\n") + 1
    if size_kb > 100 or dangerous:
        risk = "danger"
    elif size_kb > 80:
        risk = "warning"
    else:
        risk = "ok"
    return {
        "exists": True,
        "size_kb": round(size_kb, 2),
        "limit_kb": 100,
        "risk": risk,
        "dangerous": dangerous,
        "functions": fn_count,
        "lines": lines,
        "sha256": file_sha(solver_path, short=True),
        "full_sha256": file_sha(solver_path, short=False),
    }


def inspect_backup_archive(path: Path):
    item = {
        "archive": path.name,
        "created_at": dt.datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
        "size_kb": round(path.stat().st_size / 1024, 2),
        "manifest": {},
        "solver_hash": "--",
        "solver_name": "submission/solver.py",
        "round": "--",
        "note": ""
    }
    try:
        with zipfile.ZipFile(path) as z:
            if "BACKUP_MANIFEST.json" in z.namelist():
                manifest = json.loads(z.read("BACKUP_MANIFEST.json").decode("utf-8"))
                item["manifest"] = manifest
                item["created_at"] = manifest.get("created_at", item["created_at"])
                item["note"] = manifest.get("note", "")
                item["round"] = manifest.get("round", "--")
                for f in manifest.get("files", []):
                    if f.get("path") == "submission/solver.py":
                        item["solver_hash"] = f.get("sha256", "--")[:16]
                        item["solver_name"] = f.get("path", "submission/solver.py")
                        break
    except Exception as e:
        item["note"] = f"manifest read failed: {e}"
    return item


def list_backups(limit=80):
    BACKUPS.mkdir(exist_ok=True)
    zips = sorted(BACKUPS.glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
    return [inspect_backup_archive(p) for p in zips[:limit]]


def run_tool(cmd, timeout=180):
    try:
        p = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=timeout)
        return {"ok": p.returncode == 0, "code": p.returncode, "stdout": p.stdout[-8000:], "stderr": p.stderr[-8000:]}
    except Exception as e:
        return {"ok": False, "code": -1, "stdout": "", "stderr": f"{type(e).__name__}: {e}"}


def llm_chat(prompt: str):
    cfg = deepseek_env_config()
    api_key = cfg.get("api_key", "")
    base = cfg.get("base_url", "https://api.deepseek.com").rstrip("/")
    model = cfg.get("model", "DeepSeek-V4-pro")
    if not api_key:
        return {"ok": False, "message": "DeepSeek API key 未设置。请在环境变量或 config/.env.local 配置 DEEPSEEK_API_KEY；Qwen 只用于 OCR。", "model": model, "provider": "missing"}
    url = base + "/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你是 SwarmCell / OmniCell 的 DeepSeek-V4-pro LLM Reflector。负责错误归因、策略 DSL、patch 计划解释和安全审计建议。Qwen 仅用于 OCR。需要改写 solver.py 时必须走 autonomous_patch_agent 的 diff + no-regression gate，且 solver.py 不允许注释、不超过 100KB。"},
            {"role": "user", "content": prompt}
        ],
        "max_tokens": 4096,
        "thinking": {"type": "disabled"}
    }
    req = urllib.request.Request(url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), headers={"Content-Type": "application/json", "Authorization": "Bearer " + api_key}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="ignore"))
        msg = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        return {"ok": True, "message": msg, "model": model, "provider": "deepseek"}
    except Exception as e:
        return {"ok": False, "message": f"DeepSeek 调用失败：{type(e).__name__}: {e}", "model": model, "provider": "deepseek"}


def load_full_state():
    ensure_bootstrap_files()
    st = read_json(STATE_PATH, default_state())
    # migrate old V2 state without overwriting scores/events
    base = default_state()
    for k, v in base.items():
        if k not in st:
            st[k] = v
    st["project"] = "SwarmCell_OmniCell_autosolver"
    st["version"] = "5.0-deepseek-llm-qwen-ocr-final-train"
    cfg = read_json(CONFIG_PATH, default_config())
    st["training_config"] = cfg
    st["audit"] = audit_solver()
    st["backups"] = list_backups()
    st["logs_preview"] = {
        "notes": NOTES_PATH.read_text(encoding="utf-8", errors="ignore")[-6000:] if NOTES_PATH.exists() else "",
        "handover": HANDOVER_PATH.read_text(encoding="utf-8", errors="ignore")[-6000:] if HANDOVER_PATH.exists() else "",
        "chat": read_jsonl(CHAT_PATH, 80),
        "trials": read_jsonl(TRIALS_PATH, 80),
        "agent_logs": read_jsonl(AGENT_LOG_PATH, 120),
        "training_rounds": read_jsonl(TRAINING_LOG_PATH, 80),
        "patch_reports": read_jsonl(PATCH_LOG_PATH, 40),
    }
    st["patch_reports"] = st["logs_preview"].get("patch_reports", [])
    latest_feedback = read_json(SCORE_FEEDBACK_LATEST, st.get("score_feedback", {}))
    st["score_feedback"] = latest_feedback or st.get("score_feedback", {})
    st["score_feedback_history"] = read_jsonl(SCORE_FEEDBACK_HISTORY, 60) or st.get("score_feedback_history", [])
    st["next_round_plan"] = read_json(NEXT_ROUND_PLAN_PATH, {})
    st["qwen"] = qwen_config_status()
    st["qwen"]["role"] = "ocr_only"
    st["deepseek"] = deepseek_config_status()
    st["generated_cases"] = read_json(MEMORY / "generated_cases_latest.json", st.get("generated_cases", {}))
    try:
        diffs = sorted(PATCH_DIFF_DIR.glob("*.diff"), key=lambda x: x.stat().st_mtime, reverse=True) if PATCH_DIFF_DIR.exists() else []
        st["latest_patch_diff"] = diffs[0].read_text(encoding="utf-8", errors="ignore")[-16000:] if diffs else ""
    except Exception:
        st["latest_patch_diff"] = ""
    return st


class Handler(SimpleHTTPRequestHandler):
    def translate_path(self, path):
        parsed = urlparse(path).path
        if parsed in {"/", "/index.html"}:
            return str(DASHBOARD / "index.html")
        if parsed.startswith("/dashboard/"):
            return str(ROOT / parsed.lstrip("/"))
        return str(ROOT / parsed.lstrip("/"))

    def send_json(self, data, code=200):
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def read_body(self):
        n = int(self.headers.get("Content-Length", "0") or "0")
        if n <= 0:
            return {}
        raw = self.rfile.read(n).decode("utf-8", errors="ignore")
        try:
            return json.loads(raw)
        except Exception:
            return {"raw": raw}

    def do_GET(self):
        p = urlparse(self.path).path
        if p == "/api/state":
            self.send_json(load_full_state())
            return
        if p == "/api/audit":
            self.send_json(audit_solver())
            return
        if p == "/api/backups":
            self.send_json({"items": list_backups()})
            return
        if p == "/api/logs":
            st = load_full_state()
            self.send_json(st["logs_preview"])
            return
        if p == "/api/patches":
            self.send_json({"items": read_jsonl(PATCH_LOG_PATH, 80), "latest_diff": load_full_state().get("latest_patch_diff", "")})
            return
        if p == "/api/qwen/status":
            self.send_json(qwen_config_status())
            return
        if p == "/api/solver":
            sp = ROOT / "submission" / "solver.py"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(sp.read_bytes() if sp.exists() else b"")
            return
        return super().do_GET()

    def do_POST(self):
        p = urlparse(self.path).path
        body = self.read_body()
        if p != "/api/action":
            if p == "/api/chat":
                self.handle_chat(body)
                return
            if p == "/api/feedback/upload":
                self.handle_feedback_upload(body)
                return
            if p == "/api/feedback/text":
                self.handle_feedback_text(body)
                return
            self.send_json({"ok": False, "error": "unknown endpoint"}, code=404)
            return

        action = body.get("action")
        if action == "pause":
            ev = append_event("Leader", "user", "用户请求暂停训练。")
            self.send_json({"ok": True, "event": ev, "state": load_full_state()})
            return

        if action == "audit":
            ev = append_event("Auditor", "audit", "用户请求审计 solver.py。")
            self.send_json({"ok": True, "event": ev, "audit": audit_solver(), "state": load_full_state()})
            return

        if action == "freeze_scene":
            scene = body.get("scene", "unknown")
            ev = append_event("Auditor", "guard", f"用户冻结场景：{scene}")
            self.send_json({"ok": True, "event": ev, "state": load_full_state()})
            return

        if action == "backup":
            note = body.get("note") or "created from dashboard"
            tag = body.get("tag") or "ui_backup"
            ev = append_event("Auditor", "backup", "用户点击创建备份：执行 champion_guard backup。")
            res = run_tool([sys.executable, "tools/champion_guard.py", "backup", "--tag", tag, "--note", note])
            append_event("Auditor", "backup-result", "备份完成，已刷新备份清单。" if res["ok"] else "备份失败。", res)
            self.send_json({"ok": res["ok"], "event": ev, "result": res, "backups": list_backups(), "state": load_full_state()})
            return

        if action == "one_click_train":
            change = body.get("change") or "UI one-click training with current DataLab parameters"
            ev = append_event("Trainer", "train", "决赛日一键训练启动：生成多场景种子、DeepSeek 归因、自主 patch / 本地 gate / 日志归因。")
            res = run_tool([sys.executable, "tools/final_day_trainer.py", "--source", "dashboard", "--rounds", "1", "--submission-budget", "18", "--change", change], timeout=900)
            append_event("Trainer", "train-result", "一键训练流程已结束，已写入 Notes/Handover/日志。" if res["ok"] else "一键训练流程失败，已记录 stderr。", res)
            self.send_json({"ok": res["ok"], "event": ev, "result": res, "state": load_full_state()})
            return

        if action == "autonomous_patch":
            objective = body.get("objective") or body.get("change") or "Autonomously improve solver.py with safe CONFIG patch and no-regression gate"
            ev = append_event("Patch Generator Agent", "autopatch", "用户触发自主改写 solver.py：生成 patch、diff 审查、no-regression gate。")
            res = run_tool([sys.executable, "tools/autonomous_patch_agent.py", "--source", "dashboard", "--objective", objective], timeout=420)
            append_event("Gate Keeper", "autopatch-result", "自主改写流程结束，已记录 patch report。" if res["ok"] else "自主改写流程失败或被 gate 拒绝。", res)
            self.send_json({"ok": res["ok"], "event": ev, "result": res, "state": load_full_state()})
            return

        if action == "save_params":
            scene = body.get("scene")
            params = body.get("params") or {}
            cfg = read_json(CONFIG_PATH, default_config())
            if scene:
                cfg.setdefault("scenes", {}).setdefault(scene, {}).update(params)
                write_json(CONFIG_PATH, cfg)
                ev = append_event("HyperParam", "params", f"DataLab 参数已保存：{scene}", params)
                self.send_json({"ok": True, "event": ev, "config": cfg, "state": load_full_state()})
            else:
                self.send_json({"ok": False, "error": "missing scene"}, code=400)
            return

        if action == "auto_seed_config":
            target = body.get("target") or "all"
            cfg = read_json(CONFIG_PATH, default_config())
            data_cfg = cfg.get("data_generation", {})
            base_case = body.get("base_case") or data_cfg.get("base_case", "cases/large_seed301.txt")
            seed = str(body.get("seed") or cfg.get("scenes", {}).get("large_seed301", {}).get("seed_auto", 301))
            ev = append_event("Data Seed Agent", "seed-config", f"DataLab 触发随机种子生成：target={target}, base={base_case}", {"target": target, "base_case": base_case, "seed": seed})
            res = run_tool([sys.executable, "tools/generate_midtrain_cases.py", "--base", base_case, "--target", target, "--seed", seed], timeout=240)
            manifest = read_json(MEMORY / "generated_cases_latest.json", {})
            if res.get("ok"):
                append_event("Data Seed Agent", "seed-result", f"随机种子样本生成完成：{len(manifest.get('items', []))} 组场景。", manifest)
            else:
                append_event("Data Seed Agent", "seed-result", "随机种子样本生成失败，请查看 stderr。", res)
            self.send_json({"ok": res["ok"], "event": ev, "result": res, "seed_config": manifest, "state": load_full_state()})
            return

        if action == "rollback":
            archive = body.get("archive", "")
            confirm = body.get("confirm", "")
            if confirm != "ROLLBACK":
                self.send_json({"ok": False, "error": "rollback requires confirm=ROLLBACK"}, code=400)
                return
            if not archive:
                self.send_json({"ok": False, "error": "missing archive"}, code=400)
                return
            ev = append_event("Auditor", "rollback", f"用户二次确认后一键回滚到：{archive}")
            res = run_tool([sys.executable, "tools/champion_guard.py", "restore", "--archive", archive], timeout=240)
            append_event("Auditor", "rollback-result", "回滚完成，已创建 pre_restore 保护备份。" if res["ok"] else "回滚失败。", res)
            self.send_json({"ok": res["ok"], "event": ev, "result": res, "state": load_full_state()})
            return

        if action == "submit_prepare":
            ev = append_event("Submit Agent", "submit-prepare", "已生成提交前检查事件；平台 URL 只记录，不自动提交。")
            self.send_json({"ok": True, "event": ev, "submit_url": read_json(CONFIG_PATH, default_config())["one_click_training"]["submit_url"], "state": load_full_state()})
            return

        ev = append_event("Leader", "user", f"收到用户动作：{action}")
        self.send_json({"ok": True, "event": ev, "state": load_full_state()})

    def handle_feedback_upload(self, body):
        data_url = body.get("image_data", "")
        filename = body.get("filename") or f"score_feedback_{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        notes = body.get("notes", "")
        if not data_url:
            self.send_json({"ok": False, "error": "missing image_data"}, code=400)
            return
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", filename)[-120:] or "score_feedback.png"
        stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        suffix = Path(safe_name).suffix.lower() if Path(safe_name).suffix else ".png"
        out = SCORE_FEEDBACK_DIR / f"upload_{stamp}{suffix}"
        try:
            if "," in data_url:
                data_url = data_url.split(",", 1)[1]
            raw = base64.b64decode(data_url)
            SCORE_FEEDBACK_DIR.mkdir(parents=True, exist_ok=True)
            out.write_bytes(raw)
        except Exception as e:
            self.send_json({"ok": False, "error": f"decode failed: {type(e).__name__}: {e}"}, code=400)
            return
        append_event("Score Feedback Agent", "upload", f"收到线上分数截图：{safe_name}，开始 OCR/归因/下一轮预测。")
        res = run_tool([sys.executable, "tools/score_feedback_agent.py", "--image", str(out), "--source", "dashboard_upload", "--notes", notes], timeout=180)
        latest = read_json(SCORE_FEEDBACK_LATEST, {})
        self.send_json({"ok": res["ok"], "result": res, "feedback": latest, "state": load_full_state()})

    def handle_feedback_text(self, body):
        text = body.get("text", "")
        notes = body.get("notes", "")
        if not text.strip():
            self.send_json({"ok": False, "error": "missing text"}, code=400)
            return
        tmp = SCORE_FEEDBACK_DIR / f"pasted_{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        SCORE_FEEDBACK_DIR.mkdir(parents=True, exist_ok=True)
        tmp.write_text(text, encoding="utf-8")
        append_event("Score Feedback Agent", "text-feedback", "收到用户粘贴的线上分数文本，开始归因与下一轮预测。")
        res = run_tool([sys.executable, "tools/score_feedback_agent.py", "--text-file", str(tmp), "--source", "dashboard_text", "--notes", notes], timeout=180)
        latest = read_json(SCORE_FEEDBACK_LATEST, {})
        self.send_json({"ok": res["ok"], "result": res, "feedback": latest, "state": load_full_state()})

    def handle_chat(self, body):
        msg = body.get("message", "").strip()
        if not msg:
            self.send_json({"ok": False, "message": "empty"})
            return
        append_jsonl(CHAT_PATH, {"time": iso_now(), "role": "user", "message": msg})
        append_event("User", "chat", msg[:160])
        res = llm_chat(msg)
        append_jsonl(CHAT_PATH, {"time": iso_now(), "role": "assistant", "message": res["message"], "ok": res["ok"], "model": res.get("model")})
        append_event("LLM Reflector", "llm", res["message"][:180])
        self.send_json(res)


def main():
    ensure_bootstrap_files()
    port = int(os.getenv("RSD_STUDIO_PORT", "8765"))
    print(f"SwarmCell_OmniCell_autosolver running at http://127.0.0.1:{port}")
    print("Qwen OCR model:", qwen_config_status().get("ocr_model"))
    print("DeepSeek LLM model:", deepseek_config_status().get("model"))
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
