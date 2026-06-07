"""DeepSeek V4 LLM client via OpenAI-compatible API.

Provides a unified interface for calling DeepSeek for:
- Training analysis and strategy generation
- Failure pattern analysis
- Case generation suggestions
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

_client: OpenAI | None = None


def get_client() -> OpenAI:
    """Get or create the singleton OpenAI client for DeepSeek."""
    global _client
    if _client is None:
        _client = OpenAI(
            base_url=DEEPSEEK_BASE_URL,
            api_key=DEEPSEEK_API_KEY,
            timeout=120.0,
            max_retries=2,
        )
    return _client


def chat(
    prompt: str,
    system: str | None = None,
    temperature: float = 0.3,
    max_tokens: int = 4096,
    model: str | None = None,
) -> str:
    """Send a chat completion request and return the text response."""
    client = get_client()
    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    for attempt in range(3):
        try:
            response = client.chat.completions.create(
                model=model or DEEPSEEK_MODEL,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return response.choices[0].message.content or ""
        except Exception as exc:
            if attempt == 2:
                raise
            wait = 2 ** attempt * 2
            print(f"[llm_client] attempt {attempt+1} failed: {exc}, retrying in {wait}s")
            time.sleep(wait)
    return ""


def chat_json(
    prompt: str,
    schema: dict[str, Any] | None = None,
    system: str | None = None,
    temperature: float = 0.2,
    max_tokens: int = 4096,
    model: str | None = None,
) -> dict[str, Any] | list[Any]:
    """Send a chat request expecting JSON output."""
    json_instruction = "\n\nReturn pure JSON only. Do not include markdown code fences."
    if schema:
        json_instruction += f"\n\nExpected JSON structure:\n```json\n{json.dumps(schema, ensure_ascii=False, indent=2)}\n```"

    full_prompt = prompt + json_instruction
    text = chat(full_prompt, system=system, temperature=temperature, max_tokens=max_tokens, model=model)

    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[-1].strip().startswith("```"):
            lines = lines[1:-1]
        else:
            lines = lines[1:]
        text = "\n".join(lines)

    return json.loads(text)


def chat_with_retry(
    prompt: str,
    system: str | None = None,
    temperature: float = 0.3,
    max_retries: int = 3,
    fallback: str = "",
) -> str:
    """Chat with explicit retry control and fallback value."""
    for attempt in range(max_retries):
        try:
            return chat(prompt, system=system, temperature=temperature)
        except Exception as exc:
            print(f"[llm_client] chat attempt {attempt+1}/{max_retries} failed: {exc}")
            if attempt == max_retries - 1:
                return fallback
            time.sleep(2 ** attempt)
    return fallback


def is_available() -> bool:
    """Check if the API is reachable."""
    try:
        result = chat("Reply with just the word 'ok'.", max_tokens=10, temperature=0.0)
        return "ok" in result.lower()
    except Exception:
        return False
