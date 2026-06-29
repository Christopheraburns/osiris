"""Layer-1 graph enrichment: connect the *remaining* durable entities.

The recorder already promotes structural actor edges (OPERATED_BY / OWNED_BY /
FLAGGED_TO) and REGISTERED_IN for aircraft that carry a `registration`. In
practice most aircraft arrive from ADS-B with only an `icao24` and no
registration, so they end up unconnected.

This script closes that gap **in-graph** (no re-ingest) by deriving the
registration country for every Aircraft from two deterministic sources:

  1. icao24  -> country   (ICAO 24-bit address blocks are allocated by state)
  2. registration prefix  -> country   (fallback; mirrors the recorder table)

It is idempotent: it MERGEs Country nodes on the same `iso` key the recorder
uses (org-id slug of the country name) and MERGEs REGISTERED_IN edges, so it is
safe to re-run and converges with a normal recorder rebuild.

Run it from inside the stack (so `neo4j:7687` resolves), e.g.:

    docker compose exec osiris-recorder python /app/graph_enrich_layer1.py
    # or copy in first:
    docker compose cp tools/recorder/graph_enrich_layer1.py osiris-recorder:/app/
"""
from __future__ import annotations

import os
import re

from neo4j import GraphDatabase


def _org_id(name: str) -> str:
    """Match the recorder's Country.iso key exactly (graph_sink._org_id)."""
    return re.sub(r"[^a-z0-9]+", "-", str(name).strip().lower()).strip("-")


# ICAO 24-bit address allocation blocks (inclusive int ranges). High-confidence
# national blocks that cover the large majority of global traffic. Extend as
# needed; unmatched aircraft fall back to the registration-prefix table below.
_ICAO24_BLOCKS = [
    (0xA00000, 0xAFFFFF, "United States"),
    (0xC00000, 0xC3FFFF, "Canada"),
    (0xC80000, 0xC87FFF, "New Zealand"),
    (0x7C0000, 0x7FFFFF, "Australia"),
    (0x780000, 0x7BFFFF, "China"),
    (0x800000, 0x83FFFF, "India"),
    (0x840000, 0x87FFFF, "Japan"),
    (0x718000, 0x71FFFF, "South Korea"),
    (0x140000, 0x1FFFFF, "Russia"),
    (0x3C0000, 0x3FFFFF, "Germany"),
    (0x380000, 0x3BFFFF, "France"),
    (0x400000, 0x43FFFF, "United Kingdom"),
    (0x300000, 0x33FFFF, "Italy"),
    (0x340000, 0x37FFFF, "Spain"),
    (0x480000, 0x487FFF, "Netherlands"),
    (0x4A0000, 0x4A7FFF, "Sweden"),
    (0x4B0000, 0x4B7FFF, "Switzerland"),
    (0x4CA000, 0x4CAFFF, "Ireland"),
    (0x500000, 0x5003FF, "Slovenia"),
    (0x508000, 0x50FFFF, "Ukraine"),
    (0x4B8000, 0x4BFFFF, "Turkey"),
    (0x710000, 0x717FFF, "Saudi Arabia"),
    (0x896000, 0x896FFF, "United Arab Emirates"),
    (0x738000, 0x73FFFF, "Israel"),
    (0xE40000, 0xE7FFFF, "Brazil"),
]

# Registration-prefix fallback (mirrors graph_sink._REG_PREFIXES).
_REG_PREFIXES = {
    "N": "United States", "G": "United Kingdom", "F": "France", "D": "Germany", "I": "Italy",
    "JA": "Japan", "HL": "South Korea", "B": "China", "VT": "India", "TC": "Turkey",
    "SU": "Russia", "RA": "Russia", "UR": "Ukraine", "A6": "United Arab Emirates", "A7": "Qatar",
    "9V": "Singapore", "VH": "Australia", "C": "Canada", "PP": "Brazil", "PR": "Brazil",
    "PT": "Brazil", "EC": "Spain", "PH": "Netherlands", "HS": "Thailand", "9M": "Malaysia",
    "PK": "Pakistan", "EP": "Iran", "YI": "Iraq", "HZ": "Saudi Arabia", "4X": "Israel",
    "SX": "Greece", "OE": "Austria", "HB": "Switzerland", "SE": "Sweden", "OH": "Finland",
    "LN": "Norway", "OY": "Denmark", "OO": "Belgium", "CS": "Portugal", "SP": "Poland",
    "OK": "Czech Republic", "HA": "Hungary", "YR": "Romania", "LZ": "Bulgaria",
    "EI": "Ireland", "EW": "Belarus", "ES": "Estonia", "YL": "Latvia", "LY": "Lithuania",
}


def country_from_icao24(icao24) -> str | None:
    if not isinstance(icao24, str) or not icao24.strip():
        return None
    try:
        v = int(icao24.strip(), 16)
    except ValueError:
        return None
    for lo, hi, country in _ICAO24_BLOCKS:
        if lo <= v <= hi:
            return country
    return None


def country_from_reg(registration) -> str | None:
    if not isinstance(registration, str) or not registration.strip():
        return None
    reg = registration.upper().strip()
    return _REG_PREFIXES.get(reg[:2]) or _REG_PREFIXES.get(reg[:1])


def main() -> None:
    uri = os.environ.get("NEO4J_URI", "bolt://neo4j:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    pwd = os.environ.get("NEO4J_PASSWORD", "osirisgraph1")
    driver = GraphDatabase.driver(uri, auth=(user, pwd))

    with driver.session() as s:
        aircraft = s.run(
            "MATCH (a:Aircraft) RETURN a.icao24 AS icao24, a.registration AS reg"
        ).data()

    total = len(aircraft)
    edges = []  # {k, iso, country, method}
    by_icao = by_reg = 0
    for a in aircraft:
        icao = a.get("icao24")
        country = country_from_icao24(icao)
        method = "icao24"
        if not country:
            country = country_from_reg(a.get("reg"))
            method = "registration"
        if country and icao:
            edges.append({"k": icao, "iso": _org_id(country), "country": country, "method": method})
            if method == "icao24":
                by_icao += 1
            else:
                by_reg += 1

    with driver.session() as s:
        # Country constraint already exists from the recorder; create if missing.
        s.run(
            "CREATE CONSTRAINT country_iso_uq IF NOT EXISTS "
            "FOR (n:Country) REQUIRE n.iso IS UNIQUE"
        )
        # Batched, idempotent MERGE of REGISTERED_IN edges.
        for i in range(0, len(edges), 500):
            batch = edges[i:i + 500]
            s.run(
                """
                UNWIND $rows AS row
                MATCH (a:Aircraft {icao24: row.k})
                MERGE (c:Country {iso: row.iso}) ON CREATE SET c.name = row.country
                MERGE (a)-[e:REGISTERED_IN]->(c)
                  SET e.derivedBy = 'derived', e.method = row.method
                """,
                rows=batch,
            )

        countries = s.run(
            "MATCH (:Aircraft)-[:REGISTERED_IN]->(c:Country) RETURN count(DISTINCT c) AS n"
        ).single()["n"]

    driver.close()
    print("Layer-1 aircraft enrichment complete")
    print(f"  aircraft scanned     : {total}")
    print(f"  REGISTERED_IN edges  : {len(edges)}  (icao24={by_icao}, registration={by_reg})")
    print(f"  distinct countries   : {countries}")
    print(f"  unresolved aircraft  : {total - len(edges)}")


if __name__ == "__main__":
    main()
