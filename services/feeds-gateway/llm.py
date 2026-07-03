"""Local LLM client (Ollama) for grounded synthesis.

Uses the Ollama native /api/chat endpoint with streaming. Everything runs
locally (no cloud). The caller supplies a grounded system prompt + the retrieved
facts; this module only handles transport and token streaming.

The ``prompt_stream`` helper exposes a simple NDJSON protocol for the LLM test UI:
  {"type":"meta","model":"..."}
  {"type":"token","text":"..."}   (repeated)
  {"type":"done","ttft_ms":N,"token_count":N,"tokens_per_sec":N,"total_ms":N}
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import AsyncIterator

import httpx

log = logging.getLogger("feeds-gateway.llm")

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://osiris-ollama:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "mistral-small3.2")
REQUEST_TIMEOUT = float(os.environ.get("OLLAMA_TIMEOUT", "120"))


async def available() -> bool:
    """True when Ollama is up and the configured model is pulled."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{OLLAMA_URL}/api/tags")
            if r.status_code != 200:
                return False
            models = r.json().get("models") or []
            if not models:
                return False
            names = {m.get("name", "") for m in models if m.get("name")}
            target = OLLAMA_MODEL.removesuffix(":latest")
            base = target.split(":")[0]
            return any(
                n == OLLAMA_MODEL
                or n == target
                or n.startswith(f"{base}:")
                or n.split(":")[0] == base
                for n in names
            )
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


def _ndjson(obj: dict) -> bytes:
    return (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")


async def prompt_stream(prompt: str, system: str | None = None) -> AsyncIterator[bytes]:
    """Stream a raw user prompt to Ollama with timing metrics (NDJSON events)."""
    started = time.monotonic()
    first_token_at: float | None = None
    chunk_count = 0
    eval_count = 0
    eval_duration_ns = 0

    yield _ndjson({"type": "meta", "model": OLLAMA_MODEL, "ollama_url": OLLAMA_URL})

    messages: list[dict[str, str]] = []
    if system and system.strip():
        messages.append({"role": "system", "content": system.strip()})
    messages.append({"role": "user", "content": prompt.strip()})

    payload = {
        "model": OLLAMA_MODEL,
        "messages": messages,
        "stream": True,
        "options": {"temperature": 0.7},
    }

    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            async with client.stream("POST", f"{OLLAMA_URL}/api/chat", json=payload) as resp:
                if resp.status_code != 200:
                    body = (await resp.aread()).decode("utf-8", "ignore")
                    err = f"[LLM error {resp.status_code}: {body[:200]}]"
                    yield _ndjson({"type": "token", "text": err})
                    yield _ndjson({
                        "type": "done",
                        "ttft_ms": 0,
                        "token_count": 0,
                        "tokens_per_sec": 0,
                        "total_ms": int((time.monotonic() - started) * 1000),
                        "error": True,
                    })
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
                        if first_token_at is None:
                            first_token_at = time.monotonic()
                        chunk_count += 1
                        yield _ndjson({"type": "token", "text": chunk})

                    if obj.get("done"):
                        eval_count = int(obj.get("eval_count") or chunk_count)
                        eval_duration_ns = int(obj.get("eval_duration") or 0)
                        break
    except Exception as exc:  # noqa: BLE001
        log.warning("ollama prompt_stream failed: %s", exc)
        yield _ndjson({"type": "token", "text": f"[LLM unavailable: {exc}]"})
        yield _ndjson({
            "type": "done",
            "ttft_ms": 0,
            "token_count": 0,
            "tokens_per_sec": 0,
            "total_ms": int((time.monotonic() - started) * 1000),
            "error": True,
        })
        return

    total_ms = int((time.monotonic() - started) * 1000)
    ttft_ms = int((first_token_at - started) * 1000) if first_token_at else 0
    token_count = eval_count or chunk_count
    if eval_duration_ns > 0:
        tokens_per_sec = round(token_count / (eval_duration_ns / 1e9), 1)
    elif first_token_at is not None:
        gen_ms = (time.monotonic() - first_token_at) * 1000
        tokens_per_sec = round(token_count / (gen_ms / 1000), 1) if gen_ms > 0 else 0
    else:
        tokens_per_sec = 0

    yield _ndjson({
        "type": "done",
        "ttft_ms": ttft_ms,
        "token_count": token_count,
        "tokens_per_sec": tokens_per_sec,
        "total_ms": total_ms,
    })
