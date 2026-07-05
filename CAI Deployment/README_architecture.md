# OSIRIS — Architecture Overview

OSIRIS is a real-time OSINT geospatial situational-awareness platform. It ingests
live and reference intelligence, surfaces entities on a world-canvas UI, discovers
relationships in a graph database, and retains an event history for time-travel
analysis — all designed to run **fully air-gapped** on Cloudera AI (CAI) using
managed platform capabilities that map 1:1 to on-premise equivalents.

> **Design north star: air-gap discipline.** Every architectural choice has an
> on-prem equivalent and eliminates runtime internet dependencies. External APIs
> (Wikidata, OpenSanctions, FIRMS, etc.) are acceptable in interim build stages but
> are internalized before the final demo. Ozone is the on-prem twin for S3; managed
> Cloudera services (DataFlow, Streams Messaging, Streaming Analytics) are the
> twins for the bespoke Docker services OSIRIS started as.

---

## 1. Two-store data model (the core distinction)

OSIRIS separates data by the *question it answers*, not by source:

| | **Reference data** | **Event data** |
|---|---|---|
| Question | "What is this, and what is it connected to?" | "What happened, and when?" |
| Store | **Memgraph** (graph) | **Iceberg** (data lake) |
| Organizing axis | Connection / relationships | Time |
| Contents | Durable entities: vessels, aircraft/airlines, organizations, countries; OFAC sanctions | Observations: flights, fires, weather, earthquakes, vessel sightings |
| Source | Wikidata master extract, OFAC SDN | Live feeds via NiFi |
| Used for | Entity resolution, GraphRAG, relationship discovery | Replay, pattern learning, time-travel |

The same real-world entity can appear in both — as a **node** in the graph and as a
**field inside timestamped events** in the lake. They are joined by a shared
real-world identifier (IMO for vessels, ICAO code for airlines, etc.), which is the
single most important design invariant: pick one canonical identifier per entity
type and make every dataset carry it, indexed.

---

## 2. Runtime topology — two CAI projects

OSIRIS is deployed across **two separate CAI projects**, each with its own memory
space and its own set of CAI Applications.

### Project A — **Osiris Prime**
Hosts the application tier and the ingestion/enrichment services. CAI Applications:

- **Osiris-UI** — Next.js + MapLibre world-canvas UI
- **Osiris-Intel-Server** — Node enrichment resolver, `GET /resolve`
- **Osiris-Feeds-Gateway** — supplies the canvas UI with event data
- **Osiris-Tile-Server** — tileserver-gl-light serving self-hosted OpenMapTiles
  vector tiles

*(A `recorder` service — writes event data to the Iceberg table — is planned but
not yet deployed; omitted here.)*

### Project B — **Osiris GraphDB**
Hosts the graph database in isolation. CAI Application:

- **Memgraph** — packaged inside a custom runtime image, run as a long-running
  CAI Application. Bolt (7687) stays on localhost; a FastAPI HTTP shim on
  `CDSW_APP_PORT` (`POST /cypher`, `GET /health`) is the only surface exposed
  through the subdomain. Cross-project callers (Osiris Prime services) reach the
  graph via this HTTP shim.

> **Repo layout:** both projects sync from the **same repository**
> (`https://github.com/christopheraburns/osiris`); each Application's code lives in
> its own subdirectory. Every project/Application uses the CAI default working
> directory **`/home/cdsw`**, so the repo is pulled to `/home/cdsw` and script
> paths resolve as `/home/cdsw/<subdir>/...`.

> **Why two projects:** CAI projects have separate memory/filesystem spaces.
> Isolating the stateful graph DB in its own project keeps its durability mount,
> runtime image, and lifecycle independent of the app tier. Hosting a stateful
> GraphDB inside CAI at all is a deliberate demonstration of air-gap flexibility;
> a real production deployment would run the graph as a dedicated service.

---

## 3. Data flow

```
                          ┌─────────────────────────────────────────┐
   LIVE / SIMULATED       │  NiFi (DataFlow)  — ingestion abstraction│
   SOURCES  ──────────────►  one flow per feed; normalizes to        │
   (FIRMS, EONET, NWS,    │  PolyBolos-shaped event envelopes         │
    AIS, flights, ...)    └───────────────┬─────────────────────────┘
                                          │
                                          ▼
                         Kafka topic  `osiris-events`  (Streams Messaging)
                          one topic, entityType routing (FIRE/WEATHER/…)
                          ┌───────────────┼───────────────────────────┐
                          ▼               ▼                           ▼
                  feeds-gateway     Flink (Streaming            NiFi → Memgraph
                  → OSIRIS canvas   Analytics) → Iceberg        (live graph updates;
                  (event data to    (event history for          resolves event
                   the UI)          time-travel)                 entities to backbone)
```

- **Reference data** loads **directly into Memgraph** (bulk, one-time) — it is NOT
  routed through Kafka/PolyBolos. PolyBolos is the canvas-entity format; it is not
  the graph format.
- **Event data** flows NiFi → Kafka → {feeds-gateway → UI} and NiFi → Kafka →
  Flink → Iceberg.
- **NiFi is the ingestion abstraction** in front of whatever sources exist on-prem.
  "Feeds are live" is the demo thesis: NiFi replaces hardwired API calls so the
  same pipeline serves real sensors or replayed datasets identically.

---

## 4. The graph backbone (Memgraph)

Loaded from a filtered Wikidata master extract (`nohumans.ndjson.gz`, ~883k
entities — no people by design):

**Node labels**
- `:Vessel` (~91k) — individual ships; join key `imo` (on the ~1/3 that carry it)
- `:Organization` (~792k) — all business/airline subtypes folded to one label,
  `subtype` property preserves the specific Wikidata QID; airline lookup keys
  `icao`/`iata`/`callsign` on the ~3,391 airlines that carry them
- `:Country` (~281) — real named nodes (sovereign states)
- `:Place` — HQ/home-port targets, created on demand

**Relationships**
- `(:Vessel|:Organization)-[:COUNTRY]->(:Country)` (P17) — the primary connector
- `(:Organization)-[:HEADQUARTERED_IN]->(:Place)` (P159)
- `(:Organization)-[:SUBSIDIARY_OF]->(:Organization)` (P749)
- `(:Vessel)-[:MANUFACTURED_BY]->(:Organization)` (P176)

**Identity:** every node MERGEs on `qid` (Wikidata QID). Real-world join keys
(`imo`, `icao`, `iata`) are separately indexed so live events resolve to backbone
nodes.

> Aircraft note: Wikidata contains aircraft *models* and *airlines*, not individual
> airframes. Live flight events therefore resolve at the **operator (airline)**
> level via `icao`, not at tail-number level — which matches what the resolver
> always did against live Wikidata.

---

## 5. Enrichment resolver (osiris-intel)

The "brain": `GET /resolve?type=<type>&id=<id>` returns a `{nodes, links}` graph
that the OSIRIS "Intel Deep Dive" panel renders. Resolvers exist for vessel,
aircraft, company, person. **Currently** these query public Wikidata SPARQL +
an in-memory OFAC index; the air-gap target is to repoint them at Memgraph via
the `/cypher` shim (SPARQL → Cypher rewrite), keyed on `imo`/`name`/`icao`.

---

## 6. Custom runtime images

- `christopheraburns/osiris-node-runtime:1.0.1` — Node 22 on Cloudera PBJ Workbench base
- `christopheraburns/osiris-memgraph-runtime:1.0.1` — Memgraph in PBJ Python 3.11
<!-- FILL IN: any other custom images; registry location if not Docker Hub -->

---

## 7. Repository

Single repo, both projects: **`https://github.com/christopheraburns/osiris`**
<!-- FILL IN: default branch each project syncs from, if not `main` -->

Each CAI Application points its startup script at its own subdirectory under
`/home/cdsw`. See `APPLICATIONS.md` for the exact script path per Application.

---

*See `DEPLOYMENT.md` for the step-by-step redeploy runbook and `APPLICATIONS.md`
for the per-Application deployment reference (ports, env vars, egress, gotchas).*
