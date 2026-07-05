# OSIRIS — CAI Application Reference

Per-Application deployment facts for all five OSIRIS CAI Applications. Use with
`DEPLOYMENT.md` (the step-by-step runbook) and `README_architecture.md` (the
system overview).

**Conventions**
- All Applications run from the CAI default working dir **`/home/cdsw`**; the
  single repo (`github.com/christopheraburns/osiris`) is pulled there, so each
  Application's script path is `/home/cdsw/<subdir>/...`.
- **Unauthenticated Access is ON for every Application** (demo posture — the
  cross-service proxy calls send no auth header, so this is required, not optional).
- Subdomain host pattern: `<prefix>.intsrv.se-sandb.a465-9q4k.cloudera.site`
  — the `<prefix>` column below is the per-Application subdomain.
  <!-- FILL IN: this CDP domain suffix changes per environment; in a new
       environment replace `intsrv.se-sandb.a465-9q4k.cloudera.site` throughout -->
- Every Application binds **`127.0.0.1`** on **`CDSW_APP_PORT`**. Binding
  `0.0.0.0` presents as a silent ~30-minute "Starting" hang (CAI routes to
  loopback). This is the single most important cross-cutting gotcha.

---

## Project: **Osiris GraphDB**

### Memgraph  (subdomain: `graphdb`)
| Field | Value |
|---|---|
| Script | `/home/cdsw/app.py` |
| Runtime image | `christopheraburns/osiris-memgraph-runtime:1.0.1` (Memgraph in PBJ Python 3.11) |
| Resource profile | **≥ 16 GiB** (validated at 4 vCPU / 16 GiB). **NOT 8 GB** — see warning below |
| Unauthenticated Access | ON |
| Env vars | none required (`MEMGRAPH_API_TOKEN` intentionally unset = anonymous `/cypher`) |
| Exposed surface | HTTP shim on `CDSW_APP_PORT`: `POST /cypher` (`{"query","params"}`), `GET /health` |
| Internal-only | Bolt on `127.0.0.1:7687` (never exposed through the subdomain) |
| Persistence | `--data-directory=/home/cdsw/memgraph-data`, snapshots + WAL. Survives restart/resize. |

> **⚠ Memory is not optional headroom — it's correctness.** Memgraph is
> in-memory-first and holds the entire graph (~949k nodes incl. stubs, ~1.3M
> edges, plus indexes) in RAM. At 8 GB it runs with almost no margin; the next
> data load (OFAC sanctions) will grow it. An in-memory DB that hits its ceiling
> is **OOM-killed** (Application dies/restarts, losing anything since the last
> snapshot) — it does not degrade gracefully. Size to ≥16 GiB before loading more.

> **Resizing:** the per-Application Resource Profile dropdown only offers profiles
> pre-defined at the workbench level (Site Administration → Runtime/Engine
> Profiles). If the size you need isn't listed, an admin must add it first. Max
> per profile is bounded by one node of the resource group (`m5.4xlarge` =
> 16 vCPU / 64 GiB here). After resizing, confirm the graph reloaded from snapshot:
> `MATCH (n) RETURN count(n)` should return the full count.

---

## Project: **Osiris Prime**

### OSIRIS-UI  (subdomain: `osiris`)
| Field | Value |
|---|---|
| Script | `/home/cdsw/entry.py` (launches `next start`, NOT standalone — standalone breaks static-asset chunking → ChunkLoadError) |
| Runtime image | `christopheraburns/osiris-node-runtime:1.0.1` |
| Resource profile | 4 vCPU / 8 GiB (demo default) |
| Unauthenticated Access | ON |
| Env vars | `FEEDS_GATEWAY_URL=https://feeds-gateway.<domain>` (no trailing slash) · `TILESERVER_PUBLIC_URL=https://osiris-tileserver.<domain>/` (**trailing slash**) · `INTEL_URL=https://osiris-intel.<domain>` (**no trailing slash**) |

> **⚠ Trailing-slash rules are per-variable and load-bearing.** `INTEL_URL` must
> have **no** trailing slash — the resolver appends `/resolve`, so a slash yields
> `//resolve` → 404. `TILESERVER_PUBLIC_URL` currently carries a trailing slash and
> works (its consumer tolerates it). `FEEDS_GATEWAY_URL` has none. Copy these exact
> forms; do not "normalize" them.

### OSIRIS-Intel-Server  (subdomain: `osiris-intel`)
| Field | Value |
|---|---|
| Script | `/home/cdsw/intel/launch_intel_resolver.py` (PBJ Python launcher; subprocess-launches `node server.js`) |
| Runtime image | `christopheraburns/osiris-node-runtime:1.0.1` |
| Resource profile | 4 vCPU / 8 GiB (demo default) |
| Unauthenticated Access | ON |
| Env vars | none required. Optional: `INTEL_RESOLVER_DIR` (defaults to `/home/cdsw/intel`), `INTEL_START_CMD` |
| Egress required | `data.opensanctions.org` (OFAC SDN CSV at startup) · `query.wikidata.org` (SPARQL per request) — *until repointed to Memgraph* |
| Endpoints | `GET /resolve?type=&id=`, `GET /health`, `GET /` (root — required for CAI readiness probe) |

> **⚠ Two gotchas baked into this one.** (1) `server.js` reads the port from
> **`INTEL_PORT`**, not `PORT` — the launcher must export `INTEL_PORT=CDSW_APP_PORT`
> or the app binds 4000 and CAI can't reach it. (2) `server.js` must bind
> `127.0.0.1` (not `0.0.0.0`) and must serve `GET /` — without a root route CAI's
> readiness probe fails and the app hangs in "Starting".

### OSIRIS-Feeds-Gateway  (subdomain: `feeds-gateway`)
| Field | Value |
|---|---|
| Script | `/home/cdsw/services/feeds-gateway/gateway_startup.py` |
| Runtime image | `christopheraburns/osiris-node-runtime:1.0.1` <!-- confirm: node vs python runtime --> |
| Resource profile | 4 vCPU / 8 GiB (demo default) |
| Unauthenticated Access | ON |
| Env vars | none |
| Endpoints | `GET /health` (lists active feeds), `GET /feeds/<feed>` |
| Consumes | Kafka topic `osiris-events` (entityType routing); serves reshaped event data to the UI |

### OSIRIS-Tile-Server  (subdomain: `osiris-tileserver`)
| Field | Value |
|---|---|
| Script | `/home/cdsw/tileserver/serve-tiles.py` (PBJ Python launcher; subprocess-launches `node .../tileserver-gl-light`) |
| Runtime image | `christopheraburns/osiris-node-runtime:1.0.1` |
| Resource profile | 4 vCPU / 8 GiB (demo default) |
| Unauthenticated Access | ON |
| Env vars | `TILESERVER_PUBLIC_URL=https://osiris-tileserver.<domain>/` (trailing slash, as deployed) |
| Launch args | `--no-cors` (avoids double-header conflict with CAI ingress), `--bind 127.0.0.1`, `--port $CDSW_APP_PORT`, `-c config.json` |
| Serves | Planetiler-generated OpenMapTiles vector tiles (self-hosted; no CARTO dependency) |

---

## Cross-cutting gotchas (apply when redeploying ANY Node/Python CAI Application)

1. **Bind `127.0.0.1`, never `0.0.0.0`.** CAI routes the subdomain to loopback.
   `0.0.0.0` → silent ~30-min "Starting" hang, no error.
2. **Serve `GET /`.** CAI's readiness probe hits the root; a 404 there can hang the
   app in "Starting". Add a trivial 200 root route.
3. **Port comes from `CDSW_APP_PORT`**, and it differs between a Session and an
   Application — never hardcode. (Intel's `INTEL_PORT` must be set to it.)
4. **URL env vars: mind the trailing slash** — required form is per-variable
   (see OSIRIS-UI). A wrong slash silently 404s downstream.
5. **Sustained HTTP to internal subdomains is flaky under load** (intermittent DNS
   `Failed to resolve`). Loaders/clients need a pooled session + generous retry
   with backoff; long jobs should run under `nohup` and be idempotent/resumable.
6. **`next start`, not standalone**, for the UI (standalone → `ChunkLoadError`).
