#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stdlib DeepSeek client for SwarmCell / OmniCell.

DeepSeek is the only LLM provider for strategy reflection, error attribution,
and autonomous patch planning. Qwen is intentionally reserved for OCR only.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "config"
DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "DeepSeek-V4-pro"


def load_local_env() -> None:
    for name in (".env.local", ".env"):
        path = CONFIG_DIR / name
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


@dataclass
class DeepSeekResult:
    ok: bool
    content: str
    model: str
    base_url: str
    error: str = ""
    raw: Optional[Dict[str, Any]] = None


def env_config() -> Dict[str, str]:
    load_local_env()
    return {
        "api_key": os.getenv("DEEPSEEK_API_KEY", "").strip(),
        "base_url": os.getenv("DEEPSEEK_BASE_URL", DEFAULT_BASE_URL).strip().rstrip("/"),
        "model": os.getenv("DEEPSEEK_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL,
    }


def chat_json(messages: List[Dict[str, str]], *, max_tokens: int = 4096, timeout: int = 90) -> DeepSeekResult:
    cfg = env_config()
    if not cfg["api_key"]:
        return DeepSeekResult(
            ok=False,
            content="",
            model=cfg["model"],
            base_url=cfg["base_url"],
            error="DEEPSEEK_API_KEY is not set; autonomous_patch_agent will use local safe-patch fallback.",
        )

    payload: Dict[str, Any] = {
        "model": cfg["model"],
        "messages": messages,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
        # Non-thinking mode is easier to constrain to a strict JSON patch plan.
        "thinking": {"type": "disabled"},
    }
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
        return DeepSeekResult(ok=True, content=content, model=cfg["model"], base_url=cfg["base_url"], raw=raw)
    except urllib.error.HTTPError as e:
        try:
            err = e.read().decode("utf-8", errors="ignore")[-2000:]
        except Exception:
            err = str(e)
        return DeepSeekResult(ok=False, content="", model=cfg["model"], base_url=cfg["base_url"], error=f"HTTPError {e.code}: {err}")
    except Exception as e:
        return DeepSeekResult(ok=False, content="", model=cfg["model"], base_url=cfg["base_url"], error=f"{type(e).__name__}: {e}")


def parse_json_object(text: str) -> Dict[str, Any]:
    text = (text or "").strip()
    if not text:
        raise ValueError("empty model content")
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end < start:
            raise
        obj = json.loads(text[start:end + 1])
    if not isinstance(obj, dict):
        raise ValueError("model content is not a JSON object")
    return obj
