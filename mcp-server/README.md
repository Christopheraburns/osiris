# OSIRIS MCP Server

A read-only **MCP server** that encapsulates the OSIRIS FeedsGateway + GraphDB APIs
as tools an agent (Cloudera Agent Studio, or any MCP client) can call. It's a thin
façade — no business logic, no state — so it's safe to run **alongside human
operators**. The point: demonstrate MCP + Agentic AI working next to the operators
on the same live picture.

## What it exposes (v1 — read-only)

| Tool | Backend it calls | Use |
|---|---|---|
| `health` | gateway `/health`, graph `/health` | rollup status |
| `list_feeds` | gateway `/feeds` | which live feeds exist |
| `get_feed(name, limit)` | gateway `/feeds/{name}` | any feed snapshot |
| `get_flights(callsign?, icao24?, squawk?)` | gateway `/feeds/flights` | flights, filtered (e.g. squawk 7700) |
| `get_vessels(mmsi?, name?)` | gateway `/feeds/vessels` | vessels, filtered |
| `resolve_entity(kind, id, secure?)` | osiris-intel `/resolve` | operator/owner/flag/sanctions |
| `query_graph(cypher, params?)` | Memgraph shim `POST /cypher` | **read-only** Cypher |
| `graph_neighborhood(icao?, name?)` | shim `/cypher` | canned org neighborhood |
| `history_bounds` | gateway `/history/bounds` | datalake time extent |
| `pattern_of_life(entity_type, …, window_hours)` | gateway `/intel/pattern-of-life` | fused graph + lake narrative |

Writes are deliberately out of scope in v1; `query_graph` rejects mutating Cypher.

## Deploy as a CAI Application (Osiris Prime)

1. Git-pull the repo into the project; the Application script is `mcp-server/launch_mcp.py`.
2. Bind is automatic (`127.0.0.1:CDSW_APP_PORT`); the server serves `GET /` for the
   CAI health check and the MCP endpoint at **`/mcp`**.
3. Enable **Unauthenticated Access** (MCP clients send no Authorization header).
4. Set env vars:
   - `FEEDS_GATEWAY_URL` — e.g. `https://feeds-gateway.<suffix>` (no trailing slash)
   - `MEMGRAPH_URL` — the Memgraph shim URL
   - `INTEL_URL` — the osiris-intel resolver URL
   - `MEMGRAPH_API_TOKEN` *(optional)* — bearer token for the shim
   - `SECURE_MODE_DEFAULT` *(default `true`)* — resolve against the air-gapped graph

## Point an agent at it

In **Cloudera Agent Studio**, register a remote MCP server with the URL
`https://<this-app-subdomain>/mcp` (streamable-HTTP). The agent then has the tool
surface above to monitor OSIRIS — e.g. watch for emergency squawks, enrich the
operator behind a track, or pull a pattern-of-life brief — reporting alongside the
human watch.

## Secure Mode

`resolve_entity` defaults to the air-gapped graph (`secure=1`); the legacy public
path is still reachable via `secure=false`. The migration seam is the single
`SECURE_MODE_DEFAULT` env var — flip it to enforce, drop the fallback later.

## Local test

```bash
pip install -r requirements.txt
CDSW_APP_PORT=8092 FEEDS_GATEWAY_URL=http://localhost:8091 \
  MEMGRAPH_URL=http://localhost:8090 INTEL_URL=http://localhost:4000 \
  python launch_mcp.py
# MCP endpoint: http://127.0.0.1:8092/mcp   (health: GET http://127.0.0.1:8092/)
```
