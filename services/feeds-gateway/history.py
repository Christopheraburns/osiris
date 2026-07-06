"""Lakehouse history reader for the TimeTravel replay.

Queries the Iceberg ``osiris.events_iceberg`` table through **HiveServer2** on the
Data Engineering Data Hub. Hive is RAZ-integrated, so S3 access "just works" with
the workload user's Ranger grants (no S3 credentials handled here) — the same
engine that answers your Hue queries. The OSIRIS canvas calls the gateway
``/history`` endpoints to pull time-windowed entity positions and replays them.

Connection is env-driven (CDP Knox / LDAP). Set these on the gateway Application
(all read once at import; missing HIVE_HOST just makes the endpoints return 503):

    HIVE_HOST        Knox gateway host, e.g. <env>-gateway.<...>.cloudera.site   (required)
    HIVE_HTTP_PATH   Knox httpPath, e.g. "<datahub-name>/cdp-proxy-api/hive"     (required)
    HIVE_USER        workload username (e.g. cburns)
    HIVE_PASSWORD    workload password
    HIVE_PORT        default 443
    HIVE_DATABASE    default "osiris"
    HIVE_TABLE       default "events_iceberg"
    HIVE_USE_SSL     default "true"

The Hive JDBC URL Cloudera shows you (jdbc:hive2://HOST:443/;ssl=true;
transportMode=http;httpPath=PATH) maps 1:1 onto these: HOST->HIVE_HOST,
PATH->HIVE_HTTP_PATH.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

log = logging.getLogger("feeds-gateway.history")

HIVE_HOST = os.environ.get("HIVE_HOST")
HIVE_PORT = int(os.environ.get("HIVE_PORT", "443"))
HIVE_HTTP_PATH = os.environ.get("HIVE_HTTP_PATH", "")
HIVE_USER = os.environ.get("HIVE_USER")
HIVE_PASSWORD = os.environ.get("HIVE_PASSWORD")
HIVE_DATABASE = os.environ.get("HIVE_DATABASE", "osiris")
HIVE_TABLE = os.environ.get("HIVE_TABLE", "events_iceberg")
HIVE_USE_SSL = os.environ.get("HIVE_USE_SSL", "true").lower() == "true"

FQTN = f"{HIVE_DATABASE}.{HIVE_TABLE}"


def configured() -> bool:
    return bool(HIVE_HOST and HIVE_HTTP_PATH)


def _connect():
    """Open a HiveServer2 connection over Knox (HTTP transport + LDAP auth).

    impyla is imported lazily so the gateway still boots if the driver or the
    Hive config isn't present — only the /history endpoints degrade.
    """
    if not configured():
        raise RuntimeError(
            "HIVE_HOST / HIVE_HTTP_PATH not set — TimeTravel history is not configured"
        )
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


def _to_epoch_ms(v: Any) -> Optional[int]:
    """Coerce a Hive value (datetime or ISO/space string) to epoch milliseconds."""
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
    """epoch ms -> 'YYYY-MM-DD HH:MM:SS' UTC (matches the zoneless Iceberg column)."""
    return datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def bounds() -> dict:
    """Overall time extent + row count — drives the scrubber's min/max."""
    sql = f"SELECT MIN(event_time), MAX(event_time), COUNT(*) FROM {FQTN}"
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(sql)
        row = cur.fetchone()
        cur.close()
    finally:
        conn.close()
    return {
        "min_time": _to_epoch_ms(row[0]) if row else None,
        "max_time": _to_epoch_ms(row[1]) if row else None,
        "count": int(row[2]) if row and row[2] is not None else 0,
    }


def window(start_ms: int, end_ms: int, types: Optional[list[str]] = None, limit: int = 50000) -> list[dict]:
    """Positional events in [start, end], ordered by time — the replay frames.

    start/end are ints we format ourselves (injection-safe); ``types`` values are
    stripped of quotes; ``limit`` is coerced to int. asset_id/etc. are outputs.
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
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(sql)
        rows = cur.fetchall()
        cur.close()
    finally:
        conn.close()
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
