"""Lakehouse history reader for the TimeTravel replay.

Queries the Iceberg ``osiris.events_iceberg`` table through **HiveServer2** on the
Data Engineering Data Hub. Hive is RAZ-integrated, so S3 access "just works" with
the workload user's Ranger grants (no S3 credentials handled here) — the same
engine that answers your Hue queries. The OSIRIS canvas calls the gateway
``/history`` endpoints to pull time-windowed entity positions and replays them.

Two things make interactive scrubbing usable over Hive/Tez:
  * a **persistent** HiveServer2 connection is reused across queries, so only the
    first query pays the Tez cold-start (~tens of seconds); later chunk queries
    reuse the warm session and return quickly;
  * ``bounds`` (the scrubber extent) is **cached** so we don't re-scan the whole
    table on every activate.

Connection is env-driven (CDP Knox / LDAP):
    HIVE_HOST        Knox gateway host (bare hostname; a pasted jdbc:hive2://… is
                     tolerated and stripped)                                 (required)
    HIVE_HTTP_PATH   Knox httpPath, e.g. "<datahub-name>/cdp-proxy-api/hive" (required)
    HIVE_USER        workload username (e.g. cburns)
    HIVE_PASSWORD    workload password
    HIVE_PORT        default 443
    HIVE_DATABASE    default "osiris"
    HIVE_TABLE       default "events_iceberg"
    HIVE_USE_SSL     default "true"
    HISTORY_BOUNDS_TTL  seconds to cache bounds (default 300)
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Optional

log = logging.getLogger("feeds-gateway.history")


def _clean_host(raw: Optional[str]) -> Optional[str]:
    """Accept a bare host OR a pasted JDBC string and return just the hostname.

    e.g. 'jdbc:hive2://host.example:443/;transportMode=http' -> 'host.example'.
    The port is taken from HIVE_PORT, so any :port here is dropped.
    """
    if not raw:
        return raw
    h = raw.strip()
    if "://" in h:
        h = h.split("://", 1)[1]
    h = h.split("/", 1)[0].split(";", 1)[0]
    if ":" in h:
        h = h.split(":", 1)[0]
    return h


HIVE_HOST = _clean_host(os.environ.get("HIVE_HOST"))
HIVE_PORT = int(os.environ.get("HIVE_PORT", "443"))
HIVE_HTTP_PATH = os.environ.get("HIVE_HTTP_PATH", "")
HIVE_USER = os.environ.get("HIVE_USER")
HIVE_PASSWORD = os.environ.get("HIVE_PASSWORD")
HIVE_DATABASE = os.environ.get("HIVE_DATABASE", "osiris")
HIVE_TABLE = os.environ.get("HIVE_TABLE", "events_iceberg")
HIVE_USE_SSL = os.environ.get("HIVE_USE_SSL", "true").lower() == "true"
BOUNDS_TTL = float(os.environ.get("HISTORY_BOUNDS_TTL", "300"))
PRELOAD_HOURS = float(os.environ.get("HISTORY_PRELOAD_HOURS", "3"))

FQTN = f"{HIVE_DATABASE}.{HIVE_TABLE}"

# One reused connection (keeps the Tez session warm) guarded by a lock, since the
# FastAPI threadpool can call in from multiple threads and a HS2 cursor is serial.
_conn: Any = None
_conn_lock = threading.Lock()
_bounds_cache: Optional[dict] = None
_bounds_ts = 0.0

# In-memory replay buffer: one loaded time window served to the scrubber from RAM,
# so per-chunk reads never round-trip to Hive.
_win_lock = threading.Lock()
_win_events: list = []
_win_start = 0
_win_end = 0


def configured() -> bool:
    return bool(HIVE_HOST and HIVE_HTTP_PATH)


def _connect_new():
    if not configured():
        raise RuntimeError("HIVE_HOST / HIVE_HTTP_PATH not set — TimeTravel history is not configured")
    from impala.dbapi import connect  # lazy import

    return connect(
        host=HIVE_HOST,
        port=HIVE_PORT,
        database=HIVE_DATABASE,
        use_http_transport=True,
        http_path=HIVE_HTTP_PATH,
        use_ssl=HIVE_USE_SSL,
        auth_mechanism="LDAP",
        user=HIVE_USER,
        password=HIVE_PASSWORD,
    )


def _reset_conn() -> None:
    global _conn
    try:
        if _conn is not None:
            _conn.close()
    except Exception:  # noqa: BLE001
        pass
    _conn = None


def _run(sql: str) -> list:
    """Execute on the shared warm connection; reconnect once if it went stale."""
    global _conn
    with _conn_lock:
        last_exc: Optional[Exception] = None
        for attempt in (1, 2):
            try:
                if _conn is None:
                    _conn = _connect_new()
                cur = _conn.cursor()
                cur.execute(sql)
                rows = cur.fetchall()
                cur.close()
                return rows
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                _reset_conn()  # drop the (possibly stale) session and retry once
        raise last_exc  # type: ignore[misc]


def _to_epoch_ms(v: Any) -> Optional[int]:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return int(v)
    if isinstance(v, datetime):
        if v.tzinfo is None:
            v = v.replace(tzinfo=timezone.utc)
        return int(v.timestamp() * 1000)
    try:
        s = str(v).strip().replace("T", " ").replace("Z", "")
        return int(datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp() * 1000)
    except (ValueError, TypeError):
        return None


def _fmt_ts(epoch_ms: int) -> str:
    return datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def bounds(force: bool = False) -> dict:
    """Overall time extent + row count — cached (BOUNDS_TTL) so we don't re-scan."""
    global _bounds_cache, _bounds_ts
    now = time.time()
    if not force and _bounds_cache is not None and (now - _bounds_ts) < BOUNDS_TTL:
        return _bounds_cache
    rows = _run(f"SELECT MIN(event_time), MAX(event_time), COUNT(*) FROM {FQTN}")
    row = rows[0] if rows else (None, None, 0)
    _bounds_cache = {
        "min_time": _to_epoch_ms(row[0]),
        "max_time": _to_epoch_ms(row[1]),
        "count": int(row[2]) if row[2] is not None else 0,
    }
    _bounds_ts = now
    return _bounds_cache


def _in_clause(col: str, values: Optional[list[str]]) -> Optional[str]:
    if not values:
        return None
    safe = ",".join("'" + str(v).replace("'", "").replace('"', "") + "'" for v in values if v)
    return f"{col} IN ({safe})" if safe else None


def _query_window(
    start_ms: int, end_ms: int, types: Optional[list[str]] = None,
    feeds: Optional[list[str]] = None, limit: int = 20000,
) -> list[dict]:
    """Live Hive query for positional events in [start, end], ordered by time.

    ``feeds`` filters source_feed (flights/vessels/fires/weather/earthquakes) — the
    user picks these up front so we don't drag back the heavy vessel feed when they
    only asked for aviation. ``types`` (asset_type) is an optional finer filter.
    """
    where = [
        f"event_time BETWEEN '{_fmt_ts(int(start_ms))}' AND '{_fmt_ts(int(end_ms))}'",
        "lat IS NOT NULL",
        "lon IS NOT NULL",
    ]
    feed_clause = _in_clause("source_feed", feeds)
    if feed_clause:
        where.append(feed_clause)
    type_clause = _in_clause("asset_type", types)
    if type_clause:
        where.append(type_clause)
    sql = (
        f"SELECT asset_id, asset_type, lat, lon, event_time, source_feed "
        f"FROM {FQTN} WHERE {' AND '.join(where)} "
        f"ORDER BY event_time LIMIT {int(limit)}"
    )
    rows = _run(sql)
    return [
        {
            "asset_id": r[0],
            "asset_type": r[1],
            "lat": r[2],
            "lng": r[3],  # UI expects lng; column is lon
            "t": _to_epoch_ms(r[4]),
            "source_feed": r[5],
        }
        for r in rows
    ]


def load_window(
    start_ms: int, end_ms: int, types: Optional[list[str]] = None,
    feeds: Optional[list[str]] = None, limit: int = 500000,
) -> dict:
    """Pull a whole time window (for the selected feeds) into memory in ONE query.

    After this, the scrubber's per-chunk /history reads are filtered from RAM with
    no Hive round-trip — so scrubbing/playback never pays Tez latency. Only this
    single load does. The buffer holds just the chosen feeds, so picking Aviation
    never drags back the heavy vessel history.
    """
    global _win_events, _win_start, _win_end
    evs = _query_window(start_ms, end_ms, types, feeds, limit)
    with _win_lock:
        _win_events = evs
        _win_start = int(start_ms)
        _win_end = int(end_ms)
    log.info("history window loaded: %d events in [%s, %s]", len(evs), _fmt_ts(int(start_ms)), _fmt_ts(int(end_ms)))
    return {"count": len(evs), "start": int(start_ms), "end": int(end_ms)}


def preload_recent(hours: Optional[float] = None, feeds: Optional[list[str]] = None) -> dict:
    """Load the most recent ``hours`` of the given feeds into the memory buffer."""
    h = PRELOAD_HOURS if hours is None else hours
    b = bounds()
    if not b.get("max_time"):
        return {"count": 0, "start": None, "end": None}
    end = int(b["max_time"])
    start = max(int(b.get("min_time") or 0), end - int(h * 3_600_000))
    return load_window(start, end, feeds=feeds)


def window(
    start_ms: int, end_ms: int, types: Optional[list[str]] = None,
    feeds: Optional[list[str]] = None, limit: int = 20000,
) -> list[dict]:
    """Serve a replay chunk from the in-memory window if covered; else query Hive.

    The UI preloads a feed-filtered working window via load_window; chunk reads
    inside it are filtered from RAM (instant, already feed-scoped). A request
    outside the loaded window falls back to a live Hive query (slow).
    """
    lo, hi = int(start_ms), int(end_ms)
    with _win_lock:
        if _win_events and lo >= _win_start and hi <= _win_end:
            out = [e for e in _win_events if e["t"] is not None and lo <= e["t"] <= hi]
            return out[:limit]
    return _query_window(start_ms, end_ms, types, feeds, limit)
