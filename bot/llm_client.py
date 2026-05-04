"""ローカル LLM (rkllama / Ollama 互換) クライアント。

`LLM_HOST` の `/api/chat` エンドポイントを呼び出し、 messages 形式で応答を得る。
未設定 (LLM_HOST / LLM_MODEL のいずれか空) なら is_enabled() が False を返し、
呼び出し側で機能を skip する想定。
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import requests

logger = logging.getLogger(__name__)


def _env(key: str, default: str = "") -> str:
    return (os.environ.get(key) or default).strip()


def is_enabled() -> bool:
    return bool(_env("LLM_HOST")) and bool(_env("LLM_MODEL"))


def _payload(system: str, user: str) -> dict[str, Any]:
    return {
        "model": _env("LLM_MODEL"),
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
        "options": {
            "num_ctx": int(_env("LLM_NUM_CTX", "4096")),
            "temperature": float(_env("LLM_TEMPERATURE", "0.3")),
        },
    }


def _chat_sync(system: str, user: str) -> str:
    host = _env("LLM_HOST").rstrip("/")
    timeout = (
        int(_env("LLM_TIMEOUT_CONNECT", "10")),
        int(_env("LLM_TIMEOUT_READ", "300")),
    )
    resp = requests.post(
        f"{host}/api/chat",
        json=_payload(system, user),
        timeout=timeout,
    )
    if not resp.ok:
        logger.error(
            "LLM API error: status=%s body=%s",
            resp.status_code, (resp.text or "")[:500],
        )
        resp.raise_for_status()
    data = resp.json()
    return (data.get("message") or {}).get("content", "") or ""


async def chat(system: str, user: str) -> str:
    """system + user の単発チャット。 ブロッキング requests を to_thread で逃がす。"""
    return await asyncio.to_thread(_chat_sync, system, user)
