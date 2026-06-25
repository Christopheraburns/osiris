# OSIRIS MCP Server — Tool Manifest

> The agent-facing API of the OSIRIS intelligence backend. It exposes the same fused
> entities, knowledge graph, Iceberg history, and LLM that the human WebGL map consumes —
> as typed MCP **tools**, **resources**, and **prompts** — so an in-enclave agent can
> query, reason, and (under guardrails) act, while every result lands back on the
> analyst's screen with provenance.
>
> Companion to [`osiris-knowledge-graph-schema.md`](./osiris-knowledge-graph-schema.md).
> Node/edge labels and identifier keys referenced here are defined there.

---

## 1. Design principles

1. **Read first, act later.** The default surface is read-only query tools. Anything that
   mutates state (`create_standing_query`, future tasking) is **gated-write**: disabled
   by default, requires explicit human approval per call.
2. **Provenance on every output.** Every tool result carries the source/confidence/
   classification envelope (§3). An agent can never receive a fact it cannot attribute.
3. **The tool boundary is the security perimeter.** Authorization and classification
   enforcement happen here, not in the model. The model only sees what the boundary lets
   through.
4. **Grounded, not generative.** Tools return structured graph/Iceberg data. Where natural
   language becomes a query (`query_graph`), it passes through a validator/allowlist
   before execution.
5. **Dual-use by default.** Result-bearing tools also return a `view` deep link / map
   state so the human UI can render exactly what the agent reasoned over.
6. **Everything is audited.** Each call logs caller, inputs, classification touched, and
   result digest.

---

## 2. Server placement

- Runs as a container in the OSIRIS compose stack, **inside the enclave**, in front of the
  graph DB (Neo4j / JanusGraph), the Iceberg catalog, the feed routes, and the Mistral
  LLM. Reuses `osiris-net`.
- The "agent" in a true air gap is the **local** Mistral model via an MCP-speaking harness,
  not a cloud model. The MCP server standardizes that local agent's reach into OSIRIS.
- Transport: stdio for a co-located agent, or streamable HTTP for in-enclave network
  clients. No egress.

---

## 3. Conventions

### Common result envelope

Every tool returns `structuredContent` shaped as:

```json
{
  "data": { },                       // tool-specific payload
  "provenance": [
    {
      "entityRef": "osiris-sea-<mmsi>",
      "provider": "osiris",
      "feed": "maritime-ais",
      "confidence": 0.85,
      "classification": "UNCLASSIFIED",
      "derivedBy": "structural",      // structural | computed | extracted
      "observedAt": "2026-06-25T12:00:00Z"
    }
  ],
  "classification": "UNCLASSIFIED",   // max over contributing sources
  "view": {                            // optional, for the human UI
    "type": "map_state",
    "deepLink": "/?focus=osiris-sea-<mmsi>&bbox=...",
    "highlight": ["osiris-sea-<mmsi>"]
  },
  "truncated": false
}
```

- **`classification`** is the maximum classification of all contributing elements. The
  caller's clearance is checked against it at the boundary; over-clearance results are
  filtered, not returned.
- **`view`** lets an agent investigation drive the human map (the dual-use loop).

### Access tags

| Tag | Meaning |
|---|---|
| `read` | Side-effect-free query. Enabled by default. |
| `gated-write` | Mutates state. Disabled unless explicitly enabled; requires per-call human approval. |

### Errors

Structured: `{ "error": { "code": "FORBIDDEN_CLASSIFICATION" \| "INVALID_QUERY" \| "NOT_FOUND" \| "RATE_LIMITED", "message": "..." } }`. No partial classified leakage in error text.

---

## 4. Tool catalog

### 4.1 Entity & graph

#### `find_entity` — `read`
Resolve a name/identifier to canonical graph node(s).
```jsonc
// input
{ "query": "string",                  // name, callsign, mmsi, icao24, cve, etc.
  "labels": ["Vessel","Aircraft","Organization", "..."],  // optional filter
  "limit": 10 }
// output.data
{ "matches": [ { "canonicalId": "...", "label": "Vessel", "name": "...",
                 "identifiers": { "mmsi": "..." }, "score": 0.97 } ] }
```

#### `get_entity` — `read`
Full node record + immediate provenance, last-known position, attached classification.
```jsonc
{ "canonicalId": "string", "includeNeighbors": false }
// output.data
{ "node": { "label": "Vessel", "name": "...", "properties": { },
            "lastLat": 0, "lastLng": 0, "lastPositionAt": "..." },
  "neighbors": [ ] }
```

#### `neighbors` — `read`
One-hop relationships of a node, optionally filtered by edge type.
```jsonc
{ "canonicalId": "string", "edgeTypes": ["OPERATED_BY","FLAGGED_TO"], "limit": 50 }
// output.data
{ "edges": [ { "type": "OPERATED_BY", "to": { "canonicalId":"...","label":"Organization","name":"..." },
               "validFrom":"...", "derivedBy":"structural" } ] }
```

#### `path_between` — `read`
Shortest/constrained path between two entities (multi-hop reasoning).
```jsonc
{ "fromId": "string", "toId": "string", "maxHops": 4,
  "edgeTypes": ["OPERATED_BY","SUPPLIES","FLAGGED_TO"] }  // optional
// output.data
{ "paths": [ { "length": 3, "nodes": [ ], "edges": [ ] } ] }
```

#### `query_graph` — `read` (validated)
Parameterized graph query. Accepts either a **named template** (preferred) or a natural-
language question that the server translates → Cypher/Gremlin **behind a validator**
(allowlisted patterns, read-only, bounded). Never executes raw model-authored mutations.
```jsonc
{ "template": "vessels_near_chokepoint",        // OR
  "nl": "sanctioned operators whose vessels entered a chokepoint this week",
  "params": { "windowDays": 7 }, "limit": 100 }
// output.data
{ "rows": [ ], "queryExecuted": "MATCH ... RETURN ...", "templateUsed": "..." }
```

### 4.2 Geospatial & temporal

#### `entities_near` — `read`
Durable entities and active events within a radius/time window.
```jsonc
{ "lat": 0, "lng": 0, "radiusKm": 50, "since": "PT24H",
  "labels": ["Vessel","Event:Fire"], "minThreat": "ELEVATED" }
// output.data
{ "entities": [ { "canonicalId":"...","label":"...","distanceKm":12.4,"threatLevel":"..." } ] }
```

#### `whats_happening` — `read`
Event + threat summary for a bounding box and window (the "area brief" primitive).
```jsonc
{ "bbox": { "north":0,"south":0,"east":0,"west":0 }, "window": "PT6H" }
// output.data
{ "eventCounts": { "Seismic": 2, "Fire": 11, "Gdelt": 4 },
  "topThreats": [ ], "summary": "string" }   // summary LLM-generated, graph-grounded
```

#### `pattern_of_life` — `read`
Historical track/behavior of one entity from the Iceberg time-series.
```jsonc
{ "canonicalId": "string", "from": "...", "to": "...", "resolution": "1h" }
// output.data
{ "track": [ { "t":"...","lat":0,"lng":0,"speed":0 } ],
  "dwellSites": [ { "near":"Chokepoint:...","fromT":"...","toT":"..." } ] }
```

### 4.3 Feeds

#### `list_feeds` — `read`
Catalog of live feeds, their domains, and freshness.
```jsonc
{} // -> { "feeds": [ { "feed":"maritime-ais","domain":"SEA","lastUpdate":"...","entityCount":N } ] }
```

#### `get_feed_snapshot` — `read`
Current raw entities for one feed (Polybolos shape). Bounded by `limit`.
```jsonc
{ "feed": "maritime-ais", "limit": 200, "bbox": { } }   // bbox optional
```

### 4.4 Analytical (LLM-backed, graph-grounded)

#### `summarize_area` — `read`
Narrative situational summary for a region, grounded in `whats_happening` + graph context,
returned with citations in `provenance`.
```jsonc
{ "bbox": { }, "window": "PT12H", "focus": "maritime" }   // focus optional
```

#### `entity_dossier` — `read`
Compiled profile of one durable entity: identity, relationships, recent events, and a
grounded narrative. (The `vessel_dossier` demo workflow generalizes here.)
```jsonc
{ "canonicalId": "string", "depth": 2 }
// output.data
{ "node": { }, "relationships": [ ], "recentEvents": [ ], "narrative": "string" }
```

### 4.5 Alerting

#### `list_alerts` — `read`
Currently firing standing-query alerts.
```jsonc
{ "minSeverity": "ELEVATED" } // -> { "alerts": [ { "id":"...","pattern":"...","entities":[ ],"firedAt":"..." } ] }
```

#### `create_standing_query` — `gated-write`
Register a graph pattern that fires alerts when matched. **Requires human approval per
call.** Mutates server state; bounded in count/complexity.
```jsonc
{ "name": "sanctioned_vessel_in_chokepoint",
  "template": "vessels_near_chokepoint",
  "params": { "sanctionedOnly": true }, "severity": "HIGH" }
// output.data -> { "queryId": "...", "status": "pending_approval" }
```

> Future gated-write tools (collection tasking, annotation write-back) follow the same
> approval + audit pattern. None ship in the initial read-only release.

---

## 5. Resources (read-only context the agent can pull)

| URI | Content |
|---|---|
| `osiris://schema/knowledge-graph` | The node/edge schema (this manifest's companion doc). |
| `osiris://policy/classification` | Classification levels and propagation rules. |
| `osiris://entity/{canonicalId}` | Live record for one entity (resource form of `get_entity`). |
| `osiris://feeds/catalog` | Feed catalog (resource form of `list_feeds`). |

---

## 6. Prompts (pre-built analyst workflows)

| Prompt | Arguments | Expands to |
|---|---|---|
| `area_brief` | `bbox`, `window` | `whats_happening` → `summarize_area`, returns a cited brief + map view. |
| `track_sanctioned_vessel` | `vesselRef` | `find_entity` → `entity_dossier` → `query_graph(vessels_near_chokepoint)`; the demo thread as one call. |
| `entity_investigation` | `query` | `find_entity` → `neighbors` → `path_between` against watchlist, with provenance. |

---

## 7. Security & authorization model

1. **Caller identity + clearance** established at connect; every call is authorized
   against the result's `classification`. Over-clearance elements are filtered out before
   return, never partially leaked in errors.
2. **Classification propagation** mirrors the graph schema: results inherit the maximum
   classification of contributing nodes/edges.
3. **NL→query validation** (`query_graph`): model-authored queries are constrained to an
   allowlist of read-only templates/patterns, bounded in hops and result size; raw
   mutations are rejected. Prefer named templates over free-form NL.
4. **Gated-write approval**: `gated-write` tools are disabled by default and require a
   per-call human approval step surfaced in the UI; nothing executes autonomously.
5. **Audit log**: caller, tool, inputs, classification touched, result digest, and (for
   `query_graph`) the executed query — written for every call. The audit trail is a
   deliverable feature in the defense context, not overhead.
6. **No egress**: the server makes no outbound connections beyond the enclave backend.

---

## 8. Build order

1. **Read-only core**: `find_entity`, `get_entity`, `neighbors`, `entities_near`,
   `list_feeds`, `get_feed_snapshot` + the provenance envelope and authz boundary.
2. **Graph reasoning**: `path_between`, `query_graph` (named templates first, NL behind
   the validator second).
3. **Analytical**: `summarize_area`, `entity_dossier`; wire the `view` deep links into the
   human UI to close the dual-use loop.
4. **Temporal**: `pattern_of_life` over Iceberg.
5. **Alerting**: `list_alerts`, then `create_standing_query` as the first **gated-write**
   tool with the approval flow.

Stages 1–2 make OSIRIS agent-queryable; stage 3 makes the agent's work visible to the
analyst; stages 4–5 add history and autonomy under guardrails.

> **Dependency:** the high-value tools (`query_graph`, `path_between`, `entity_dossier`)
> are graph queries — stand up the knowledge graph first, or the MCP server is only
> wrapping raw feeds.
