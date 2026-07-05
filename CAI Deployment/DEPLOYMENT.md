# OSIRIS — Deployment / Redeploy Runbook

Step-by-step to stand up OSIRIS from scratch in a **new CAI environment**. Pairs
with `APPLICATIONS.md` (per-app settings) and `README_architecture.md` (overview).

Audience: someone rebuilding OSIRIS in their own CAI workbench. Assumes CDP Public
Cloud (AWS) with a CAI workbench provisioned. Where a value is environment-specific,
it's marked `<...>`.

---

## 0. Prerequisites

- A CAI workbench on CDP Public Cloud. Validated shape: Default CPU Resource Group
  = 2× `m5.4xlarge` (16 vCPU / 64 GiB), autoscale 1–5. Infra nodes `m5.2xlarge`.
- **Workbench admin rights** (needed once, to register runtime images and a
  ≥16 GiB Resource Profile — see steps 1 and 3).
- Access to the repo: `https://github.com/christopheraburns/osiris`
- The custom runtime images (public on Docker Hub):
  - `christopheraburns/osiris-node-runtime:1.0.1`
  - `christopheraburns/osiris-memgraph-runtime:1.0.1`
- **Reference data artifacts.** Two delivery paths (see step 5):
  - **Preferred — a Memgraph snapshot** (the "base graph image"): a tarball of a
    loaded graph. Restoring it skips the multi-hour load entirely. Keep this
    archived; it is the fastest redeploy path. <!-- FILL IN: archive location. -->
  - **Fallback — the raw Wikidata extract** `nohumans.ndjson.gz` (~500 MB, ~883k
    entities), uploaded into a Session (too large for plain git). Delivery options:
    upload by hand; regenerate from the 150 GB Wikidata dump via the filter
    pipeline; **Git LFS** (convenient for connected redeploys, but GitHub free-tier
    LFS is 1 GB storage / 1 GB-mo bandwidth — tight for a 500 MB file — and LFS
    pulls from github.com, so it does **not** satisfy an air-gapped target; stage
    locally there). <!-- FILL IN: chosen mechanism for your environments. -->

---

## 1. Register the custom runtime images (admin, once per workbench)

CAI Applications can only select runtimes registered in the workbench catalog.

1. Site Administration → **Runtime Catalog** (or **Runtimes**).
2. **Add Runtime** → pull each image by full tag:
   - `christopheraburns/osiris-node-runtime:1.0.1`
   - `christopheraburns/osiris-memgraph-runtime:1.0.1`
3. Confirm both appear as selectable runtimes.

<!-- FILL IN: if your environment cannot pull from Docker Hub (air-gapped),
mirror both images into the internal registry first and register those refs. -->

---

## 2. Register a ≥16 GiB Resource Profile (admin, once per workbench)

Memgraph is in-memory and needs ≥16 GiB (see the OOM warning in `APPLICATIONS.md`).
The per-Application profile dropdown only offers profiles defined here.

1. Site Administration → **Runtime/Engine Profiles** (label varies by version).
2. Add a profile: **4 vCPU / 16 GiB** (and optionally **8 vCPU / 32 GiB** for
   growth). Must fit within one node of the resource group (`m5.4xlarge` →
   16 vCPU / 64 GiB, so both are valid).
3. Save; the new size now appears in every Application's profile dropdown.

---

## 3. Create the two CAI projects

Both sync from the **same repo**; each Application uses `/home/cdsw` as its working
dir, so the repo is pulled to `/home/cdsw`.

1. **Osiris GraphDB** project
   - New Project → Git → `https://github.com/christopheraburns/osiris` (branch `main`).
   - Runtime: the **memgraph** runtime from step 1.
2. **Osiris Prime** project
   - New Project → Git → same repo, branch `main`.
   - Runtime: the **node** runtime from step 1.

---

## 4. Stand up the Memgraph Application (Osiris GraphDB)

Do this first — the app tier depends on it.

1. In **Osiris GraphDB** → **Applications** → **New Application**.
2. Name: `Memgraph`. Subdomain: `graphdb`.
3. Script: `/home/cdsw/app.py`
4. Runtime: `osiris-memgraph-runtime:1.0.1`.
5. **Resource Profile: the ≥16 GiB profile** from step 2.
6. **Unauthenticated Access: ON.**
7. Env vars: none (leave `MEMGRAPH_API_TOKEN` unset for anonymous `/cypher`).
8. Start. Verify:
   ```
   curl -s https://graphdb.<domain>/health          # -> {"ok":true}
   curl -s https://graphdb.<domain>/cypher -H 'Content-Type: application/json' \
     -d '{"query":"MATCH (n) RETURN count(n) AS n"}'  # -> 0 on a fresh DB
   ```

---

## 5. Populate the graph backbone

Two paths. **Prefer 5A (snapshot restore)** — it turns a multi-hour load into a
minutes-long file copy. Use 5B only if you have no snapshot.

### 5A. Restore from a Memgraph snapshot (fast — preferred)

1. Before first Memgraph start, place the archived snapshot into the durability
   mount:
   ```
   # in a Session on Osiris GraphDB, with the base-image tarball staged
   mkdir -p /home/cdsw/memgraph-data
   tar xzf osiris-graph-base-<date>.tgz -C /home/cdsw/memgraph-data
   ls /home/cdsw/memgraph-data/snapshots/   # confirm snapshot present
   ```
2. Start (or restart) the Memgraph Application — it reloads the graph from the
   snapshot on boot. Verify with the count queries in 5B step 4.

> Snapshots are **Memgraph-version-specific** — restore into the same runtime
> image (`osiris-memgraph-runtime:1.0.1`) the snapshot was taken with.

#### Creating the snapshot (do this once you have a loaded graph)
```
# on-demand snapshot via the shim (no restart needed)
curl -s https://graphdb.<domain>/cypher -H 'Content-Type: application/json' \
  -d '{"query":"CREATE SNAPSHOT;"}'
# archive it as the base image
tar czf /home/cdsw/osiris-graph-base-$(date +%Y%m%d).tgz \
  -C /home/cdsw/memgraph-data snapshots
```
Keep a "backbone-only" snapshot and, later, a "backbone+sanctions" snapshot as
separate restore points. Store the tarball with your deployment assets.

### 5B. Load from the raw extract (slow — fallback)

The long pole and the most easily-omitted part of a redeploy. Run from a
**Session in the Osiris GraphDB project** (same filesystem as the data + Memgraph).

1. Stage the Wikidata extract `nohumans.ndjson.gz` (~500 MB, ~883k entities) into
   the project (uploaded via Session — too large for plain git; see step 0).
2. **Project** it to graph-ready NDJSON:
   ```
   python3 project.py --in nohumans.ndjson.gz --out graph.ndjson
   wc -l graph.ndjson        # ~883k
   ```
3. **Load** it via the shim (indexes → nodes → edges; idempotent, resumable):
   ```
   env -u HTTPS_PROXY -u https_proxy -u HTTP_PROXY -u http_proxy \
     nohup python3 load.py --url https://graphdb.<domain>/cypher \
       --in graph.ndjson > /home/cdsw/edgeload.log 2>&1 &
   tail -f /home/cdsw/edgeload.log
   ```
4. **Verify** (expected: ~949k nodes incl. stubs, ~1.3M edges):
   ```
   curl -s https://graphdb.<domain>/cypher -H 'Content-Type: application/json' \
     -d '{"query":"MATCH (n) RETURN labels(n)[0] AS label, count(*) AS n ORDER BY n DESC"}'
   curl -s https://graphdb.<domain>/cypher -H 'Content-Type: application/json' \
     -d '{"query":"MATCH ()-[r]->() RETURN type(r) AS rel, count(*) AS n ORDER BY n DESC"}'
   ```

> **Load gotchas (all learned the hard way — see the scripts' comments):**
> - Create indexes **before** loading nodes, or MERGEs go quadratic.
> - Edge-source MATCHes must be **labeled** (`MATCH (a:Organization {qid})`) — a
>   label-less match ignores per-label indexes and stalls (~6k edges then crawls).
> - Internal-subdomain DNS is flaky under sustained load; the loader uses a pooled
>   session + retry/backoff and is idempotent — **just re-run `--edges-only` /
>   `--nodes-only` until counts stop growing.**
> - Everything is `MERGE`, so re-runs converge with no duplicates.

> **Faster redeploy option:** instead of re-loading from scratch, restore a
> Memgraph **snapshot** into `/home/cdsw/memgraph-data` before first start — the
> app reloads the graph from the snapshot on boot. <!-- FILL IN: where the
> canonical snapshot is archived, if you keep one. Strongly recommended to keep
> one, to avoid repeating the multi-hour HTTP load. -->

---

## 6. Stand up the Osiris Prime Applications

Create each as a CAI Application in **Osiris Prime**. All: Unauthenticated Access
**ON**, node runtime, bind `127.0.0.1` on `CDSW_APP_PORT`, serve `GET /`. See
`APPLICATIONS.md` for full per-app detail; summary here.

Order matters only in that **OSIRIS-UI depends on the other three's subdomains**,
so create UI last (or just set its env vars once the others have subdomains).

1. **OSIRIS-Tile-Server** — subdomain `osiris-tileserver`, script
   `/home/cdsw/tileserver/serve-tiles.py`, env
   `TILESERVER_PUBLIC_URL=https://osiris-tileserver.<domain>/` (trailing slash).
   **Verify tile-data source** in a Session (not manually uploaded — it's either in
   the repo or baked into the runtime image):
   ```
   ls -lh /home/cdsw/tileserver/
   find /home/cdsw/tileserver -name '*.mbtiles' -o -name '*.pmtiles' 2>/dev/null
   git -C /home/cdsw ls-files | grep -iE 'tiles|mbtiles' | head
   cat /home/cdsw/tileserver/config.json
   ```
   If the tiles are git-tracked, they arrive with the repo pull. If present but
   untracked, they're baked into `osiris-node-runtime` and require that image.
   A blank map on redeploy = missing tile data. <!-- FILL IN once confirmed. -->
2. **OSIRIS-Intel-Server** — subdomain `osiris-intel`, script
   `/home/cdsw/intel/launch_intel_resolver.py`. Launcher must export
   `INTEL_PORT=CDSW_APP_PORT`. Egress: `data.opensanctions.org`, `query.wikidata.org`.
3. **OSIRIS-Feeds-Gateway** — subdomain `feeds-gateway`, no env vars. Requires the
   NiFi→Kafka pipeline for live data (see step 7).
4. **OSIRIS-UI** — subdomain `osiris`, `next start`. Env vars (mind trailing
   slashes — see `APPLICATIONS.md`):
   - `INTEL_URL=https://osiris-intel.<domain>` (no slash)
   - `FEEDS_GATEWAY_URL=https://feeds-gateway.<domain>` (no slash)
   - `TILESERVER_PUBLIC_URL=https://osiris-tileserver.<domain>/` (slash)

Verify each: `curl https://<prefix>.<domain>/health` (or `/` for the UI), then
open the UI and confirm the map (tiles), feeds, and Intel Deep Dive resolve.

---

## 7. Event pipeline (NiFi / Kafka / Flink / Iceberg)  <!-- FILL IN -->

The feeds-gateway serves event data produced by the NiFi→Kafka pipeline. Standing
that up in a new environment:

<!-- FILL IN: this section needs the CDP DataFlow / Streams Messaging / Streaming
Analytics deployment steps — which are in other Cloudera products, not CAI, and
weren't covered in the CAI-app work. Document: NiFi flow import, Kafka topic
`osiris-events` creation, and (once working) the Flink→Iceberg recorder. Until
then the UI shows the static maritime layer + whatever feeds are wired. -->

---

## 8. Known-remaining work (not yet in the deployed system)

- **Repoint osiris-intel to Memgraph** — resolvers currently query public Wikidata
  SPARQL + OpenSanctions; the air-gap target rewrites them to Cypher against the
  `/cypher` shim (keyed on `imo`/`name`/`icao`). Backbone already carries the keys.
- **OFAC sanctions load** into Memgraph (nodes + join to Wikidata by name/IMO).
- **recorder → Iceberg** (Flink job) — not yet deployed.
- **GPU node group + AI Inference Service** (Nemotron NIM) — the current workbench
  has **no GPU group**; add a `g5.xlarge`/`g6.xlarge` group before that work.

---

## Quick redeploy checklist

- [ ] Admin: register both runtime images (step 1)
- [ ] Admin: register ≥16 GiB Resource Profile (step 2)
- [ ] Create Osiris GraphDB + Osiris Prime projects from the repo (step 3)
- [ ] Memgraph Application up, ≥16 GiB, `/health` green (step 4)
- [ ] Backbone loaded OR snapshot restored; node/edge counts verified (step 5)
- [ ] Tile / Intel / Gateway / UI Applications up, env vars + slashes correct (step 6)
- [ ] Event pipeline (NiFi/Kafka) — env-specific (step 7)
- [ ] UI renders map + feeds + Intel Deep Dive
