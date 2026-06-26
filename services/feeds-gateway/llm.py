"""Local LLM client (Ollama) for grounded synthesis.

Uses the Ollama native /api/chat endpoint with streaming. Everything runs
locally (no cloud). The caller supplies a grounded system prompt + the retrieved
facts; this module only handles transport and token streaming.
"""
from __future__ import annotations

import json
import logging
import os
from typing import AsyncIterator

import httpx

log = logging.getLogger("feeds-gateway.llm")

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://ollama:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "mistral-small3.2")
REQUEST_TIMEOUT = float(os.environ.get("OLLAMA_TIMEOUT", "120"))


async def available() -> bool:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{OLLAMA_URL}/api/tags")
            return r.status_code == 200
    except Exception as exc:  # noqa: BLE001
        log.warning("ollama unavailable: %s", exc)
        return False


async def chat_stream(system: str, user: str) -> AsyncIterator[str]:
    """Yield answer tokens from Ollama as they are generated."""
    payload = {
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": True,
        "options": {"temperature": 0.2},
    }
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            async with client.stream("POST", f"{OLLAMA_URL}/api/chat", json=payload) as resp:
                if resp.status_code != 200:
                    body = (await resp.aread()).decode("utf-8", "ignore")
                    yield f"[LLM error {resp.status_code}: {body[:200]}]"
                    return
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        obj = json.loads(line)
                    except ValueError:
                        continue
                    chunk = (obj.get("message") or {}).get("content")
                    if chunk:
                        yield chunk
                    if obj.get("done"):
                        break
    except Exception as exc:  # noqa: BLE001
        log.warning("ollama chat failed: %s", exc)
        yield f"[LLM unavailable: {exc}]"
