"""Pattern-of-Life: fuse an asset's graph context with its lake trajectory.

For one selected asset:
  * graph/entity context via the osiris-intel resolver (operator/owner/flag/
    sanctions) — works for aircraft (callsign/icao24) and vessels (imo/mmsi);
  * movement history from the Iceberg lake (history.asset_track);
  * derived movement features (distance, dwell/loiter, signal gaps, chokepoint
    proximity) computed locally.

Then assemble provenance-tagged facts and stream a grounded LLM narrative, using
the same NDJSON protocol as intel.ask_stream (facts -> token -> done).
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import time
from typing import Any, AsyncIterator

import httpx

import db
import history
import llm

log = logging.getLogger("feeds-gateway.patternoflife")

INTEL_RESOLVER_URL = os.environ.get("INTEL_RESOLVER_URL", "http://osiris-intel:4000")
DEFAULT_WINDOW_HOURS = float(os.environ.get("POL_WINDOW_HOURS", "6"))

SYSTEM_PROMPT = (
    "You are OSIRIS, a grounded intelligence analyst writing a PATTERN-OF-LIFE brief for one "
    "tracked asset. Use ONLY the FACTS provided. Facts are tagged: 'graph:*' is entity context "
    "(operator/owner/flag/sanctions) from the knowledge graph; 'lake:*' is derived from the "
    "asset's recorded movement history. Write 3-5 sentences: what the asset is and who is behind "
    "it, then its movement pattern over the window (distance, dwell/loiter, signal gaps, proximity "
    "to chokepoints). Explicitly flag anything notable - sanctions, a long AIS/ADS-B dropout, "
    "loitering near a chokepoint. Cite the source tag inline in parentheses. If a fact is missing, "
    "say so; never invent."
)

# Strategic maritime/air chokepoints for proximity flagging (name, lat, lng).
CHOKEPOINTS = [
    ("Strait of Hormuz", 26.57, 56.25), ("Strait of Malacca", 2.5, 101.5),
    ("Suez Canal", 30.43, 32.34), ("Bab el-Mandeb", 12.58, 43.33),
    ("Panama Canal", 9.08, -79.68), ("Turkish Straits", 41.12, 29.07),
    ("Taiwan Strait", 24.0, 119.0), ("Strait of Gibraltar", 35.97, -5.5),
]


def _fact(subject: str, predicate: str, obj: Any, source: str) -> dict:
    return {"subject": subject, "predicate": predicate, "object": obj, "source": source}


def _line(obj: dict) -> bytes:
    return (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")


def _haversine_km(a_lat: float, a_lng: float, b_lat: float, b_lng: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dphi = math.radians(b_lat - a_lat)
    dl = math.radians(b_lng - a_lng)
    x = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(x)))


def _lake_asset_id(entity: dict) -> str:
    """The events_iceberg asset_id: icao24 for aircraft, mmsi for vessels."""
    if (entity.get("type") or "").lower() == "vessel":
        return str(entity.get("mmsi") or entity.get("imo") or "").strip()
    return str(entity.get("icao24") or entity.get("callsign") or "").strip()


async def _resolve_graph(entity: dict) -> dict:
    """osiris-intel /resolve for operator/owner/flag/sanctions (Secure Mode → Memgraph)."""
    if (entity.get("type") or "").lower() == "vessel":
        rid = str(entity.get("imo") or entity.get("mmsi") or "").strip()
        rtype = "vessel"
    else:
        rid = str(entity.get("callsign") or entity.get("icao24") or "").strip()
        rtype = "aircraft"
    if not rid:
        return {"nodes": [], "links": []}
    params = {"type": rtype, "id": rid, "secure": "1"}
    for k in ("registration", "model", "icao24"):
        if entity.get(k):
            params[k] = entity[k]
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(f"{INTEL_RESOLVER_URL}/resolve", params=params)
            if r.status_code == 200:
                d = r.json()
                return {"nodes": d.get("nodes", []), "links": d.get("links", [])}
    except Exception as exc:  # noqa: BLE001
        log.warning("resolve failed: %s", exc)
    return {"nodes": [], "links": []}


def _graph_facts(subject: str, sub: dict) -> list[dict]:
    by_id = {n.get("id"): n for n in sub.get("nodes", [])}
    facts: list[dict] = []
    for link in sub.get("links", []):
        tgt = by_id.get(link.get("target"), {})
        name = tgt.get("label")
        if not name:
            continue
        pred = (link.get("label") or "related").lower().replace(" ", "_")
        src = "graph:sanctions" if "sanction" in pred else "graph:memgraph"
        facts.append(_fact(subject, pred, name, src))
    return facts


def _track_features(subject: str, track: list[dict]) -> list[dict]:
    facts: list[dict] = []
    n = len(track)
    if n == 0:
        facts.append(_fact(subject, "track", "no recorded positions in the window", "lake:track"))
        return facts

    t0, t1 = track[0]["t"], track[-1]["t"]
    span_min = round((t1 - t0) / 60000, 1) if t0 and t1 else 0.0
    dist = 0.0
    max_gap_ms = 0
    for i in range(1, n):
        a, b = track[i - 1], track[i]
        if a["lat"] is not None and b["lat"] is not None:
            dist += _haversine_km(a["lat"], a["lng"], b["lat"], b["lng"])
        if a["t"] and b["t"]:
            max_gap_ms = max(max_gap_ms, b["t"] - a["t"])

    facts.append(_fact(subject, "positions_recorded", n, "lake:track"))
    facts.append(_fact(subject, "window_minutes", span_min, "lake:track"))
    facts.append(_fact(subject, "distance_km", round(dist, 1), "lake:track"))
    if span_min > 0:
        facts.append(_fact(subject, "avg_speed_knots", round((dist / 1.852) / (span_min / 60), 1), "lake:derived"))
    gap_min = round(max_gap_ms / 60000, 1)
    facts.append(_fact(subject, "max_signal_gap_min", gap_min, "lake:derived"))
    if gap_min >= 10:
        facts.append(_fact(subject, "signal_dropout", f"went dark for ~{gap_min} min", "lake:derived"))

    last = track[-1]
    if last["lat"] is not None:
        name, clat, clng = min(CHOKEPOINTS, key=lambda c: _haversine_km(last["lat"], last["lng"], c[1], c[2]))
        d = _haversine_km(last["lat"], last["lng"], clat, clng)
        if d <= 200:
            facts.append(_fact(subject, "near_chokepoint", f"{name} (~{int(round(d))} km)", "lake:derived"))

    first = track[0]
    if first["lat"] is not None and last["lat"] is not None and dist > 5:
        net = _haversine_km(first["lat"], first["lng"], last["lat"], last["lng"])
        if net / dist < 0.2:
            facts.append(_fact(subject, "loiter", "low net displacement — loitering/holding", "lake:derived"))
    return facts


def _facts_block(facts: list[dict]) -> str:
    if not facts:
        return "(no facts found)"
    return "\n".join(f"- {f['subject']} {f['predicate']} {f['object']} [{f['source']}]" for f in facts)


async def stream(entity: dict, window_hours: Any = None) -> AsyncIterator[bytes]:
    """Run the Pattern-of-Life pipeline and stream NDJSON events."""
    started = time.monotonic()
    subject = (
        entity.get("callsign") or entity.get("name") or entity.get("mmsi")
        or entity.get("icao24") or "asset"
    )
    try:
        hours = float(window_hours) if window_hours else DEFAULT_WINDOW_HOURS
    except (TypeError, ValueError):
        hours = DEFAULT_WINDOW_HOURS

    # 1) graph/entity context
    sub = await _resolve_graph(entity)
    facts = _graph_facts(str(subject), sub)

    # 2) lake trajectory + derived features
    aid = _lake_asset_id(entity)
    track: list[dict] = []
    if aid and history.configured():
        try:
            b = await asyncio.to_thread(history.bounds)
            end = int(b.get("max_time") or int(time.time() * 1000))
            start = end - int(hours * 3_600_000)
            track = await asyncio.to_thread(history.asset_track, aid, start, end)
        except Exception as exc:  # noqa: BLE001
            log.warning("asset_track failed: %s", exc)
    facts += _track_features(str(subject), track)

    yield _line({"type": "facts", "facts": facts, "subgraph": sub, "model": llm.OLLAMA_MODEL})

    # 3) grounded narrative
    user_prompt = (
        f"SUBJECT: {entity.get('type', 'asset')} {subject} "
        f"(window {int(hours)}h)\n\nFACTS:\n{_facts_block(facts)}\n\n"
        f"Write the pattern-of-life brief using only these facts, citing source tags."
    )
    parts: list[str] = []
    async for token in llm.chat_stream(SYSTEM_PROMPT, user_prompt):
        parts.append(token)
        yield _line({"type": "token", "text": token})

    latency = int((time.monotonic() - started) * 1000)
    yield _line({"type": "done", "latency_ms": latency})

    db.log_row(
        "info", "patternoflife", f"pattern-of-life: {subject}",
        data={
            "subject": str(subject),
            "type": entity.get("type"),
            "fact_count": len(facts),
            "track_points": len(track),
            "window_hours": hours,
            "answer": "".join(parts)[:2000],
            "latency_ms": latency,
            "model": llm.OLLAMA_MODEL,
        },
    )
