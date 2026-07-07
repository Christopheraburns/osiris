"""OSIRIS MCP server — encapsulates the FeedsGateway + GraphDB APIs as MCP tools.

A thin façade for a Cloudera Agent Studio agent (or any MCP client): the agent
calls these tools; the server translates them into the OSIRIS HTTP surfaces that
already exist —
  * feeds-gateway  /feeds/*, /history/*, /intel/pattern-of-life
  * Memgraph shim  POST /cypher
  * osiris-intel   GET /resolve
It holds no state and is **read-only in v1**, so it is safe to run alongside human
operators. The goal is to show MCP + Agentic AI working next to the operators on
the same live picture.

Secure Mode: entity resolution defaults to the air-gapped graph (secure=1); the
legacy Wikidata path stays reachable via the per-call ``secure`` override — the
migration line lives in one place (SECURE_MODE_DEFAULT).

Transport: streamable-HTTP, mounted at /mcp. A GET / route satisfies the CAI
Application health check. Agent Studio connects to  https://<app-url>/mcp
"""
from __future__ import annotations

import json
import math
import os
import re
from typing import Optional

import httpx
from mcp.server.fastmcp import FastMCP
from starlette.responses import PlainTextResponse
from starlette.routing import Route

FEEDS_GATEWAY_URL = os.environ.get("FEEDS_GATEWAY_URL", "http://localhost:8091").rstrip("/")
MEMGRAPH_URL = os.environ.get("MEMGRAPH_URL", "http://localhost:8090").rstrip("/")
MEMGRAPH_API_TOKEN = os.environ.get("MEMGRAPH_API_TOKEN")
INTEL_URL = os.environ.get("INTEL_URL", "http://localhost:4000").rstrip("/")
SECURE_MODE_DEFAULT = os.environ.get("SECURE_MODE_DEFAULT", "true").lower() == "true"

mcp = FastMCP(
    "OSIRIS",
    host="127.0.0.1",
    port=int(os.environ.get("CDSW_APP_PORT", "8092")),
)

# Read-only Cypher guard — reject any mutating clause.
_WRITE_RE = re.compile(
    r"\b(CREATE|MERGE|SET|DELETE|REMOVE|DROP|DETACH\s+DELETE|FOREACH|LOAD\s+CSV)\b",
    re.IGNORECASE,
)


def _mem_headers() -> dict:
    return {"Authorization": f"Bearer {MEMGRAPH_API_TOKEN}"} if MEMGRAPH_API_TOKEN else {}


def _first_list(data: dict) -> list:
    for v in data.values():
        if isinstance(v, list):
            return v
    return []


# ── Health / discovery ────────────────────────────────────────────────────────

@mcp.tool()
async def health() -> dict:
    """Health of the OSIRIS backends this server fronts (streaming gateway + graph)."""
    out: dict = {}
    async with httpx.AsyncClient(timeout=8) as c:
        for name, url in (("gateway", f"{FEEDS_GATEWAY_URL}/health"), ("graph", f"{MEMGRAPH_URL}/health")):
            try:
                r = await c.get(url)
                out[name] = {"ok": r.status_code == 200, "status": r.status_code}
            except Exception as exc:  # noqa: BLE001
                out[name] = {"ok": False, "error": str(exc)}
    out["secure_mode_default"] = SECURE_MODE_DEFAULT
    return out


@mcp.tool()
async def list_feeds() -> list[str]:
    """List the live feeds available from the OSIRIS streaming gateway."""
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(f"{FEEDS_GATEWAY_URL}/feeds")
        r.raise_for_status()
        return r.json().get("migrated", [])


# ── Live feeds (situational awareness) ────────────────────────────────────────

@mcp.tool()
async def get_feed(name: str, limit: int = 500) -> dict:
    """Current snapshot of one feed by name (flights, vessels, fires, weather, earthquakes)."""
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(f"{FEEDS_GATEWAY_URL}/feeds/{name}")
        if r.status_code != 200:
            return {"error": f"feed '{name}' not available ({r.status_code})"}
        data = r.json()
    items = _first_list(data)
    return {"feed": name, "count": len(items), "items": items[:limit]}


@mcp.tool()
async def get_flights(
    callsign: Optional[str] = None, icao24: Optional[str] = None,
    squawk: Optional[str] = None, limit: int = 200,
) -> dict:
    """Current flights, optionally filtered by callsign, icao24, or squawk.

    Tip: squawk 7500/7600/7700 are the hijack/radio-failure/emergency codes.
    """
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(f"{FEEDS_GATEWAY_URL}/feeds/flights")
        r.raise_for_status()
        flights = r.json().get("flights", [])

    def match(f: dict) -> bool:
        if callsign and callsign.upper() not in str(f.get("callsign", "")).upper():
            return False
        if icao24 and icao24.lower() != str(f.get("icao24", "")).lower():
            return False
        if squawk and str(squawk) != str(f.get("squawk", "")):
            return False
        return True

    fl = [f for f in flights if match(f)]
    return {"count": len(fl), "flights": fl[:limit]}


@mcp.tool()
async def get_vessels(mmsi: Optional[str] = None, name: Optional[str] = None, limit: int = 200) -> dict:
    """Current vessels, optionally filtered by MMSI or a name substring."""
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(f"{FEEDS_GATEWAY_URL}/feeds/vessels")
        r.raise_for_status()
        vessels = r.json().get("vessels", [])

    def match(v: dict) -> bool:
        if mmsi and str(mmsi) != str(v.get("mmsi", "")):
            return False
        if name and name.upper() not in str(v.get("name", "")).upper():
            return False
        return True

    vs = [v for v in vessels if match(v)]
    return {"count": len(vs), "vessels": vs[:limit]}


# ── Graph / enrichment ────────────────────────────────────────────────────────

@mcp.tool()
async def resolve_entity(kind: str, id: str, secure: Optional[bool] = None) -> dict:
    """Resolve an entity to its knowledge-graph context (operator/owner/flag/sanctions).

    kind = aircraft | vessel | company | person | country | ip.
    Secure Mode (default) resolves against the air-gapped OSIRIS graph; set
    secure=false to use the legacy public-enrichment path.
    """
    use_secure = SECURE_MODE_DEFAULT if secure is None else secure
    params = {"type": kind, "id": id}
    if use_secure:
        params["secure"] = "1"
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(f"{INTEL_URL}/resolve", params=params)
        if r.status_code != 200:
            return {"error": f"resolver {r.status_code}", "detail": r.text[:200]}
        return r.json()


@mcp.tool()
async def query_graph(cypher: str, params: Optional[dict] = None) -> dict:
    """Run a READ-ONLY Cypher query against the OSIRIS knowledge graph (Memgraph).

    Mutating clauses (CREATE/MERGE/SET/DELETE/REMOVE/DROP/...) are rejected — this
    tool cannot change the graph. Returns { rows: [...] }.
    """
    if _WRITE_RE.search(cypher):
        return {"error": "read-only: mutating Cypher clauses are not permitted"}
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(
            f"{MEMGRAPH_URL}/cypher",
            json={"query": cypher, "params": params or {}},
            headers=_mem_headers(),
        )
        if r.status_code != 200:
            return {"error": f"graph {r.status_code}", "detail": r.text[:200]}
        return r.json()


@mcp.tool()
async def graph_neighborhood(icao: Optional[str] = None, name: Optional[str] = None) -> dict:
    """1-hop neighborhood of an airline/organization (country + parent) without
    hand-writing Cypher. Match by ICAO designator or a name substring."""
    if icao:
        q = (
            "MATCH (o:Organization {icao: $icao}) "
            "OPTIONAL MATCH (o)-[:COUNTRY]->(c:Country) "
            "OPTIONAL MATCH (o)-[:SUBSIDIARY_OF]->(p:Organization) "
            "RETURN o {.name, .icao} AS org, c {.name} AS country, p {.name} AS parent"
        )
        return await query_graph(q, {"icao": icao})
    if name:
        q = (
            "MATCH (o:Organization) WHERE toLower(o.name) CONTAINS toLower($name) "
            "OPTIONAL MATCH (o)-[:COUNTRY]->(c:Country) "
            "RETURN o {.name, .icao} AS org, c {.name} AS country LIMIT 10"
        )
        return await query_graph(q, {"name": name})
    return {"error": "provide icao or name"}


# ── Datalake history + fused intel ────────────────────────────────────────────

@mcp.tool()
async def history_bounds() -> dict:
    """Time extent + row count of the OSIRIS datalake (Iceberg), for history queries."""
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.get(f"{FEEDS_GATEWAY_URL}/history/bounds")
        if r.status_code != 200:
            return {"error": f"history {r.status_code}", "detail": r.text[:200]}
        return r.json()


@mcp.tool()
async def pattern_of_life(
    entity_type: str, callsign: Optional[str] = None, icao24: Optional[str] = None,
    mmsi: Optional[str] = None, imo: Optional[str] = None, name: Optional[str] = None,
    window_hours: int = 6,
) -> dict:
    """Fused Pattern-of-Life brief for one asset: graph context + lake trajectory +
    derived movement features (distance, dwell, signal gaps, chokepoint proximity)
    → a grounded narrative. entity_type = aircraft | vessel.

    Returns { entity, facts: [...], narrative }.
    """
    entity = {"type": entity_type, "callsign": callsign, "icao24": icao24,
              "mmsi": mmsi, "imo": imo, "name": name}
    entity = {k: v for k, v in entity.items() if v}
    facts: list = []
    narrative = ""
    async with httpx.AsyncClient(timeout=180) as c:
        async with c.stream(
            "POST", f"{FEEDS_GATEWAY_URL}/intel/pattern-of-life",
            json={"entity": entity, "window_hours": window_hours},
        ) as r:
            if r.status_code != 200:
                body = (await r.aread()).decode("utf-8", "ignore")
                return {"error": f"pattern-of-life {r.status_code}", "detail": body[:200]}
            async for line in r.aiter_lines():
                line = line.strip()
                if not line:
                    continue
                try:
                    evt = json.loads(line)
                except ValueError:
                    continue
                if evt.get("type") == "facts":
                    facts = evt.get("facts", [])
                elif evt.get("type") == "token":
                    narrative += evt.get("text", "")
    return {"entity": entity, "facts": facts, "narrative": narrative}


# ── Situational-awareness triage ──────────────────────────────────────────────

_CHOKEPOINTS = [
    ("Strait of Hormuz", 26.57, 56.25), ("Strait of Malacca", 2.5, 101.5),
    ("Suez Canal", 30.43, 32.34), ("Bab el-Mandeb", 12.58, 43.33),
    ("Panama Canal", 9.08, -79.68), ("Turkish Straits", 41.12, 29.07),
    ("Taiwan Strait", 24.0, 119.0), ("Strait of Gibraltar", 35.97, -5.5),
]
_EMERGENCY = {"7500": "hijack", "7600": "radio failure", "7700": "general emergency"}


def _haversine_km(a_lat: float, a_lng: float, b_lat: float, b_lng: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dphi = math.radians(b_lat - a_lat)
    dl = math.radians(b_lng - a_lng)
    x = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(x)))


def _safe_float(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 99.0


@mcp.tool()
async def assets_of_interest(
    max_results: int = 8, near_chokepoints: bool = True,
    check_sanctions: bool = False, sanction_scan_limit: int = 6,
) -> dict:
    """Triage the CURRENT live picture and return a small, ranked list of the tracks
    that warrant an operator's attention — so you don't have to reason over thousands
    of raw tracks. Flags:
      * aircraft squawking an emergency code (7500 hijack / 7600 radio-fail / 7700);
      * military aircraft;
      * vessels loitering on, or transiting, a strategic maritime chokepoint.
    With check_sanctions=true, the top candidates are resolved against the knowledge
    graph and any tied to a sanctioned owner/operator are promoted.

    Returns { scanned: {flights, vessels}, items: [ {kind, id, label, reason,
    priority, lat, lng, icao24|mmsi|imo} ] } — lower priority number = more urgent.
    Use this to focus, then drill in with resolve_entity or pattern_of_life.
    """
    async with httpx.AsyncClient(timeout=20) as c:
        try:
            fr = await c.get(f"{FEEDS_GATEWAY_URL}/feeds/flights")
            flights = fr.json().get("flights", []) if fr.status_code == 200 else []
        except Exception:  # noqa: BLE001
            flights = []
        try:
            vr = await c.get(f"{FEEDS_GATEWAY_URL}/feeds/vessels")
            vessels = vr.json().get("vessels", []) if vr.status_code == 200 else []
        except Exception:  # noqa: BLE001
            vessels = []

    items: list[dict] = []

    for f in flights:
        sq = str(f.get("squawk") or "")
        ident = f.get("callsign") or f.get("icao24")
        if not ident:
            continue
        if sq in _EMERGENCY:
            items.append({"kind": "aircraft", "id": ident, "icao24": f.get("icao24"), "label": ident,
                          "reason": f"emergency squawk {sq} ({_EMERGENCY[sq]})", "priority": 1,
                          "lat": f.get("lat"), "lng": f.get("lng")})
        elif str(f.get("category") or f.get("aircraft_category") or "").lower() in ("military", "mil"):
            items.append({"kind": "aircraft", "id": ident, "icao24": f.get("icao24"), "label": ident,
                          "reason": "military aircraft", "priority": 4,
                          "lat": f.get("lat"), "lng": f.get("lng")})

    if near_chokepoints:
        for v in vessels:
            lat, lng = v.get("lat"), v.get("lng")
            if lat is None or lng is None:
                continue
            name, clat, clng = min(_CHOKEPOINTS, key=lambda c: _haversine_km(lat, lng, c[1], c[2]))
            d = _haversine_km(lat, lng, clat, clng)
            if d <= 100:
                spd = v.get("speed")
                loiter = spd is not None and _safe_float(spd) < 1.0
                items.append({"kind": "vessel", "id": v.get("mmsi"), "mmsi": v.get("mmsi"), "imo": v.get("imo"),
                              "label": v.get("name") or v.get("mmsi"),
                              "reason": (f"loitering near {name} (~{int(d)} km)" if loiter
                                         else f"transiting {name} (~{int(d)} km)"),
                              "priority": 2 if loiter else 3, "lat": lat, "lng": lng})

    items.sort(key=lambda x: x["priority"])
    items = items[: max(max_results * 2, max_results)]

    if check_sanctions and items:
        async with httpx.AsyncClient(timeout=20) as c:
            for it in items[:sanction_scan_limit]:
                if it["kind"] == "aircraft":
                    rtype, rid = "aircraft", it.get("id")
                else:
                    rtype, rid = "vessel", (it.get("imo") or it.get("mmsi"))
                if not rid:
                    continue
                try:
                    r = await c.get(f"{INTEL_URL}/resolve", params={"type": rtype, "id": str(rid), "secure": "1"})
                    if r.status_code == 200:
                        links = r.json().get("links", [])
                        if any("sanction" in (l.get("label") or "").lower() for l in links):
                            it["reason"] = "SANCTIONED owner/operator — " + it["reason"]
                            it["priority"] = 0
                except Exception:  # noqa: BLE001
                    pass
        items.sort(key=lambda x: x["priority"])

    return {"scanned": {"flights": len(flights), "vessels": len(vessels)}, "items": items[:max_results]}


# ── ASGI app: streamable-HTTP (/mcp) + a GET / health route for CAI ───────────
app = mcp.streamable_http_app()


async def _root(_request) -> PlainTextResponse:
    return PlainTextResponse("OSIRIS MCP server — streamable-HTTP endpoint at /mcp")


app.router.routes.append(Route("/", _root, methods=["GET"]))
