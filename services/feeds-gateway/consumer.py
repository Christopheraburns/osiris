"""Kafka -> in-memory feed store.

A single ``aiokafka`` consumer (run as one uvicorn worker, started from the
FastAPI lifespan) subscribes to the canonical ``osiris.entities`` topic, reshapes
each record into feed items via the registry, and keeps a TTL keyed snapshot per
feed. The HTTP layer reads the latest snapshot; it never touches Kafka directly.

A coherent in-memory snapshot requires exactly one consumer instance, hence one
uvicorn worker. Scaling out would require a shared store (Redis) -- out of scope.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any

from aiokafka import AIOKafkaConsumer
from aiokafka.errors import KafkaConnectionError

import db
from registry import ENTITY_TYPE_TO_FEED, FEEDS, RESPONSE_KEY_TO_FEED

log = logging.getLogger("feeds-gateway.consumer")

KAFKA_BROKERS = os.environ.get("KAFKA_BROKERS", "osiris-kafka:9092")
ENTITIES_TOPIC = os.environ.get("OSIRIS_ENTITIES_TOPIC", "osiris.entities")
FEED_TTL_SECONDS = float(os.environ.get("FEED_TTL_SECONDS", "3600"))
GROUP_ID = os.environ.get("KAFKA_GROUP_ID", "osiris-feeds-gateway")


class FeedStore:
    """TTL keyed snapshot: feed -> { entity_id: (item, monotonic_ts) }."""

    LOG_THROTTLE_SECONDS = 20.0

    def __init__(self, ttl_seconds: float = FEED_TTL_SECONDS) -> None:
        self._ttl = ttl_seconds
        self._store: dict[str, dict[str, tuple[dict, float]]] = {
            name: {} for name in FEEDS
        }
        self._lock = asyncio.Lock()
        self._messages = 0
        self._last_message_at: float | None = None
        # Provenance + throttled-logging state (per feed).
        self._provenance: dict[str, dict] = {}
        self._batch_count: dict[str, int] = {}
        self._last_log_at: dict[str, float] = {}

    async def put(self, feed: str, entity_id: str, item: dict) -> None:
        async with self._lock:
            self._store.setdefault(feed, {})[entity_id] = (item, time.monotonic())

    async def set_provenance(self, feed: str, prov: dict) -> None:
        async with self._lock:
            self._provenance[feed] = prov

    async def get_provenance(self, feed: str) -> dict:
        async with self._lock:
            return dict(self._provenance.get(feed, {}))

    async def note_ingest(self, feed: str) -> tuple[bool, int]:
        """Increment the per-feed ingest counter; return (should_log, count)."""
        async with self._lock:
            self._batch_count[feed] = self._batch_count.get(feed, 0) + 1
            now = time.monotonic()
            last = self._last_log_at.get(feed, 0.0)
            if now - last >= self.LOG_THROTTLE_SECONDS:
                count = self._batch_count[feed]
                self._batch_count[feed] = 0
                self._last_log_at[feed] = now
                return True, count
            return False, self._batch_count[feed]

    async def items(self, feed: str) -> list[dict]:
        cutoff = time.monotonic() - self._ttl
        async with self._lock:
            bucket = self._store.get(feed, {})
            fresh = {k: v for k, v in bucket.items() if v[1] >= cutoff}
            self._store[feed] = fresh  # opportunistic eviction
            return [item for item, _ in fresh.values()]

    async def mark_message(self) -> None:
        async with self._lock:
            self._messages += 1
            self._last_message_at = time.time()

    async def stats(self) -> dict:
        async with self._lock:
            return {
                "messages": self._messages,
                "last_message_at": self._last_message_at,
                "counts": {feed: len(bucket) for feed, bucket in self._store.items()},
            }


def _records_from_value(value: Any) -> list[dict]:
    """Flatten a Kafka message value into a list of candidate records."""
    if isinstance(value, list):
        return [r for r in value if isinstance(r, dict)]
    if isinstance(value, dict):
        return [value]
    return []


def _provenance_of(rec: dict) -> dict:
    """Extract provenance from the canonical envelope (or nested entity.source)."""
    entity = rec.get("entity") if isinstance(rec.get("entity"), dict) else {}
    inner = entity.get("source") if isinstance(entity.get("source"), dict) else {}
    return {
        "ingest_run_id": rec.get("ingest_run_id"),
        "source": rec.get("source") if isinstance(rec.get("source"), str) else inner.get("feed"),
        "captured_at": rec.get("captured_at"),
    }


async def _record_provenance(store: "FeedStore", feed: str, rec: dict) -> None:
    """Save provenance and emit a throttled, durable ingest log row."""
    prov = _provenance_of(rec)
    await store.set_provenance(feed, prov)
    should_log, count = await store.note_ingest(feed)
    if should_log:
        db.log_row(
            "info",
            feed,
            f"ingested {count} {feed} entities from streaming lakehouse",
            ingest_run_id=prov.get("ingest_run_id"),
            data={"feed": feed, "count": count, "source": prov.get("source"),
                  "captured_at": prov.get("captured_at")},
        )


def _feed_for(rec: dict) -> str | None:
    """Resolve which migrated feed a record belongs to.

    Handles the canonical NiFi envelope ({"source", "entity": {...}}), a bare
    entity, and routes by entityType or the source/feed name.
    """
    entity = rec.get("entity") if isinstance(rec.get("entity"), dict) else rec
    entity_type = entity.get("entityType") or entity.get("entity_type")
    feed = ENTITY_TYPE_TO_FEED.get(entity_type) if entity_type else None
    if feed:
        return feed
    # Fall back to the source/feed name on the envelope or entity.source.feed.
    source = rec.get("source")
    if isinstance(source, str) and source in FEEDS:
        return source
    inner = entity.get("source") if isinstance(entity.get("source"), dict) else {}
    feed_name = inner.get("feed")
    if isinstance(feed_name, str) and feed_name in FEEDS:
        return feed_name
    return None


async def _ingest_record(store: FeedStore, rec: dict, counter: list[int]) -> None:
    """Route one record to the right feed and store its reshaped item(s)."""
    # Case 1: canonical entity/envelope routed by entityType or source.
    feed_name = _feed_for(rec)
    if feed_name:
        spec = FEEDS[feed_name]
        item = spec.reshape(rec)
        entity_id = str(item.get("id") or counter[0])
        counter[0] += 1
        await store.put(feed_name, entity_id, item)
        await _record_provenance(store, feed_name, rec)
        return

    # Case 2: feed-shaped envelope, e.g. {"earthquakes": [ {...}, ... ]}.
    for key, fname in RESPONSE_KEY_TO_FEED.items():
        bucket = rec.get(key)
        if isinstance(bucket, list):
            spec = FEEDS[fname]
            for raw in bucket:
                if not isinstance(raw, dict):
                    continue
                item = spec.reshape(raw)
                entity_id = str(item.get("id") or counter[0])
                counter[0] += 1
                await store.put(fname, entity_id, item)
            await _record_provenance(store, fname, rec)
            return


async def consume_loop(store: FeedStore, stop: asyncio.Event) -> None:
    """Connect (with retry) and consume until ``stop`` is set."""
    while not stop.is_set():
        consumer = AIOKafkaConsumer(
            ENTITIES_TOPIC,
            bootstrap_servers=KAFKA_BROKERS,
            group_id=GROUP_ID,
            enable_auto_commit=True,
            auto_offset_reset="latest",
            value_deserializer=lambda b: _safe_json(b),
        )
        try:
            await consumer.start()
            log.info("consumer connected brokers=%s topic=%s", KAFKA_BROKERS, ENTITIES_TOPIC)
        except (KafkaConnectionError, OSError) as exc:
            log.warning("kafka unavailable (%s); retrying in 5s", exc)
            await consumer.stop()
            await _wait(stop, 5.0)
            continue

        counter = [0]
        try:
            async for msg in consumer:
                if stop.is_set():
                    break
                if msg.value is None:
                    continue
                await store.mark_message()
                for rec in _records_from_value(msg.value):
                    try:
                        await _ingest_record(store, rec, counter)
                    except Exception as exc:  # noqa: BLE001
                        log.exception("reshape error: %s", exc)
        except Exception as exc:  # noqa: BLE001
            log.warning("consume loop error (%s); reconnecting", exc)
        finally:
            await consumer.stop()
            if not stop.is_set():
                await _wait(stop, 2.0)


def _safe_json(raw: bytes) -> Any:
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None


async def _wait(stop: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
    except asyncio.TimeoutError:
        pass
