#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Qwen OpenAI-compatible OCR client for SwarmCell/OmniCell Studio.

Qwen is reserved for score-screenshot OCR only. All LLM attribution, dialogue,
and autonomous iteration are routed to DeepSeek-V4-pro.
"""
from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config"

DEFAULT_BASE_URL = "https://ws-bmzspe69czs7znm9.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1"
DEFAULT_OCR_MODEL = "qwen-vl-ocr"


def load_local_env() -> None:
    """Load config/.env.local or config/.env into os.environ if variables are absent."""
    for name in (".env.local", ".env"):
        path = CONFIG / name
        if not path.exists():
            continue
        for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v


def qwen_env() -> Dict[str, str]:
    load_local_env()
    return {
        "api_key": os.getenv("QWEN_API_KEY", "").strip(),
        "base_url": os.getenv("QWEN_BASE_URL", DEFAULT_BASE_URL).strip().rstrip("/"),
        "ocr_model": os.getenv("QWEN_OCR_MODEL", DEFAULT_OCR_MODEL).strip() or DEFAULT_OCR_MODEL,
    }


def qwen_configured() -> bool:
    return bool(qwen_env().get("api_key"))


@dataclass
class QwenResult:
    ok: bool
    content: str
    model: str
    base_url: str
    error: str = ""
    raw: Optional[Dict[str, Any]] = None


def _post_chat(payload: Dict[str, Any], *, timeout: int = 90) -> QwenResult:
    cfg = qwen_env()
    if not cfg["api_key"]:
        return QwenResult(False, "", payload.get("model", cfg["ocr_model"]), cfg["base_url"], "QWEN_API_KEY is not set")
    url = cfg["base_url"] + "/chat/completions"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + cfg["api_key"]},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = json.loads(resp.read().decode("utf-8", errors="ignore"))
        content = raw.get("choices", [{}])[0].get("message", {}).get("content", "")
        return QwenResult(True, content, payload.get("model", cfg["ocr_model"]), cfg["base_url"], raw=raw)
    except urllib.error.HTTPError as e:
        try:
            err = e.read().decode("utf-8", errors="ignore")[-3000:]
        except Exception:
            err = str(e)
        return QwenResult(False, "", payload.get("model", cfg["ocr_model"]), cfg["base_url"], f"HTTPError {e.code}: {err}")
    except Exception as e:
        return QwenResult(False, "", payload.get("model", cfg["ocr_model"]), cfg["base_url"], f"{type(e).__name__}: {e}")


def image_to_data_url(image_path: Path) -> str:
    raw = image_path.read_bytes()
    mime = mimetypes.guess_type(str(image_path))[0] or "image/png"
    return "data:%s;base64,%s" % (mime, base64.b64encode(raw).decode("ascii"))



def ocr_image(image_path: Path, *, timeout: int = 120) -> QwenResult:
    cfg = qwen_env()
    prompt = (
        "请对这张线上评测截图做高精度 OCR。只输出图片中可见的原始文本，保留换行。"
        "重点识别 case 名称、score/分数、coverage/覆盖率、time/ms、平均分、提交历史。"
        "不要解释，不要总结，不要改写。"
    )
    data_url = image_to_data_url(image_path)
    payload: Dict[str, Any] = {
        "model": cfg["ocr_model"],
        "messages": [
            {"role": "system", "content": "你是严谨的比赛分数截图 OCR 引擎，只返回可见文本。"},
            {"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": data_url}},
            ]},
        ],
        "max_tokens": 4096,
    }
    return _post_chat(payload, timeout=timeout)


def parse_json_object(text: str) -> Dict[str, Any]:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S)
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end < start:
            raise
        obj = json.loads(text[start:end + 1])
    if not isinstance(obj, dict):
        raise ValueError("content is not a JSON object")
    return obj


def config_status() -> Dict[str, Any]:
    cfg = qwen_env()
    return {
        "configured": bool(cfg["api_key"]),
        "base_url": cfg["base_url"],
        "ocr_model": cfg["ocr_model"],
        "role": "ocr_only",
        "api_key_source": "env_or_config_env_local" if cfg["api_key"] else "missing",
    }
