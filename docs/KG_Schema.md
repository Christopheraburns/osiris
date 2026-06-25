# OSIRIS Knowledge Graph Schema

> A property-graph schema for an entity/relationship layer on top of the OSIRIS feed
> pipeline. It promotes the durable entities already flowing through the
> `PolybolosEntity` model into graph nodes, derives relationships from the feeds,
> and gives the LLM a grounded structure to reason over (GraphRAG).

---

## 1. Modeling principles

1. **The node is the *asset or actor*; the stream is its *observations*.** A vessel is one
   node; its 10,000 AIS position pings are time-series rows in Iceberg, not graph
   elements. The graph holds *identity and relationships*; Iceberg holds *history*.
2. **Promote latent entities out of the property bag.** Today `operator`, `owner`,
   `flag`, and `supplier` live as strings inside `properties`. These are the most
   durable, highest-value nodes (the hubs that connect feeds) and must become
   first-class nodes via entity resolution.
3. **Every node and edge carries provenance.** OSIRIS already attaches
   `source.provider`, `source.feed`, `source.confidence`, and `classification` to each
   entity. The graph preserves these on every element so the system can always answer
   *why* it believes a fact, from which feed, at what confidence and classification.
4. **Relationships are temporal.** A vessel's operator or a zone's controlling party
   changes over time. Edges that can change carry `validFrom` / `validTo`; observation
   facts carry `observedAt`.
5. **Selective, not exhaustive.** Only durable entities and *meaningful* derived
   relationships are promoted. Raw positional/measurement streams stay in Iceberg.

### Property-graph notation

The schema is expressed as a **labeled property graph** so it maps cleanly to either
target store:

- **Neo4j / Cypher** — node labels, relationship types, properties (fastest to build,
  best text-to-Cypher tooling).
- **JanusGraph / Gremlin on HBase** — vertex labels, edge labels, properties (the
  Cloudera-faithful target; HBase is a Cloudera product).

Notation: `(:NodeLabel {key})` for nodes, `-[:EDGE_TYPE]->` for directed edges.

---

## 2. Common properties (every node)

| Property | Source | Notes |
|---|---|---|
| `entityId` | Polybolos `id` | Original OSIRIS entity id (e.g. `osiris-sea-<mmsi>`). |
| `canonicalId` | derived | Stable cross-feed key after entity resolution (see §6). |
| `name` | `name` | Human-readable label/callsign. |
| `domain` | `domain` | AIR / SEA / LAND / SPACE / CYBER / EW / SUBSURFACE. |
| `entityType` | `entityType` | TRACK / FACILITY / EVENT / SENSOR / SIGNAL / INTEL. |
| `provider` | `source.provider` | e.g. `osiris`. |
| `feed` | `source.feed` | Originating feed (e.g. `maritime-ais`). |
| `confidence` | `source.confidence` | 0.0–1.0. |
| `classification` | `classification` | UNCLASSIFIED / FOUO / CONFIDENTIAL / SECRET. |
| `firstObserved` | first `timestamp` | When the node first appeared. |
| `lastObserved` | latest `timestamp` | Updated on every re-observation. |
| `threatLevel` | `threat` | NONE / LOW / ELEVATED / HIGH / CRITICAL. |

> **Last-known position** is stored as properties on platform nodes
> (`lastLat`, `lastLng`, `lastHeading`, `lastSpeed`, `lastAlt`, `lastPositionAt`) — a
> single current snapshot. The *history* of those values is **not** in the graph; it is
> the Iceberg time-series keyed by `canonicalId`.

---

## 3. Node catalog

### Tier 1 — Durable entities

#### Platforms (the asset, not the track)

| Label | Identifier key | Key properties | Source feed |
|---|---|---|---|
| `Vessel` | `mmsi` | `name`, `vesselType`, `flagCountry`, `destination`, `imo?` | `maritime-ais` (ships) |
| `Aircraft` | `icao24` | `registration`, `model`, `callsign` (current), `subtype` (commercial/private/jets/military) | `flights-*` |
| `Satellite` | `noradId` | `name`, `mission`, `country` | `satnogs` (satellites) |

> `callsign` on `Aircraft` is per-flight and semi-ephemeral — keep it as a current
> property, not an identifier. The durable key is `icao24`.

#### Fixed sites

| Label | Identifier key | Key properties | Source feed |
|---|---|---|---|
| `Facility` | `facilityId` (infra `id`) | `facilityType`, `owner`, `city`, `country`, `lat`, `lng` | `infrastructure` |
| `Port` | `portId` / UN-LOCODE / `name+country` | `name`, `country`, `lat`, `lng` | `maritime` (ports) |
| `Chokepoint` | `name` | `region`, `lat`, `lng` | `maritime` (chokepoints) |
| `Sensor` | `sensorId` (cctv/rad `id` or `name`) | `sensorType` (CCTV / RADIATION), `city`, `country`, `network`, `status`, `feedUrl?` | `cctv-network`, `radiation-network` |

#### Actors & geography

| Label | Identifier key | Key properties | Source feed |
|---|---|---|---|
| `Organization` | `orgId` (ER surrogate from normalized name) | `name`, `role` (operator/owner/supplier/manufacturer), `tier?`, `country`, `sector?` | `scm-suppliers`; extracted from `infrastructure.owner`, ship `operator`, etc. |
| `Country` | ISO 3166-1 alpha-2/3 | `name`, `riskScore?` | `country-risk`; referenced everywhere via flag/registration |
| `ConflictZone` | `name` | `controlledBy`, `parties`, `lat`, `lng` | `frontlines` |

#### Cyber

| Label | Identifier key | Key properties | Source feed |
|---|---|---|---|
| `MalwareFamily` | `name` / `id` | `type`, `firstSeen` | `malware` |
| `Vulnerability` | `cveId` | `severity`, `type` | `cyber-threats` |
| `ThreatActor` | `name` | `aliases` | extracted (news/GDELT/cyber) |

### Tier 2 — Event records (immutable, timestamped)

Discrete occurrences. They never update; they anchor pattern-of-life and temporal
reasoning. Modeled with a base `Event` and a subtype label.

| Label | Identifier key | Key properties | Source feed |
|---|---|---|---|
| `Event:Seismic` | `originalId` | `magnitude`, `depth`, `place`, `lat`, `lng`, `timestamp` | `usgs-earthquakes` |
| `Event:Fire` | `originalId` | `brightness`, `lat`, `lng`, `timestamp` | `nasa-firms` |
| `Event:Gdelt` | `originalId` | `eventType`, `tone`, `lat`, `lng`, `timestamp` | `gdelt` |
| `NewsItem` | `url` / `originalId` | `title`, `publishedAt`, `sourceName` | `news`, `live-news` |

### Tier 3 — Observations (NOT graph nodes)

Position pings, weather / air-quality / space-weather readings, market / crypto quotes.
These are **Iceberg time-series**, keyed by `canonicalId`, surfaced into the graph only
as (a) last-known properties on the platform node and (b) derived proximity/co-occurrence
edges (§4). The monitoring *station* may be a durable `Sensor`; its *readings* are Tier 3.

---

## 4. Edge catalog

All edges carry: `feed` (source), `confidence`, `classification`, `derivedBy`
(`structural` | `computed` | `extracted`), and — where the relationship can change —
`validFrom` / `validTo`. Computed edges also carry `observedAt`.

| Edge type | From → To | Derived by | Properties | Source |
|---|---|---|---|---|
| `OPERATED_BY` | Vessel / Aircraft / Satellite → Organization | structural | `validFrom`, `validTo` | ship `operator`, flight/sat operator |
| `OWNED_BY` | Facility / Vessel → Organization | structural | `validFrom`, `validTo` | `infrastructure.owner` |
| `FLAGGED_TO` | Vessel → Country | structural | — | ship `flag` |
| `REGISTERED_IN` | Aircraft / Satellite → Country | structural | — | registration prefix / sat country |
| `LOCATED_IN` | Facility / Port / Sensor / Event → Country | structural / computed | — | `city`/`country` fields or point-in-polygon |
| `SUPPLIES` | Organization → Organization | structural | `tier`, `component?` | `scm-suppliers` |
| `NEAR` | Platform → Chokepoint / Port / Facility / Event | computed | `distanceKm`, `observedAt`, `minDistanceKm` | positions vs fixed sites |
| `CO_OCCURRED_WITH` | Event ↔ Event / Platform ↔ Platform | computed | `windowSecs`, `distanceKm` | spatio-temporal correlation |
| `OCCURRED_IN` | Event → Country / ConflictZone / Chokepoint | computed | — | point-in-region |
| `MENTIONS` | NewsItem / Event:Gdelt → any durable entity | extracted | `sentiment`, `confidence`, `extractor` | LLM NER + relation extraction |
| `CONTROLS` | Organization / Faction → ConflictZone | structural / extracted | `validFrom`, `validTo` | `frontlines` |
| `TARGETS` | MalwareFamily / ThreatActor → Vulnerability / Facility / Organization / Country | extracted | `confidence` | `malware`, `cyber-threats`, news |
| `EXPLOITS` | MalwareFamily → Vulnerability | structural / extracted | — | `cyber-threats` |
| `MONITORS` | Sensor → Facility / Region | computed | `rangeKm` | sensor coverage (optional) |
| `SAME_AS` | any → its canonical node | ER | `method`, `score` | entity resolution (§6) |

---

## 5. Feed → graph contribution matrix

| Feed | Nodes it creates | Edges it creates |
|---|---|---|
| `flights-*` | `Aircraft`, (`Organization` operator) | `OPERATED_BY`, `REGISTERED_IN`, `NEAR` (computed) |
| `maritime` (ships) | `Vessel`, (`Organization`, `Country`) | `OPERATED_BY`, `FLAGGED_TO`, `NEAR` |
| `maritime` (ports/chokepoints) | `Port`, `Chokepoint` | `LOCATED_IN` |
| `satellites` | `Satellite` | `REGISTERED_IN`, `OPERATED_BY` |
| `infrastructure` | `Facility`, `Organization` (owner) | `OWNED_BY`, `LOCATED_IN` |
| `scm-suppliers` | `Organization` | `SUPPLIES` |
| `cctv` / `radiation` | `Sensor` | `LOCATED_IN`, `MONITORS` |
| `country-risk` | `Country` | — (enriches `riskScore`) |
| `frontlines` | `ConflictZone`, `Organization`/faction | `CONTROLS`, `OCCURRED_IN` |
| `earthquakes` | `Event:Seismic` | `OCCURRED_IN`, `NEAR`, `CO_OCCURRED_WITH` |
| `fires` | `Event:Fire` | `OCCURRED_IN`, `NEAR` |
| `gdelt` | `Event:Gdelt` | `MENTIONS`, `OCCURRED_IN` |
| `news` / `live-news` | `NewsItem` | `MENTIONS` |
| `malware` / `cyber-threats` | `MalwareFamily`, `Vulnerability`, `ThreatActor` | `EXPLOITS`, `TARGETS` |
| weather / air-quality / space-weather / markets / crypto / radar | — (Tier 3 → Iceberg) | feed `NEAR` / `CO_OCCURRED_WITH` via readings |

---

## 6. Entity resolution

The graph is only as good as its identity layer. Resolution strategy by node type:

- **Natural keys (high confidence):** `Vessel.mmsi`, `Aircraft.icao24`,
  `Satellite.noradId`, `Vulnerability.cveId`, `Country` ISO code. Merge on exact key.
- **Surrogate keys (Organizations, ThreatActors):** no natural key. Build a
  `canonicalId` from normalized name + context (country, sector), with a `SAME_AS`
  edge from each raw observation to the canonical node. This is the highest-risk,
  highest-value step — bad merges create false edges the LLM will then reason over
  confidently. Hold a manual review/allowlist for the entities that matter most.
- **Cross-feed fusion:** the same real-world vessel may appear in AIS, a sanctions
  mention (news), and a supplier record. Resolve to one `Vessel`/`Organization` node so
  a single click surfaces everything across feeds.

`derivedBy` and `confidence` on every edge let downstream queries filter out
low-trust, machine-extracted links when precision matters.

---

## 7. Classification & provenance propagation

- A node/edge inherits the **maximum** classification of its contributing sources.
- `MENTIONS` and other `extracted` edges record the `extractor` (model + version) so
  machine-inferred links are distinguishable from structural facts.
- Queries crossing classification boundaries must honor the node/edge `classification`;
  never imply a connection that mixes levels without policy.

---

## 8. Worked example — the maritime demo thread

A focused vertical slice that shows KG + LLM value end to end: *track a sanctioned
vessel and everything connected to it.*

```
(:Vessel {mmsi})-[:OPERATED_BY]->(:Organization {orgId})
(:Vessel)-[:FLAGGED_TO]->(:Country {iso})
(:Vessel)-[:NEAR {distanceKm, observedAt}]->(:Chokepoint {name})
(:NewsItem)-[:MENTIONS {sentiment}]->(:Vessel)
(:Organization)-[:SUPPLIES {tier}]->(:Organization)
```

**Cypher (Neo4j) — "sanctioned operators whose vessels entered a chokepoint this week":**

```cypher
MATCH (v:Vessel)-[n:NEAR]->(c:Chokepoint),
      (v)-[:OPERATED_BY]->(o:Organization)
WHERE o.sanctioned = true
  AND n.observedAt >= datetime() - duration('P7D')
RETURN o.name, v.name, c.name, n.observedAt
ORDER BY n.observedAt DESC;
```

**Gremlin (JanusGraph) — same intent:**

```groovy
g.V().hasLabel('Vessel').as('v')
 .out('OPERATED_BY').has('sanctioned', true).as('o')
 .select('v').outE('NEAR').has('observedAt', gte(weekAgo)).as('n')
 .inV().hasLabel('Chokepoint').as('c')
 .select('o','v','c','n').by('name').by('name').by('name').by('observedAt')
```

**LLM's role over this slice:** translate the analyst's natural-language question into
the query above, retrieve the subgraph, and synthesize a narrative answer *with citations
back to the originating feeds* (`feed`/`confidence`/`classification` on each element).

---

## 9. Mapping back to `PolybolosEntity`

| PolybolosEntity field | Graph use |
|---|---|
| `id` | `entityId` (raw), basis for `canonicalId` |
| `name` | node `name` |
| `domain`, `entityType` | node labels / typing |
| `position.*` | last-known properties + Iceberg time-series (not history in graph) |
| `threat` | `threatLevel` |
| `classification` | node/edge `classification` (propagated) |
| `source.{provider,feed,originalId,confidence}` | provenance on nodes/edges |
| `timestamp` | `firstObserved` / `lastObserved`; `observedAt` on computed edges |
| `properties.{operator,owner,flag,...}` | **promoted** to `Organization`/`Country` nodes + edges |
| `display.*` | not modeled (UI concern) |

---

## 10. Build order (suggested)

1. Natural-key nodes from structural feeds: `Vessel`, `Aircraft`, `Satellite`,
   `Facility`, `Port`, `Chokepoint`, `Sensor`, `Country`.
2. Promote latent actors → `Organization`, with `OPERATED_BY` / `OWNED_BY` /
   `FLAGGED_TO` / `SUPPLIES`. (Entity resolution starts here.)
3. Computed edges from Tier-3 streams in Flink: `NEAR`, `CO_OCCURRED_WITH`,
   `OCCURRED_IN`.
4. Event nodes + `MENTIONS` via LLM extraction over `news` / `gdelt`.
5. Cyber subgraph (`MalwareFamily`, `Vulnerability`, `TARGETS`, `EXPLOITS`).

Stages 1–2 give immediate cross-feed fusion value; 3–4 unlock the grounded multi-hop
Q&A that justifies the LLM.