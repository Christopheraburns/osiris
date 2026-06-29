# A1 — Wikidata reference core for the air-gapped graph

Bulk-import a bounded slice of Wikidata (the five entity classes OSIRIS reasons
over) into Neo4j, so the intelligence layer can resolve owners, operators,
parent companies, executives, heads of state, and country facts **behind the air
gap** — including queries beyond OSIRIS's current enrichment.

Wikidata is **CC0** (public domain), so mirroring and redistribution are fully
permitted.

## Scope

| Class | Wikidata `P31` | Neo4j label | Rough size |
|---|---|---|---|
| Country | `Q6256` | `Country` | ~200 |
| Airline | `Q46970` | `Airline` | ~10–15k |
| Ship | `Q11446` | `Ship` | tens of thousands |
| Business / organization | `Q4830453` / `Q43229` | `Organization` | low millions |
| Human | `Q5` | `Person` | ~11–12M (**bounded by default**) |

Every node also gets a `Wikidata` label, keyed by its QID, with a `nameKey`
(name slug) for linking to the live OSIRIS graph.

**Relationships derived** (only edges whose target is in-scope survive import):
`LOCATED_IN` (P17), `OWNED_BY` (P127), `OPERATED_BY` (P137), `PARENT_ORG`
(P749), `HAS_CEO` (P169), `EMPLOYED_BY` (P108), `HEAD_OF_STATE` (P35),
`CITIZEN_OF` (P27), `BORDERS` (P47), `MEMBER_OF` (P463), `REGISTERED_IN` (P8047).

**Node properties kept:** `population` (P1082), `gdp` (P2131), `callingCode`
(P474), `isoA2`/`isoA3` (P297/P298), `imo` (P458), `icao` (P230). The rest of
Wikidata's property tail is dropped to keep the dataset small.

## The human bound (the size lever)

`Q5` dominates everything. `transform.py` reads `WIKIDATA_HUMANS`:

- **`linked`** (default) — keep only people who are referenced as CEO / head of
  state by an in-scope org or country, or whose employer (P108) is an in-scope
  organization. Drops ~90% of the person tail while preserving the
  "who-runs-what" graph.
- **`all`** — keep every human. Large; plan for the hardware.

## Pipeline

```
full dump ──filter.sh──> filtered.ndjson.gz ──transform.py──> nodes.csv + rels.csv ──import.sh──> Neo4j
   (~140GB, CC0)            (class subset)         (bounded, whitelisted)          (offline bulk load)
```

1. **Filter** (connected side, multi-hour streaming pass):
   ```bash
   npm install -g wikibase-dump-filter
   ./filter.sh latest-all.json.gz filtered.ndjson.gz
   ```
2. **Transform** to import CSVs:
   ```bash
   python3 transform.py filtered.ndjson.gz import            # bounded humans
   WIKIDATA_HUMANS=all python3 transform.py filtered.ndjson.gz import   # all humans
   ```
3. **Import** into Neo4j (offline; **overwrites** the database — see below):
   ```bash
   docker compose cp tools/wikidata-a1/import neo4j:/var/lib/neo4j/import
   docker compose stop neo4j
   docker compose run --rm neo4j bash /var/lib/neo4j/import/../import.sh /var/lib/neo4j/import neo4j
   docker compose start neo4j
   ```
4. **Link** to the live OSIRIS entities:
   ```cypher
   CREATE INDEX wd_namekey IF NOT EXISTS FOR (n:Wikidata) ON (n.nameKey);
   // then run link_to_osiris.cypher
   ```

## Integrating with the existing OSIRIS graph

`neo4j-admin database import full` is **offline and overwrites** the database, so
it cannot be merged into a running graph that already holds `Vessel`/`Aircraft`
nodes. Treat the Wikidata core as the **foundation**:

1. Build the CSVs (steps 1–2) on the connected side; carry `import/` across the gap.
2. Import the Wikidata core into a fresh `neo4j` database (step 3).
3. Re-run the **recorder rebuild** + `graph_enrich_layer1.py` — the live OSIRIS
   entities re-materialize via idempotent MERGE on top of the reference core.
4. Run `link_to_osiris.cypher` to `SAME_AS`-link OSIRIS `Organization`/`Country`
   nodes to their Wikidata twins by name slug.

After linking, an OSIRIS platform reaches Wikidata facts in two hops:
```cypher
MATCH (v:Vessel)-[:OWNED_BY]->(:Organization)-[:SAME_AS]->(w:Wikidata)
MATCH (w)-[:HAS_CEO|PARENT_ORG|LOCATED_IN]->(x)
RETURN v, w, x;
```

If you run **Neo4j Enterprise**, an alternative is to import the core as a
*separate* `wikidata` database and query across with Fabric — no overwrite of the
OSIRIS db. Community (single user db) uses the foundation approach above.

## Sizing

- Plan for ~32–64 GB RAM and SSD on the build/import host.
- Filtering is a multi-hour streaming pass over the full dump.
- `neo4j-admin import` of this volume runs in tens of minutes to a couple hours.
- Result is static reference data: snapshot it and re-use across deployments.

## Files

| File | Role |
|---|---|
| `filter.sh` | class-filter the full dump (`wikibase-dump-filter`) |
| `transform.py` | NDJSON → Neo4j CSVs; human bound; property whitelist |
| `import.sh` | `neo4j-admin database import full` wrapper |
| `link_to_osiris.cypher` | `SAME_AS` link Wikidata core ↔ OSIRIS entities |

## Extending

- Widen the class set in `filter.sh` + `transform.py` (e.g. add international
  organizations `Q484652`, government agencies) to capture more `MEMBER_OF`
  targets.
- Add identifier-based linking (Country `isoA2`, vessel IMO → P458) to
  `link_to_osiris.cypher` for higher-confidence joins than the name slug.
- Re-point `intel/server.js` `WIKIDATA_ENDPOINT` at a local SPARQL mirror built
  from the same subset if you prefer SPARQL over Cypher for enrichment.
