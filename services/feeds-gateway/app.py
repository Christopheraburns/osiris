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
