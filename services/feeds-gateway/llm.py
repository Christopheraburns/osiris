"""LLM client for grounded synthesis — Ollama OR an OpenAI-compatible endpoint.

Provider is selected by env:
  LLM_PROVIDER = "openai"  → an OpenAI-compatible chat endpoint (Cloudera AI
                             Inference Service, NVIDIA NIM, vLLM, …). No local GPU
                             needed — the model runs on the served endpoint.
  LLM_PROVIDER = "ollama"  → local Ollama /api/chat (the GPU-app path).
Default: "openai" when OPENAI_BASE_URL is set, else "ollama".

The public interface is unchanged (chat_stream, prompt_stream, available,
OLLAMA_MODEL, OLLAMA_URL) so intel.py / patternoflife.py / app.py need no edits.

Config for the OpenAI-compatible path (e.g. Cloudera AI Inference serving
Llama-3.3-70B-Instruct):
    LLM_PROVIDER   = openai
    OPENAI_BASE_URL= https://<endpoint-host>/.../v1     (the base ending in /v1)
    OPENAI_API_KEY = <bearer token / CDP JWT>
    OPENAI_MODEL   = <exact model id the endpoint expects>  (see GET {base}/models)
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import AsyncIterator

import httpx

log = logging.getLogger("feeds-gateway.llm")

# ── Ollama (local GPU app) ──
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://osiris-ollama:11434")
_OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "mistral-small3.2")

# ── OpenAI-compatible endpoint (Cloudera AI Inference / NIM / vLLM) ──
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "").rstrip("/")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "meta/llama-3.3-70b-instruct")

PROVIDER = os.environ.get("LLM_PROVIDER", "openai" if OPENAI_BASE_URL else "ollama").lower()
REQUEST_TIMEOUT = float(os.environ.get("LLM_TIMEOUT", os.environ.get("OLLAMA_TIMEOUT", "120")))

# Back-compat attributes read by the health endpoints and intel modules — point
# them at whichever provider is active so /llm/health reports the real model.
OLLAMA_MODEL = OPENAI_MODEL if PROVIDER == "openai" else _OLLAMA_MODEL
if PROVIDER == "openai":
    OLLAMA_URL = OPENAI_BASE_URL


def _chat_url() -> str:
    b = OPENAI_BASE_URL
    if b.endswith("/chat/completions"):
        return b
    if b.endswith("/v1"):
        return b + "/chat/completions"
    return b + "/v1/chat/completions"


def _openai_headers() -> dict:
    h = {"Content-Type": "application/json"}
    if OPENAI_API_KEY:
        h["Authorization"] = f"Bearer {OPENAI_API_KEY}"
    return h


async def available() -> bool:
    """True when the configured LLM provider is reachable."""
    try:
        if PROVIDER == "openai":
            if not OPENAI_BASE_URL:
                return False
            models_url = OPENAI_BASE_URL + ("/models" if OPENAI_BASE_URL.endswith("/v1") else "/v1/models")
            async with httpx.AsyncClient(timeout=8) as client:
                r = await client.get(models_url, headers=_openai_headers())
                # Any HTTP response means the endpoint is reachable (401/403 = up but
                # /models not exposed/authed — still fine for chat).
                return r.status_code < 500
        # ollama
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{OLLAMA_URL}/api/tags")
            if r.status_code != 200:
                return False
            names = {m.get("name", "") for m in (r.json().get("models") or []) if m.get("name")}
            base = OLLAMA_MODEL.split(":")[0]
            return any(n == OLLAMA_MODEL or n.split(":")[0] == base for n in names)
    except Exception as exc:  # noqa: BLE001
        log.warning("llm unavailable: %s", exc)
        return False


async def _chat_stream_openai(system: str, user: str) -> AsyncIterator[str]:
    payload = {
        "model": OPENAI_MODEL,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "stream": True,
        "temperature": 0.2,
    }
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            async with client.stream("POST", _chat_url(), json=payload, headers=_openai_headers()) as resp:
                if resp.status_code != 200:
                    body = (await resp.aread()).decode("utf-8", "ignore")
                    yield f"[LLM error {resp.status_code}: {body[:200]}]"
                    return
                async for line in resp.aiter_lines():
                    line = line.strip()
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[len("data:"):].strip()
                    if data == "[DONE]":
                        break
                    try:
                        obj = json.loads(data)
                    except ValueError:
                        continue
                    choices = obj.get("choices") or [{}]
                    delta = (choices[0].get("delta") or {}).get("content")
                    if delta:
                        yield delta
    except Exception as exc:  # noqa: BLE001
        log.warning("openai chat failed: %s", exc)
        yield f"[LLM unavailable: {exc}]"


async def _chat_stream_ollama(system: str, user: str) -> AsyncIterator[str]:
    payload = {
        "model": _OLLAMA_MODEL,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
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


async def chat_stream(system: str, user: str) -> AsyncIterator[str]:
    """Yield answer tokens as they are generated (provider-agnostic)."""
    if PROVIDER == "openai":
        async for tok in _chat_stream_openai(system, user):
            yield tok
    else:
        async for tok in _chat_stream_ollama(system, user):
            yield tok


def _ndjson(obj: dict) -> bytes:
    return (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")


async def prompt_stream(prompt: str, system: str | None = None) -> AsyncIterator[bytes]:
    """Stream a raw user prompt with timing metrics (NDJSON events), any provider."""
    started = time.monotonic()
    first_at: float | None = None
    n = 0
    yield _ndjson({"type": "meta", "model": OLLAMA_MODEL, "provider": PROVIDER})
    sys_prompt = (system or "").strip() or "You are a helpful assistant."
    async for tok in chat_stream(sys_prompt, prompt.strip()):
        if first_at is None:
            first_at = time.monotonic()
        n += 1
        yield _ndjson({"type": "token", "text": tok})
    total_ms = int((time.monotonic() - started) * 1000)
    ttft_ms = int((first_at - started) * 1000) if first_at else 0
    gen_s = (time.monotonic() - first_at) if first_at else 0
    tps = round(n / gen_s, 1) if gen_s > 0 else 0
    yield _ndjson({"type": "done", "ttft_ms": ttft_ms, "token_count": n, "tokens_per_sec": tps, "total_ms": total_ms})
