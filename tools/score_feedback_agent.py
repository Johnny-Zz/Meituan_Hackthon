#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Score Feedback Agent for MeituanRSD_autosolver.

Purpose:
- Ingest official online judge score screenshots or pasted OCR text.
- Extract per-case score / coverage / runtime.
- Compare with previous official feedback or current dashboard state.
- Update Cockpit state so every chart can show current-vs-previous deltas.
- Generate next-round improvement direction for autonomous patch agents.

The script is dependency-light:
- OCR is Qwen OpenAI-compatible vision API when QWEN_API_KEY is configured.
- LLM attribution is DeepSeek-V4-pro only; Qwen is never used for chat/forensics.
- If both OCR paths are unavailable, caller can pass --text-file or --raw-text and the same analysis pipeline still runs.
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import os
import re
import sys
import urllib.request
from pathlib import Path
from typing import Dict, List, Tuple, Any

try:
    from qwen_client import ocr_image as qwen_ocr_image, config_status as qwen_config_status
except Exception:  # keep script runnable even if copied without tools/qwen_client.py
    qwen_ocr_image = None
    def qwen_config_status():
        return {"configured": False, "error": "qwen_client unavailable"}
try:
    from deepseek_client import env_config as deepseek_env_config, parse_json_object as deepseek_parse_json
except Exception:
    def deepseek_env_config():
        return {"api_key": "", "base_url": "https://api.deepseek.com", "model": "DeepSeek-V4-pro"}
    def deepseek_parse_json(text):
        return json.loads(text)


ROOT = Path(__file__).resolve().parents[1]
MEMORY = ROOT / "memory" / "studio"
DOCS = ROOT / "docs"
CONFIG = ROOT / "config"
STATE_PATH = MEMORY / "current_state.json"
AGENT_LOG_PATH = MEMORY / "agent_logs.jsonl"
CHAT_PATH = MEMORY / "chat.jsonl"
NOTES_PATH = DOCS / "Notes.md"
HANDOVER_PATH = DOCS / "Handover.md"
FEEDBACK_DIR = MEMORY / "score_feedback"
FEEDBACK_HISTORY = MEMORY / "score_feedback_history.jsonl"
FEEDBACK_LATEST = MEMORY / "score_feedback_latest.json"
NEXT_PLAN_PATH = CONFIG / "next_round_plan.json"

CASE_KEYS = [
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

CASE_ALIASES = {
    "high_noise_seed601": [r"high[_\s-]*noise[_\s-]*seed601", r"high[_\s-]*noise\s*601"],
    "large_seed301": [r"large[_\s-]*seed301", r"large\s*301"],
    "large_seed302": [r"large[_\s-]*seed302", r"large\s*302"],
    "low_willingness_seed501": [r"low[_\s-]*willingness[_\s-]*seed501", r"low[_\s-]*willingness\s*501", r"low[_\s-]*willingness[_\s-]*seed\s*501"],
    "medium_seed201": [r"medium[_\s-]*seed201", r"medium\s*201"],
    "medium_seed202": [r"medium[_\s-]*seed202", r"medium\s*202"],
    "medium_seed203": [r"medium[_\s-]*seed203", r"medium\s*203"],
    "scarce_couriers_seed401": [r"scarce[_\s-]*couriers[_\s-]*seed401", r"scarce[_\s-]*couriers\s*401"],
    "small_seed100": [r"small[_\s-]*seed100", r"small\s*100"],
    "tiny_seed42": [r"tiny[_\s-]*seed42", r"tiny\s*42"],
}

NUMBER_RE = r"([0-9][0-9,]*\.[0-9]+)"
COVERAGE_RE = r"(\d+)\s*/\s*(\d+)"
TIME_RE = r"(\d{2,6})\s*ms"


def iso_now() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def compact_time() -> str:
    return dt.datetime.now().strftime("%H:%M:%S")


def read_json(path: Path, default: Any):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, data: Any):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def append_jsonl(path: Path, obj: Dict[str, Any]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def read_jsonl(path: Path, limit: int = 200) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines()[-limit:]:
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return out


def append_event(agent: str, typ: str, message: str, extra: Dict[str, Any] | None = None):
    st = read_json(STATE_PATH, {})
    ev = {"time": compact_time(), "iso": iso_now(), "agent": agent, "type": typ, "message": message}
    if extra is not None:
        ev["extra"] = extra
    st.setdefault("events", []).append(ev)
    st["events"] = st["events"][-180:]
    write_json(STATE_PATH, st)
    append_jsonl(AGENT_LOG_PATH, ev)


def _ocr_with_tesseract(image_path: Path) -> Tuple[str, str]:
    try:
        from PIL import Image, ImageOps, ImageEnhance
        import pytesseract
    except Exception as e:
        return "", f"pytesseract_unavailable: {type(e).__name__}: {e}"
    try:
        img = Image.open(image_path).convert("RGB")
        # Upscale and improve contrast for dark online-judge screenshots.
        w, h = img.size
        scale = 2 if max(w, h) < 1600 else 1
        if scale > 1:
            img = img.resize((w * scale, h * scale))
        gray = ImageOps.grayscale(img)
        gray = ImageEnhance.Contrast(gray).enhance(1.85)
        text = pytesseract.image_to_string(gray, config="--psm 6")
        if not text.strip():
            text = pytesseract.image_to_string(img, config="--psm 6")
        return text, "pytesseract"
    except Exception as e:
        return "", f"pytesseract_failed: {type(e).__name__}: {e}"


def ocr_image(image_path: Path) -> Tuple[str, str]:
    """OCR a judge screenshot. Qwen is the primary production path."""
    qwen_status = qwen_config_status()
    if qwen_ocr_image is not None and qwen_status.get("configured"):
        res = qwen_ocr_image(image_path)
        if res.ok and res.content.strip():
            return res.content, f"qwen:{res.model}"
        append_event("Score Feedback Agent", "qwen-ocr-fallback", f"Qwen OCR failed, fallback to pytesseract: {res.error}", {"model": res.model, "base_url": res.base_url})
    else:
        append_event("Score Feedback Agent", "qwen-ocr-missing", "QWEN_API_KEY 未配置，OCR 将尝试本地 pytesseract 或等待粘贴文本。", qwen_status)
    return _ocr_with_tesseract(image_path)


def normalize_text(text: str) -> str:
    # Keep line breaks but normalize common OCR mistakes/spaces.
    text = text.replace("，", ",").replace("–", "-").replace("—", "-")
    text = text.replace("_ ", "_").replace(" _", "_")
    text = re.sub(r"(?<=\d)[iIl](?=ms)", "1", text)  # OCR often reads 9151ms as 915ims
    text = re.sub(r"(?i)low[_\s]+willingness\s+seed", "low_willingness_seed", text)
    text = re.sub(r"(?i)scarce[_\s]+couriers\s+seed", "scarce_couriers_seed", text)
    text = re.sub(r"(?i)tiny\s+seed", "tiny_seed", text)
    return text


def find_case_in_line(line: str) -> Tuple[str | None, re.Match | None]:
    low = line.lower()
    for key in CASE_KEYS:
        for pat in CASE_ALIASES[key]:
            m = re.search(pat, low, flags=re.I)
            if m:
                return key, m
    return None, None


def parse_feedback_text(text: str) -> Dict[str, Any]:
    text = normalize_text(text)
    cases: Dict[str, Dict[str, Any]] = {}
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    for ln in lines:
        key, match = find_case_in_line(ln)
        if not key or not match:
            continue
        tail = ln[match.end():]
        nums = re.findall(NUMBER_RE, tail)
        if not nums:
            continue
        score = float(nums[0].replace(",", ""))
        cov = re.search(COVERAGE_RE, tail)
        time_m = re.search(TIME_RE, tail, flags=re.I)
        cases[key] = {
            "case": key,
            "score": round(score, 4),
            "assigned": f"{cov.group(1)}/{cov.group(2)}" if cov else "--",
            "covered": int(cov.group(1)) if cov else None,
            "total": int(cov.group(2)) if cov else None,
            "time_ms": int(time_m.group(1)) if time_m else None,
            "raw_line": ln,
        }
    avg_candidates = []
    for ln in lines[:8]:
        for n in re.findall(NUMBER_RE, ln):
            val = float(n.replace(",", ""))
            if 100 <= val <= 2500:
                avg_candidates.append(val)
    avg_score = None
    if len(cases) >= 2:
        avg_score = sum(c["score"] for c in cases.values()) / len(cases)
    elif avg_candidates:
        avg_score = avg_candidates[0]
    completed_match = None
    joined = "\n".join(lines)
    for m in re.finditer(r"(\d+)\s*/\s*(\d+)", joined):
        a, b = int(m.group(1)), int(m.group(2))
        if b == 10 or b >= len(cases):
            completed_match = f"{a}/{b}"
            break
    session_history = []
    for ln in lines:
        m = re.search(r"(\d{1,2}:\d{2}:\d{2})\s+" + NUMBER_RE + r"\s+(\d+)\s*/\s*(\d+)", ln)
        if m:
            session_history.append({"clock": m.group(1), "average_score": float(m.group(2).replace(',', '')), "completed_cases": f"{m.group(3)}/{m.group(4)}", "raw_line": ln})
    return {
        "cases": [cases[k] for k in CASE_KEYS if k in cases],
        "case_map": cases,
        "average_score": round(avg_score, 4) if avg_score is not None else None,
        "completed_cases": completed_match,
        "session_history": session_history,
        "raw_text": text,
    }


def get_previous_scores() -> Tuple[Dict[str, float], float | None, str]:
    latest = read_json(FEEDBACK_LATEST, {})
    if latest.get("cases"):
        return ({c["case"]: float(c["score"]) for c in latest.get("cases", [])}, latest.get("average_score"), "previous_official_feedback")
    st = read_json(STATE_PATH, {})
    prev = {}
    for row in st.get("case_results", []):
        if row.get("case"):
            val = row.get("official_score", row.get("champion"))
            if val is not None:
                prev[row["case"]] = float(val)
    avg = st.get("score_feedback", {}).get("average_score") or st.get("champion", {}).get("score")
    return prev, float(avg) if avg is not None else None, "dashboard_state"


def classify_case(row: Dict[str, Any], delta: float | None) -> str:
    case = row["case"]
    if case in {"small_seed100", "tiny_seed42", "scarce_couriers_seed401"}:
        if delta is not None and delta > 0.25:
            return "protected-regressed"
        return "protected"
    if delta is not None and delta < -0.25:
        return "improved"
    if delta is not None and delta > 0.25:
        return "regressed"
    return "stalled"


def generate_local_analysis(parsed: Dict[str, Any], previous_scores: Dict[str, float], prev_avg: float | None, source_label: str) -> Dict[str, Any]:
    rows = []
    for row in parsed["cases"]:
        prev = previous_scores.get(row["case"])
        delta = round(row["score"] - prev, 4) if prev is not None else None
        item = dict(row)
        item["previous_score"] = prev
        item["delta"] = delta
        item["trend"] = classify_case(row, delta)
        rows.append(item)
    avg = parsed.get("average_score")
    avg_delta = round(avg - prev_avg, 4) if avg is not None and prev_avg is not None else None
    improved = [r for r in rows if r.get("delta") is not None and r["delta"] < -0.25]
    regressed = [r for r in rows if r.get("delta") is not None and r["delta"] > 0.25]
    stalled = [r for r in rows if r.get("trend") == "stalled"]
    protected_bad = [r for r in rows if r.get("trend") == "protected-regressed"]
    slow = [r for r in rows if (r.get("time_ms") or 0) >= 8900]
    focus = []
    if protected_bad:
        focus.append("立即恢复保护门禁：small/tiny/scarce 出现退化时禁用本轮 patch。")
    # Prefer non-protected stalled cases and still-expensive cases.
    for name in ["high_noise_seed601", "medium_seed202", "large_seed302", "low_willingness_seed501", "large_seed301"]:
        row = next((r for r in rows if r["case"] == name), None)
        if not row:
            continue
        if row["trend"] in {"stalled", "regressed"} and row["case"] not in {"small_seed100", "tiny_seed42", "scarce_couriers_seed401"}:
            focus.append(f"{name}: {row['trend']}，下一轮优先做场景专属轻量校准。")
    if not focus:
        focus.append("保持当前 anchor，下一轮只做小步 CONFIG patch，避免破坏已有收益。")
    plan = []
    if any(r["case"] == "high_noise_seed601" for r in stalled + regressed):
        plan.append({"target": "high_noise_seed601", "patch_surface": "backup_order_only", "action": "提升 noise_guard/regret_weight，降低 primary topology 改写强度；只重排 backup list。", "risk": "不能共享到 small/tiny/scarce。"})
    if any(r["case"] == "medium_seed202" for r in stalled + regressed):
        plan.append({"target": "medium_seed202", "patch_surface": "output_level_swap", "action": "固定 champion 拓扑，只做 1-2 个 bundle 的输出级 swap 校准。", "risk": "禁止 broad topology repair。"})
    if any(r["case"] == "low_willingness_seed501" for r in rows):
        low = next((r for r in rows if r["case"] == "low_willingness_seed501"), None)
        if low and (low.get("delta") is None or low["delta"] <= 0.25):
            plan.append({"target": "low_willingness_seed501", "patch_surface": "backup_budget", "action": "保留本轮 low 收益；下一轮只微调 backup order 与 max_extra_couriers，不扩大主解搜索。", "risk": "主解 budget 扩大可能吃掉 backup 时间。"})
    if slow:
        plan.append({"target": "runtime", "patch_surface": "budget_gate", "action": "多个 case 接近 9s，下一轮 patch 必须保持 safety_margin，不引入额外 LNS。", "risk": ", ".join(r["case"] for r in slow[:5])})
    if not plan:
        plan.append({"target": "global", "patch_surface": "CONFIG_ONLY", "action": "只接受 no-regression 的微调；先积累第二张线上截图再扩大搜索。", "risk": "样本不足。"})
    summary_bits = []
    if avg_delta is not None:
        summary_bits.append(f"平均分 {prev_avg:.2f} → {avg:.2f}，Δ={avg_delta:+.2f}（lower is better）。")
    if improved:
        summary_bits.append("显著改善：" + ", ".join(f"{r['case']} {r['delta']:+.2f}" for r in improved[:6]) + "。")
    if regressed:
        summary_bits.append("退化警戒：" + ", ".join(f"{r['case']} {r['delta']:+.2f}" for r in regressed[:6]) + "。")
    if stalled:
        summary_bits.append("未动/平台期：" + ", ".join(r["case"] for r in stalled[:6]) + "。")
    if slow:
        summary_bits.append("耗时接近上限：" + ", ".join(f"{r['case']} {r['time_ms']}ms" for r in slow[:5]) + "。")
    return {
        "summary": " ".join(summary_bits) or "已解析线上反馈，但可比较样本不足。",
        "avg_delta": avg_delta,
        "previous_average_score": prev_avg,
        "comparison_source": source_label,
        "improved_cases": [r["case"] for r in improved],
        "regressed_cases": [r["case"] for r in regressed],
        "stalled_cases": [r["case"] for r in stalled],
        "slow_cases": [r["case"] for r in slow],
        "next_focus": focus[:6],
        "next_round_plan": plan[:8],
        "case_deltas": rows,
    }


def llm_refine_analysis(feedback: Dict[str, Any]) -> Dict[str, Any] | None:
    """Use DeepSeek-V4-pro to enrich local numeric analysis. Qwen is OCR-only."""
    prompt = (
        "你是 SwarmCell / OmniCell 的线上分数反馈分析 Agent。"
        "请基于 JSON 中的 case 分数、delta、耗时和保护场景，输出下一轮改进方向。"
        "必须遵守：lower_is_better；small/tiny/scarce 是保护场景；只能建议可回滚、可 no-regression gate 的 patch。"
        "请输出 JSON，字段包含 summary、next_focus、next_round_plan、risk_guard、forensics。"
        "forensics 是数组，每项包含 scene、finding、severity、reason、bad_code、patch。"
        "注意：DeepSeek 负责归因和迭代建议，Qwen 只负责 OCR。\n\n"
        + json.dumps(feedback, ensure_ascii=False)[:12000]
    )
    cfg = deepseek_env_config()
    api_key = cfg.get("api_key", "")
    if not api_key:
        return None
    base = cfg.get("base_url", "https://api.deepseek.com").rstrip("/")
    model = cfg.get("model", "DeepSeek-V4-pro")
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "输出必须是可解析 JSON，不要输出 markdown。"},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 3072,
        "response_format": {"type": "json_object"},
        "thinking": {"type": "disabled"},
    }
    try:
        req = urllib.request.Request(
            base + "/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + api_key},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="ignore"))
        msg = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
        append_jsonl(CHAT_PATH, {"time": iso_now(), "role": "assistant", "message": msg, "model": model, "source": "score_feedback_deepseek"})
        return deepseek_parse_json(msg)
    except Exception as e:
        append_event("Score Feedback Agent", "deepseek-llm-fallback", f"DeepSeek 归因失败，使用本地分析：{type(e).__name__}: {e}")
        return None


def build_forensics(record: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Create live, UI-ready error attribution cards from current feedback."""
    analysis = record.get("analysis", {})
    cases = record.get("cases", [])
    llm = record.get("llm_analysis") or {}
    llm_items = llm.get("forensics") if isinstance(llm, dict) else None
    items: List[Dict[str, Any]] = []
    if isinstance(llm_items, list):
        for it in llm_items[:8]:
            if isinstance(it, dict):
                z = dict(it)
                z.setdefault("source", "deepseek_llm")
                z.setdefault("updated_at", record.get("time"))
                items.append(z)
    for c in cases:
        delta = c.get("delta")
        trend = c.get("trend") or "unknown"
        case = c.get("case")
        if trend not in {"regressed", "protected-regressed", "stalled"} and not (c.get("time_ms") or 0) >= 8900:
            continue
        severity = "danger" if trend == "protected-regressed" else ("warning" if trend == "regressed" else "info")
        reason_bits = []
        if delta is not None:
            reason_bits.append(f"本轮 score={c.get('score')}，上一轮={c.get('previous_score')}，Δ={delta:+.4f}。")
        if trend == "stalled":
            reason_bits.append("该场景进入平台期，主拓扑继续大改的边际收益低。")
        if trend == "regressed":
            reason_bits.append("候选 patch 可能破坏了 anchor-preserving 或 backup order。")
        if trend == "protected-regressed":
            reason_bits.append("保护场景退化，必须优先恢复 no-regression hard lock。")
        if (c.get("time_ms") or 0) >= 8900:
            reason_bits.append(f"耗时 {c.get('time_ms')}ms 接近上限，下一轮禁止扩大搜索半径。")
        bad_code = "candidate = broad_topology_repair(candidate)\nif candidate.score < best.score: accept(candidate)"
        patch = "只允许 CONFIG_ONLY / backup_order_only / output_level_swap；先过 local gate，再写入 next_round_plan。"
        if case == "low_willingness_seed501":
            bad_code = "backup_pool.sort(key=lambda x: x.score)  # 忽略 willingness"
            patch = "backup_pool.sort(key=lambda x: (x.score / max(x.willingness, 1e-6), x.score)); 限制 max_extra_couriers。"
        elif case == "high_noise_seed601":
            bad_code = "rank = sorted(candidates, key=lambda x: x.primary_score)"
            patch = "提高 noise_guard / regret_weight，只重排 backup list，不改 primary topology。"
        elif case == "medium_seed202":
            bad_code = "solution = global_lns(solution)  # broad topology repair"
            patch = "固定 champion 解，只尝试 1-2 个 bundle 的 output-level swap。"
        elif case in {"small_seed100", "tiny_seed42", "scarce_couriers_seed401"}:
            bad_code = "if global_patch: apply_to_all_cases()"
            patch = "恢复 hard lock；禁止将 large/low/high_noise 的 patch 共享到 protected cases。"
        items.append({
            "scene": case,
            "finding": f"{case} 当前状态：{trend}",
            "severity": severity,
            "reason": " ".join(reason_bits) or analysis.get("summary", "等待更多反馈。"),
            "bad_code": bad_code,
            "patch": patch,
            "source": record.get("ocr_method") or record.get("source"),
            "updated_at": record.get("time"),
            "evidence": c.get("raw_line", ""),
        })
    if not items:
        items.append({
            "scene": "global",
            "finding": "当前反馈未发现明显退化，保持 anchor-preserving。",
            "severity": "info",
            "reason": analysis.get("summary", "解析成功但可比较样本不足。"),
            "bad_code": "# no aggressive patch accepted without no-regression gate",
            "patch": "继续积累第二轮截图；仅允许小步 CONFIG patch。",
            "source": record.get("ocr_method") or record.get("source"),
            "updated_at": record.get("time"),
        })
    return items[:10]


def update_state(record: Dict[str, Any]):
    st = read_json(STATE_PATH, {})
    cases_by_key = {c["case"]: c for c in record.get("cases", [])}
    for row in st.get("case_results", []):
        key = row.get("case")
        if key in cases_by_key:
            c = cases_by_key[key]
            prev = c.get("previous_score")
            row["previous_score"] = prev if prev is not None else row.get("champion")
            row["official_score"] = c.get("score")
            row["champion"] = c.get("score")
            row["candidate"] = c.get("score")
            row["delta_vs_previous"] = c.get("delta")
            row["trend"] = c.get("trend")
            row["assigned"] = c.get("assigned", row.get("assigned"))
            row["time_ms"] = c.get("time_ms")
            hist = row.setdefault("score_history", [])
            hist.append({"time": record["time"], "score": c.get("score"), "delta": c.get("delta")})
            row["score_history"] = hist[-20:]
            dh = row.setdefault("delta_history", [])
            if c.get("delta") is not None:
                dh.append(c.get("delta"))
                row["delta_history"] = dh[-12:]
            if c.get("trend") == "improved":
                row["status"] = "improved"
            elif c.get("trend") == "regressed":
                row["status"] = "watch"
            elif c.get("trend") == "protected-regressed":
                row["status"] = "protected"
    avg = record.get("average_score")
    if avg is not None:
        st.setdefault("champion", {})["score"] = round(float(avg), 2)
        st.setdefault("candidate", {})["score"] = round(float(avg), 2)
        st.setdefault("candidate", {})["status"] = "official-feedback-ingested"
        st.setdefault("candidate", {})["round"] = len(read_jsonl(FEEDBACK_HISTORY, 999)) + 1
    st["score_feedback"] = record
    if record.get("forensics"):
        st["forensics"] = record.get("forensics")
    st.setdefault("score_feedback_history", [])
    st["score_feedback_history"].append({"time": record["time"], "average_score": avg, "avg_delta": record.get("analysis", {}).get("avg_delta"), "completed_cases": record.get("completed_cases")})
    st["score_feedback_history"] = st["score_feedback_history"][-30:]
    # Update Evaluator/Strategy/Reflector desk details.
    for ag in st.get("agents", []):
        if ag.get("id") == "evaluator":
            ag["status"] = "feedback-ingested"
            ag["last_action"] = "已解析线上截图，更新 Cockpit 图表并计算 case delta。"
            ag.setdefault("key_data", {})["latest_official_avg"] = avg
            ag.setdefault("key_data", {})["avg_delta"] = record.get("analysis", {}).get("avg_delta")
        if ag.get("id") == "strategy":
            ag["last_action"] = "根据线上反馈预测下一轮 patch focus：" + "; ".join(record.get("analysis", {}).get("next_focus", [])[:2])
            ag.setdefault("key_data", {})["feedback_focus"] = ", ".join(record.get("analysis", {}).get("stalled_cases", [])[:3]) or "anchor preserve"
        if ag.get("id") == "reflector":
            ag["status"] = "feedback-analyzed"
            ag["last_action"] = "已将线上截图反馈写入 Notes/Handover，并准备 DeepSeek 归因。"
    write_json(STATE_PATH, st)


def write_markdown_logs(record: Dict[str, Any]):
    analysis = record.get("analysis", {})
    cases = record.get("cases", [])
    case_lines = "\n".join(
        f"- {c['case']}: {c.get('previous_score', '--')} → {c['score']}，Δ={c.get('delta', '--')}，{c.get('assigned', '--')}，{c.get('time_ms', '--')}ms，trend={c.get('trend')}"
        for c in cases
    )
    plan_lines = "\n".join(
        f"- `{p.get('target')}` / `{p.get('patch_surface')}`：{p.get('action')} 风险：{p.get('risk')}"
        for p in analysis.get("next_round_plan", [])
    )
    notes_block = f"""

## 线上分数反馈 · {record['time']}

- 来源：{record.get('source')} / OCR：{record.get('ocr_method')}
- 平均分：{analysis.get('previous_average_score', '--')} → {record.get('average_score', '--')}，Δ={analysis.get('avg_delta', '--')}（lower is better）
- 完成算例：{record.get('completed_cases') or '--'}
- 解析摘要：{analysis.get('summary', '')}

### Case 对比
{case_lines}

### 下一轮预测方向
{plan_lines}
"""
    handover_block = f"""

## Handover · 线上截图反馈 {record['time']}

本轮官方反馈已被 Score Feedback Agent 接收。平均分 {record.get('average_score', '--')}，与上一轮相比 Δ={analysis.get('avg_delta', '--')}。

下一轮优先级：
{chr(10).join('- ' + x for x in analysis.get('next_focus', []))}

保护规则：small_seed100、tiny_seed42、scarce_couriers_seed401 仍保持 hard lock；任何自动 patch 必须先通过 no-regression gate。
"""
    NOTES_PATH.parent.mkdir(parents=True, exist_ok=True)
    HANDOVER_PATH.parent.mkdir(parents=True, exist_ok=True)
    with NOTES_PATH.open("a", encoding="utf-8") as f:
        f.write(notes_block)
    with HANDOVER_PATH.open("a", encoding="utf-8") as f:
        f.write(handover_block)


def analyze_text(raw_text: str, source: str, image_name: str | None, notes: str = "") -> Dict[str, Any]:
    FEEDBACK_DIR.mkdir(parents=True, exist_ok=True)
    parsed = parse_feedback_text(raw_text)
    previous_scores, prev_avg, source_label = get_previous_scores()
    if len(parsed.get("session_history", [])) >= 2:
        # Online judge screenshots often include a session history table. Use its second row as the
        # previous official average for the headline delta, while preserving case-level deltas from
        # the dashboard/previous-ingested case table.
        prev_avg = parsed["session_history"][1].get("average_score")
        source_label = "screenshot_session_history"
    local = generate_local_analysis(parsed, previous_scores, prev_avg, source_label)
    record = {
        "time": iso_now(),
        "source": source,
        "image_name": image_name,
        "notes": notes,
        "ocr_method": "text_input",
        "average_score": parsed.get("average_score"),
        "completed_cases": parsed.get("completed_cases"),
        "session_history": parsed.get("session_history", []),
        "cases": local["case_deltas"],
        "raw_text": parsed.get("raw_text", ""),
        "analysis": local,
        "llm_analysis": None,
    }
    llm = llm_refine_analysis(record)
    if llm:
        record["llm_analysis"] = llm
        # Keep local numeric facts, let LLM enrich only prose/plan fields if present.
        if llm.get("summary"):
            record["analysis"]["llm_summary"] = llm.get("summary")
        if llm.get("next_focus"):
            record["analysis"]["next_focus"] = llm.get("next_focus")
        if llm.get("next_round_plan"):
            record["analysis"]["next_round_plan"] = llm.get("next_round_plan")
    record["forensics"] = build_forensics(record)
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    write_json(FEEDBACK_DIR / f"feedback_{stamp}.json", record)
    write_json(FEEDBACK_LATEST, record)
    write_json(NEXT_PLAN_PATH, {"generated_at": record["time"], "from_feedback": record.get("image_name") or source, "analysis": record["analysis"], "cases": record["cases"]})
    append_jsonl(FEEDBACK_HISTORY, {k: v for k, v in record.items() if k != "raw_text"})
    update_state(record)
    write_markdown_logs(record)
    append_event("Score Feedback Agent", "feedback", f"已解析线上反馈：平均分 {record.get('average_score')}，下一轮 focus 已写入 next_round_plan.json。", {"avg_delta": record.get("analysis", {}).get("avg_delta"), "cases": len(record.get("cases", []))})
    return record


def analyze_image(image_path: Path, source: str, notes: str = "") -> Dict[str, Any]:
    raw_text, method = ocr_image(image_path)
    if not raw_text.strip():
        # Still write a transparent failure record.
        record = {
            "time": iso_now(),
            "source": source,
            "image_name": image_path.name,
            "notes": notes,
            "ocr_method": method,
            "average_score": None,
            "completed_cases": None,
            "cases": [],
            "raw_text": raw_text,
            "analysis": {"summary": f"未能从截图中自动识别分数：{method}", "next_focus": ["请在分数反馈模块粘贴 OCR 文本或配置 Qwen OCR / pytesseract，或直接粘贴官方文本。"], "next_round_plan": []},
            "forensics": [{"scene": "ocr", "finding": "OCR 未返回可解析文本", "severity": "warning", "reason": f"{method}", "bad_code": "ocr_engine = pytesseract_optional", "patch": "配置 QWEN_API_KEY / QWEN_BASE_URL / QWEN_OCR_MODEL 后重试；或粘贴 OCR 文本。", "updated_at": iso_now()}],
        }
        write_json(FEEDBACK_LATEST, record)
        append_jsonl(FEEDBACK_HISTORY, record)
        append_event("Score Feedback Agent", "feedback-failed", record["analysis"]["summary"])
        return record
    record = analyze_text(raw_text, source, image_path.name, notes)
    record["ocr_method"] = method
    write_json(FEEDBACK_LATEST, record)
    return record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default="", help="Path to uploaded online score screenshot")
    parser.add_argument("--text-file", default="", help="Path to OCR/plain text copied from online score page")
    parser.add_argument("--raw-text", default="", help="Raw text to parse")
    parser.add_argument("--source", default="cli")
    parser.add_argument("--notes", default="")
    args = parser.parse_args()

    if args.image:
        record = analyze_image(Path(args.image), args.source, args.notes)
    elif args.text_file:
        raw = Path(args.text_file).read_text(encoding="utf-8", errors="ignore")
        record = analyze_text(raw, args.source, Path(args.text_file).name, args.notes)
    elif args.raw_text:
        record = analyze_text(args.raw_text, args.source, None, args.notes)
    else:
        print(json.dumps({"ok": False, "error": "missing --image or --text-file or --raw-text"}, ensure_ascii=False))
        return 2
    print(json.dumps({"ok": bool(record.get("cases")), "record": record}, ensure_ascii=False, indent=2))
    return 0 if record.get("cases") else 1


if __name__ == "__main__":
    raise SystemExit(main())
