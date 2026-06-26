"""Neo4j retrieval for the GraphRAG intelligence layer.

Reads the captured knowledge graph (projected from the lake by the recorder; see
tools/recorder/graph_sink.py and docs/KG_Schema.md). All queries are read-only.
The driver is created lazily and shared; failures degrade to empty results so the
intel layer never hard-fails on a graph outage.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from neo4j import GraphDatabase

log = logging.getLogger("feeds-gateway.graph")

NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://neo4j:7687")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "osirisgraph1")

_driver = None


def _get_driver():
    global _driver
    if _driver is None:
        _driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    return _driver


def close() -> None:
    global _driver
    if _driver is not None:
        try:
            _driver.close()
        finally:
            _driver = None


def available() -> bool:
    try:
        _get_driver().verify_connectivity()
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("neo4j unavailable: %s", exc)
        return False


# ── Phase 1: aircraft attribution (the node + its 1-hop neighbourhood) ──
AIRCRAFT_1HOP = """
MATCH (a:Aircraft {icao24: $icao24})
OPTIONAL MATCH (a)-[op:OPERATED_BY]->(org:Organization)
OPTIONAL MATCH (a)-[rg:REGISTERED_IN]->(rc:Country)
OPTIONAL MATCH (a)-[fl:FLAGGED_TO]->(fc:Country)
RETURN a {
         .icao24, .name, .registration, .model, .subtype, .domain, .entityType,
         .feed, .confidence, .classification, .threatLevel,
         .lastLat, .lastLng, .lastAlt, .lastHeading, .lastSpeed,
         .firstObserved, .lastObserved
       } AS aircraft,
       collect(DISTINCT { name: org.name, orgId: org.orgId, role: org.role,
                          sanctioned: org.sanctioned, feed: op.feed,
                          confidence: op.confidence }) AS operators,
       collect(DISTINCT { name: rc.name, iso: rc.iso, riskScore: rc.riskScore }) AS registeredIn,
       collect(DISTINCT { name: fc.name, iso: fc.iso }) AS flaggedTo
"""


def aircraft_attribution(icao24: str) -> dict[str, Any] | None:
    """Return the captured aircraft node + operator/country neighbours, or None."""
    if not icao24:
        return None
    try:
        with _get_driver().session() as s:
            rec = s.run(AIRCRAFT_1HOP, icao24=icao24).single()
    except Exception as exc:  # noqa: BLE001
        log.warning("aircraft_attribution query failed: %s", exc)
        return None
    if rec is None or rec["aircraft"] is None:
        return None

    def _clean(items: list[dict]) -> list[dict]:
        return [i for i in items if i and i.get("name")]

    return {
        "aircraft": rec["aircraft"],
        "operators": _clean(rec["operators"]),
        "registeredIn": _clean(rec["registeredIn"]),
        "flaggedTo": _clean(rec["flaggedTo"]),
    }
