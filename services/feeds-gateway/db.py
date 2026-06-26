"""Durable log writer for the feeds-gateway.

Persists structured log rows to the shared ``osiris_logs.app_logs`` Postgres
table (same table the OSIRIS server writes to; the ``service`` column
distinguishes them). Writes are non-blocking: ``log_row`` enqueues, and a
background thread batch-inserts. If Postgres is unavailable the rows are dropped
to stdout so logging never blocks the consumer.
"""
from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
from datetime import datetime, timezone
from typing import Any

import psycopg

log = logging.getLogger("feeds-gateway.db")

PG = {
    "host": os.environ.get("LOGS_PG_HOST", "metastore-db"),
    "port": int(os.environ.get("LOGS_PG_PORT", "5432")),
    "user": os.environ.get("LOGS_PG_USER", "hive"),
    "password": os.environ.get("LOGS_PG_PASSWORD", "hive"),
}
LOGS_DB = os.environ.get("LOGS_PG_DB", "osiris_logs")
BOOTSTRAP_DB = os.environ.get("LOGS_PG_BOOTSTRAP_DB", "metastore")
SERVICE = "gateway"

DDL = """
CREATE TABLE IF NOT EXISTS app_logs (
  id            BIGSERIAL PRIMARY KEY,
  ts            TIMESTAMPTZ NOT NULL DEFAULT now(),
  service       TEXT NOT NULL,
  level         TEXT NOT NULL,
  scope         TEXT,
  msg           TEXT NOT NULL,
  ingest_run_id TEXT,
  data          JSONB
);
CREATE INDEX IF NOT EXISTS app_logs_ts_idx ON app_logs (ts DESC);
CREATE INDEX IF NOT EXISTS app_logs_svc_lvl_idx ON app_logs (service, level);
"""

_q: "queue.Queue[dict]" = queue.Queue(maxsize=2000)
_started = False
_lock = threading.Lock()


def _dsn(db: str) -> str:
    return f"host={PG['host']} port={PG['port']} user={PG['user']} password={PG['password']} dbname={db}"


def _bootstrap() -> None:
    """Create the logs database (if missing) and the app_logs table."""
    with psycopg.connect(_dsn(BOOTSTRAP_DB), autocommit=True) as conn:
        row = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (LOGS_DB,)).fetchone()
        if not row:
            try:
                conn.execute(f"CREATE DATABASE {LOGS_DB}")
            except psycopg.errors.DuplicateDatabase:
                pass
    with psycopg.connect(_dsn(LOGS_DB), autocommit=True) as conn:
        conn.execute(DDL)


def _writer_loop() -> None:
    conn: psycopg.Connection | None = None
    while True:
        item = _q.get()
        batch = [item]
        # Drain any other queued rows for a batched insert.
        try:
            while len(batch) < 100:
                batch.append(_q.get_nowait())
        except queue.Empty:
            pass
        try:
            if conn is None or conn.closed:
                conn = psycopg.connect(_dsn(LOGS_DB), autocommit=True)
            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO app_logs (ts, service, level, scope, msg, ingest_run_id, data) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)",
                    [
                        (
                            r["ts"], SERVICE, r["level"], r.get("scope"), r["msg"],
                            r.get("ingest_run_id"), json.dumps(r.get("data")),
                        )
                        for r in batch
                    ],
                )
        except Exception as exc:  # noqa: BLE001
            # Never block the consumer on logging; fall back to stdout.
            print(f"[gateway.db] log persist failed ({exc}); dropping {len(batch)} row(s) to stdout", flush=True)
            for r in batch:
                print(f"  {r['ts']} {r['level']} {r.get('scope')} {r['msg']} {r.get('data')}", flush=True)
            if conn is not None:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass
                conn = None


def start() -> None:
    """Bootstrap the schema and start the background writer (idempotent)."""
    global _started
    with _lock:
        if _started:
            return
        for attempt in range(10):
            try:
                _bootstrap()
                break
            except Exception as exc:  # noqa: BLE001
                log.warning("logs db bootstrap retry %s: %s", attempt + 1, exc)
                time.sleep(3)
        t = threading.Thread(target=_writer_loop, daemon=True, name="logs-writer")
        t.start()
        _started = True
        log.info("logs db writer started -> %s.app_logs", LOGS_DB)


def log_row(level: str, scope: str, msg: str, *, ingest_run_id: str | None = None, data: dict[str, Any] | None = None) -> None:
    """Enqueue a structured log row (non-blocking)."""
    rec = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "level": level,
        "scope": scope,
        "msg": msg,
        "ingest_run_id": ingest_run_id,
        "data": data,
    }
    try:
        _q.put_nowait(rec)
    except queue.Full:
        print(f"[gateway.db] log queue full; dropping: {scope} {msg}", flush=True)
