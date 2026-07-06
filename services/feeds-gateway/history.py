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

FQTN = f"{HIVE_DATABASE}.{HIVE_TABLE}"

# One reused connection (keeps the Tez session warm) guarded by a lock, since the
# FastAPI threadpool can call in from multiple threads and a HS2 cursor is serial.
_conn: Any = None
_conn_lock = threading.Lock()
_bounds_cache: Optional[dict] = None
_bounds_ts = 0.0


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


def window(start_ms: int, end_ms: int, types: Optional[list[str]] = None, limit: int = 20000) -> list[dict]:
    """Positional events in [start, end], ordered by time — a single replay chunk.

    Kept small by design: the UI requests short time windows around the playhead
    and prefetches the next as it plays, so each query is cheap on the warm session.
    """
    where = [
        f"event_time BETWEEN '{_fmt_ts(int(start_ms))}' AND '{_fmt_ts(int(end_ms))}'",
        "lat IS NOT NULL",
        "lon IS NOT NULL",
    ]
    if types:
        safe = ",".join("'" + str(t).replace("'", "").replace('"', "") + "'" for t in types if t)
        if safe:
            where.append(f"asset_type IN ({safe})")
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
