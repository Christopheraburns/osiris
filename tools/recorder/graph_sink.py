"""Neo4j projection — the graph is a derived, rebuildable view of the lake.

Every write is idempotent (MERGE on a stable natural key + SET last-known
props), so re-running a batch or rebuilding the whole graph from Iceberg
converges to the same state. Aligned with docs/KG_Schema.md (Tier-1 durable
nodes, Tier-2 event nodes, structural promotion of latent actors).

Key choice matters for idempotency:
  * platforms with a real id  -> natural key == entity_id (icao24/mmsi/noradId)
  * ports/chokepoints have NO id field (entity_id is a synthesized, unstable
    index) -> key on the stable `name`
  * malware id is stable but its `name` (ip) is NOT unique -> key on entity_id
"""
from __future__ import annotations

import json
import os
import re
from collections import defaultdict

from neo4j import GraphDatabase

# (label, unique-key property) pairs -> uniqueness constraints.
CONSTRAINTS = [
    ("Aircraft", "icao24"),
    ("Vessel", "mmsi"),
    ("Satellite", "noradId"),
    ("Port", "name"),
    ("Chokepoint", "name"),
    ("Facility", "facilityId"),
    ("Sensor", "sensorId"),
    ("MalwareFamily", "entityId"),
    ("Outage", "entityId"),
    ("Entity", "entityId"),
    ("Organization", "orgId"),
    ("Country", "iso"),
    ("Event", "eventId"),
    ("NewsItem", "newsId"),
    ("Broadcast", "entityId"),
]

# (domain, entityType) -> (labels, key_prop, key_source)  key_source in {"id","name"}
def _resolve_obs(domain: str, entity_type: str) -> tuple[list[str], str, str]:
    if domain == "AIR":
        return (["Aircraft"], "icao24", "id")
    mapping = {
        ("SPACE", "SATELLITE"): (["Satellite"], "noradId", "id"),
        ("SEA", "VESSEL"): (["Vessel"], "mmsi", "id"),
        ("SEA", "PORT"): (["Port"], "name", "name"),
        ("SEA", "CHOKEPOINT"): (["Chokepoint"], "name", "name"),
        ("LAND", "NUCLEAR"): (["Facility"], "facilityId", "id"),
        ("LAND", "SUPPLIER"): (["Organization"], "orgId", "id"),
        ("LAND", "CCTV"): (["Sensor"], "sensorId", "id"),
        ("LAND", "AIR_QUALITY"): (["Sensor"], "sensorId", "id"),
        ("CYBER", "MALWARE"): (["MalwareFamily"], "entityId", "id"),
        ("CYBER", "OUTAGE"): (["Outage"], "entityId", "id"),
    }
    return mapping.get((domain, entity_type), (["Entity"], "entityId", "id"))


def _resolve_event(event_type: str, feed: str) -> tuple[list[str], str]:
    if feed == "news":
        return (["NewsItem"], "newsId")
    if feed == "live-news":
        return (["Broadcast"], "entityId")
    subtype = {"SEISMIC": "Seismic", "FIRE": "Fire", "INCIDENT": "Gdelt", "WEATHER": "Weather"}.get(event_type)
    return (["Event", subtype] if subtype else ["Event"], "eventId")


def _org_id(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(name).strip().lower()).strip("-")


# ICAO aircraft registration-prefix -> country (subset; mirrors intel/server.js
# and services/feeds-gateway/reg_prefixes.py). Used to derive REGISTERED_IN.
_REG_PREFIXES = {
    "N": "United States", "G": "United Kingdom", "F": "France", "D": "Germany", "I": "Italy",
    "JA": "Japan", "HL": "South Korea", "B": "China", "VT": "India", "TC": "Turkey",
    "SU": "Russia", "RA": "Russia", "UR": "Ukraine", "A6": "UAE", "A7": "Qatar", "9V": "Singapore",
    "VH": "Australia", "C": "Canada", "PP": "Brazil", "PR": "Brazil", "PT": "Brazil",
    "EC": "Spain", "PH": "Netherlands", "HS": "Thailand", "9M": "Malaysia", "PK": "Pakistan",
    "EP": "Iran", "YI": "Iraq", "HZ": "Saudi Arabia", "4X": "Israel", "SX": "Greece",
    "OE": "Austria", "HB": "Switzerland", "SE": "Sweden", "OH": "Finland", "LN": "Norway",
    "OY": "Denmark", "OO": "Belgium", "CS": "Portugal", "SP": "Poland",
    "OK": "Czech Republic", "HA": "Hungary", "YR": "Romania", "LZ": "Bulgaria",
    "EI": "Ireland", "EW": "Belarus", "ES": "Estonia", "YL": "Latvia", "LY": "Lithuania",
}


def _country_from_reg(registration) -> str | None:
    if not isinstance(registration, str) or not registration.strip():
        return None
    reg = registration.upper().strip()
    return _REG_PREFIXES.get(reg[:2]) or _REG_PREFIXES.get(reg[:1])


def _dedupe(rows: list[dict], key: str) -> list[dict]:
    """Last-wins dedupe by merge key — avoids the UNWIND+MERGE duplicate pitfall."""
    out: dict[str, dict] = {}
    for r in rows:
        k = r.get(key)
        if k is None or k == "":
            continue
        out[k] = r
    return list(out.values())


class GraphSink:
    def __init__(self) -> None:
        uri = os.environ.get("NEO4J_URI", "bolt://neo4j:7687")
        user = os.environ.get("NEO4J_USER", "neo4j")
        pwd = os.environ.get("NEO4J_PASSWORD", "osirisgraph1")
        self.driver = GraphDatabase.driver(uri, auth=(user, pwd))

    def close(self) -> None:
        self.driver.close()

    def ensure_constraints(self) -> None:
        with self.driver.session() as s:
            for label, key in CONSTRAINTS:
                s.run(
                    f"CREATE CONSTRAINT {label.lower()}_{key}_uq IF NOT EXISTS "
                    f"FOR (n:{label}) REQUIRE n.{key} IS UNIQUE"
                )

    # ── Observations (durable platforms / sites) ────────────────────────────
    def project_observations(self, rows: list[dict]) -> dict:
        # group by (labels, key_prop); attach merge value `_k` per row
        groups: dict[tuple, list[dict]] = defaultdict(list)
        for r in rows:
            labels, key_prop, key_src = _resolve_obs(r.get("domain"), r.get("entity_type"))
            r = {**r, "_k": r.get("name") if key_src == "name" else r.get("entity_id")}
            groups[(tuple(labels), key_prop)].append(r)

        counts = {"nodes": 0, "edges": 0, "skipped": 0}
        with self.driver.session() as s:
            for (labels, key_prop), rs in groups.items():
                rs = _dedupe(rs, "_k")
                if not rs:
                    continue
                try:
                    s.execute_write(self._merge_nodes, list(labels), key_prop, rs)
                    counts["nodes"] += len(rs)
                    counts["edges"] += s.execute_write(self._promote_actors, list(labels), key_prop, rs)
                    if list(labels) == ["Aircraft"]:
                        counts["edges"] += s.execute_write(self._enrich_aircraft, key_prop, rs)
                except Exception as exc:  # noqa: BLE001 — one bad group must not abort the rest
                    counts["skipped"] += len(rs)
                    counts["last_error"] = f"{labels}: {exc}"
        return counts

    @staticmethod
    def _enrich_aircraft(tx, key_prop: str, rows: list[dict]) -> int:
        """Set registration/model/subtype on Aircraft + derive REGISTERED_IN."""
        attrs, regs = [], []
        for r in rows:
            try:
                original = json.loads(r.get("properties") or "{}")
            except (TypeError, ValueError):
                original = {}
            if not isinstance(original, dict):
                original = {}
            registration = original.get("registration") or original.get("reg")
            model = original.get("model") or original.get("aircraft_type") or original.get("type")
            attrs.append({
                "k": r["_k"],
                "registration": registration if isinstance(registration, str) else None,
                "model": model if isinstance(model, str) else None,
                "subtype": r.get("entity_type"),
            })
            country = _country_from_reg(registration)
            if country:
                regs.append({
                    "k": r["_k"], "iso": _org_id(country), "country": country,
                    "feed": r.get("feed"), "confidence": r.get("confidence"),
                })

        if attrs:
            tx.run(
                f"""
                UNWIND $rows AS row
                MATCH (n:Aircraft {{{key_prop}: row.k}})
                SET n.registration = coalesce(row.registration, n.registration),
                    n.model        = coalesce(row.model, n.model),
                    n.subtype      = coalesce(row.subtype, n.subtype)
                """,
                rows=attrs,
            )
        edges = 0
        if regs:
            tx.run(
                f"""
                UNWIND $rows AS row
                MATCH (n:Aircraft {{{key_prop}: row.k}})
                MERGE (c:Country {{iso: row.iso}}) ON CREATE SET c.name = row.country
                MERGE (n)-[e:REGISTERED_IN]->(c)
                  SET e.feed = row.feed, e.derivedBy = 'derived', e.confidence = row.confidence
                """,
                rows=regs,
            )
            edges += len(regs)
        return edges

    @staticmethod
    def _merge_nodes(tx, labels: list[str], key_prop: str, rows: list[dict]) -> None:
        label_str = ":".join(labels)
        tx.run(
            f"""
            UNWIND $rows AS row
            MERGE (n:{label_str} {{{key_prop}: row._k}})
            ON CREATE SET n.firstObserved = row.observed_at
            SET n.entityId      = row.entity_id,
                n.name          = row.name,
                n.domain        = row.domain,
                n.entityType    = row.entity_type,
                n.provider      = row.provider,
                n.feed          = row.feed,
                n.confidence    = row.confidence,
                n.classification= row.classification,
                n.threatLevel   = row.threat,
                n.lastLat       = row.lat,
                n.lastLng       = row.lng,
                n.lastAlt       = row.alt,
                n.lastHeading   = row.heading,
                n.lastSpeed     = row.speed,
                n.lastPositionAt= row.observed_at,
                n.lastObserved  = row.observed_at,
                n.ingestRunId   = row.ingest_run_id
            """,
            rows=rows,
        )

    @staticmethod
    def _promote_actors(tx, labels: list[str], key_prop: str, rows: list[dict]) -> int:
        label_str = ":".join(labels)
        operated, owned, flagged = [], [], []
        for r in rows:
            try:
                original = json.loads(r.get("properties") or "{}")
            except (TypeError, ValueError):
                original = {}
            if not isinstance(original, dict):
                continue
            base = {"k": r["_k"], "feed": r.get("feed"), "confidence": r.get("confidence")}
            op = original.get("operator")
            if isinstance(op, str) and op.strip():
                operated.append({**base, "orgId": _org_id(op), "orgName": op.strip()})
            ow = original.get("owner")
            if isinstance(ow, str) and ow.strip():
                owned.append({**base, "orgId": _org_id(ow), "orgName": ow.strip()})
            fl = original.get("flag") or original.get("flagCountry")
            if isinstance(fl, str) and fl.strip():
                flagged.append({**base, "iso": _org_id(fl), "country": fl.strip()})

        edges = 0
        if operated:
            tx.run(
                f"""
                UNWIND $rows AS row
                MATCH (n:{label_str} {{{key_prop}: row.k}})
                MERGE (o:Organization {{orgId: row.orgId}}) ON CREATE SET o.name = row.orgName, o.role = 'operator'
                MERGE (n)-[e:OPERATED_BY]->(o) SET e.feed = row.feed, e.derivedBy = 'structural', e.confidence = row.confidence
                """,
                rows=operated,
            )
            edges += len(operated)
        if owned:
            tx.run(
                f"""
                UNWIND $rows AS row
                MATCH (n:{label_str} {{{key_prop}: row.k}})
                MERGE (o:Organization {{orgId: row.orgId}}) ON CREATE SET o.name = row.orgName, o.role = 'owner'
                MERGE (n)-[e:OWNED_BY]->(o) SET e.feed = row.feed, e.derivedBy = 'structural', e.confidence = row.confidence
                """,
                rows=owned,
            )
            edges += len(owned)
        if flagged:
            tx.run(
                f"""
                UNWIND $rows AS row
                MATCH (n:{label_str} {{{key_prop}: row.k}})
                MERGE (c:Country {{iso: row.iso}}) ON CREATE SET c.name = row.country
                MERGE (n)-[e:FLAGGED_TO]->(c) SET e.feed = row.feed, e.derivedBy = 'structural'
                """,
                rows=flagged,
            )
            edges += len(flagged)
        return edges

    # ── Events (immutable occurrences) ──────────────────────────────────────
    def project_events(self, rows: list[dict]) -> dict:
        groups: dict[tuple, list[dict]] = defaultdict(list)
        for r in rows:
            labels, key_prop = _resolve_event(r.get("event_type"), r.get("feed"))
            groups[(tuple(labels), key_prop)].append(r)

        counts = {"nodes": 0, "skipped": 0}
        with self.driver.session() as s:
            for (labels, key_prop), rs in groups.items():
                rs = _dedupe([{**r, "_k": r.get("event_id")} for r in rs], "_k")
                if not rs:
                    continue
                try:
                    s.execute_write(self._merge_events, list(labels), key_prop, rs)
                    counts["nodes"] += len(rs)
                except Exception as exc:  # noqa: BLE001
                    counts["skipped"] += len(rs)
                    counts["last_error"] = f"{labels}: {exc}"
        return counts

    @staticmethod
    def _merge_events(tx, labels: list[str], key_prop: str, rows: list[dict]) -> None:
        label_str = ":".join(labels)
        tx.run(
            f"""
            UNWIND $rows AS row
            MERGE (n:{label_str} {{{key_prop}: row._k}})
            ON CREATE SET n.firstObserved = row.occurred_at,
                          n.entityId      = row.event_id,
                          n.name          = row.name,
                          n.eventType     = row.event_type,
                          n.domain        = row.domain,
                          n.lat           = row.lat,
                          n.lng           = row.lng,
                          n.magnitude     = row.magnitude,
                          n.brightness    = row.brightness,
                          n.provider      = row.provider,
                          n.feed          = row.feed,
                          n.classification= 'UNCLASSIFIED',
                          n.occurredAt    = row.occurred_at,
                          n.ingestRunId   = row.ingest_run_id
            """,
            rows=rows,
        )
