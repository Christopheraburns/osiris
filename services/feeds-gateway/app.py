"""OSIRIS Feeds Gateway — FastAPI ASGI service.

Consumes the NiFi -> Kafka entity stream and serves clean, feed-shaped JSON that
the OSIRIS server proxies to in SECURED CONNECTION mode. This is also the
intended home for the streaming intelligence layer (GraphRAG / LLM / MCP), which
is why it is built on FastAPI/uvicorn (async, streaming, typed) rather than the
recorder's stdlib control plane.

Run with a single worker so the in-memory snapshot is coherent:
    uvicorn app:app --host 0.0.0.0 --port 8091 --workers 1
"""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

import db
import graph
import history
import intel
import llm
from consumer import FeedStore, consume_loop
from registry import FEEDS, migrated_feeds

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("feeds-gateway")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start the durable log writer (bootstraps osiris_logs.app_logs) off-thread.
    await asyncio.to_thread(db.start)
    store = FeedStore()
    stop = asyncio.Event()
    task = asyncio.create_task(consume_loop(store, stop), name="kafka-consumer")
    app.state.store = store
    log.info("feeds-gateway started; migrated feeds=%s", migrated_feeds())
    db.log_row("info", "gateway", "feeds-gateway started", data={"migrated": migrated_feeds()})

    # Warm the TimeTravel/Hive path in the background: heats the Tez session and
    # pre-caches bounds so the operator's first click isn't stuck on cold-start.
    if history.configured():
        async def _warm_history() -> None:
            try:
                b = await asyncio.to_thread(history.bounds, True)
                log.info("history warm-up ok: %s", b)
            except Exception as exc:  # noqa: BLE001
                log.warning("history warm-up failed (retries on first request): %s", exc)
        asyncio.create_task(_warm_history(), name="history-warmup")

    try:
        yield
    finally:
        stop.set()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        graph.close()
        log.info("feeds-gateway stopped")


app = FastAPI(title="OSIRIS Feeds Gateway", version="0.1.0", lifespan=lifespan)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@app.get("/health")
async def health() -> JSONResponse:
    store: FeedStore = app.state.store
    stats = await store.stats()
    return JSONResponse(
        {
            "status": "ok",
            "available": True,
            "feeds": migrated_feeds(),
            "kafka": stats,
            "timestamp": _now_iso(),
        }
    )


@app.get("/feeds")
async def feeds() -> JSONResponse:
    return JSONResponse({"migrated": migrated_feeds(), "timestamp": _now_iso()})


# ── GraphRAG intelligence layer ──────────────────────────────────────────────

@app.get("/intel/health")
async def intel_health() -> JSONResponse:
    return JSONResponse(
        {
            "status": "ok",
            "neo4j": graph.available(),
            "ollama": await llm.available(),
            "model": llm.OLLAMA_MODEL,
            "timestamp": _now_iso(),
        }
    )


@app.get("/llm/health")
async def llm_health() -> JSONResponse:
    return JSONResponse(
        {
            "status": "ok",
            "ollama": await llm.available(),
            "model": llm.OLLAMA_MODEL,
            "ollama_url": llm.OLLAMA_URL,
            "timestamp": _now_iso(),
        }
    )


@app.post("/llm/chat")
async def llm_chat(request: Request) -> StreamingResponse:
    """Raw prompt test endpoint for the LLM UI. Streams NDJSON token events."""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="invalid json")

    prompt = (body.get("prompt") or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="prompt is required")
    system = body.get("system")

    return StreamingResponse(
        llm.prompt_stream(prompt, system),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@app.post("/intel/ask")
async def intel_ask(request: Request) -> StreamingResponse:
    """GraphRAG Q&A for a selected entity. Streams NDJSON events.

    Body: { entity: {type, icao24, callsign, registration, model}, tier?, question? }
    """
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="invalid json")

    entity = body.get("entity") or {}
    if not isinstance(entity, dict):
        raise HTTPException(status_code=400, detail="entity must be an object")
    try:
        tier = int(body.get("tier", 1))
    except (TypeError, ValueError):
        tier = 1
    question = body.get("question")

    return StreamingResponse(
        intel.ask_stream(entity, tier, question),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


# ── TimeTravel: historical replay read from the Iceberg lake (via Hive) ───────

@app.get("/history/bounds")
async def history_bounds() -> JSONResponse:
    """Time extent + row count of the lake -- drives the replay scrubber range."""
    if not history.configured():
        raise HTTPException(status_code=503, detail="history not configured (set HIVE_* env vars)")
    try:
        data = await asyncio.to_thread(history.bounds)
    except Exception as exc:  # noqa: BLE001
        log.warning("history bounds failed: %s", exc)
        raise HTTPException(status_code=503, detail=f"history unavailable: {exc}")
    return JSONResponse({**data, "timestamp": _now_iso()})


@app.get("/history")
async def history_window(start: int, end: int, types: str = "", limit: int = 50000) -> JSONResponse:
    """Positional events in [start, end] epoch-ms, ordered by time -- replay frames.

    ``types`` is a comma-separated asset_type filter (e.g. FLIGHT,VESSEL).
    """
    if not history.configured():
        raise HTTPException(status_code=503, detail="history not configured (set HIVE_* env vars)")
    tlist = [t for t in types.split(",") if t]
    try:
        events = await asyncio.to_thread(history.window, start, end, tlist or None, limit)
    except Exception as exc:  # noqa: BLE001
        log.warning("history window failed: %s", exc)
        raise HTTPException(status_code=503, detail=f"history unavailable: {exc}")
    return JSONResponse({"events": events, "count": len(events), "timestamp": _now_iso()})


@app.get("/feeds/{feed}")
async def feed(feed: str) -> JSONResponse:
    spec = FEEDS.get(feed)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"feed '{feed}' is not migrated")
    store: FeedStore = app.state.store
    items = await store.items(feed)
    provenance = await store.get_provenance(feed)
    return JSONResponse(
        {
            spec.response_key: items,
            "total": len(items),
            "source": "streaming-lakehouse",
            "provenance": {
                "ingest_run_id": provenance.get("ingest_run_id"),
                "source": provenance.get("source"),
                "captured_at": provenance.get("captured_at"),
                "count": len(items),
            },
            "timestamp": _now_iso(),
        }
    )
