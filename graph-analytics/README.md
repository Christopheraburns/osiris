# OSIRIS · graph-analytics

Distributed graph analytics (Spark GraphFrames) for OSIRIS, and the pieces that
surface it alongside the live picture. Memgraph is the real-time **serving** graph;
this is the **analytics** tier.

Pipeline (one cycle — don't reload Memgraph in between):

1. `export_graph.py`  — page Memgraph (POST /cypher) → `nodes.csv` / `edges.csv` on the lake.
2. `graph_analytics_job.py` — **Spark 3 Data Hub**: PageRank + connectedComponents +
   labelPropagation → Iceberg table `osiris.graph_metrics` (+ a flat metrics CSV).
3. `enrich_memgraph.py` — SET `pagerank`/`community`/`degree` back onto the graph nodes.
4. View:
   - `viewer/app.py` — companion CAI Application (central table + community force-graph).
   - MCP tools `central_entities` / `network_clusters` in `../mcp-server/server.py`.

`requirements.txt` covers the non-Spark pieces (export/enrich/viewer). The Spark job
uses the Data Hub's `pyspark`; GraphFrames comes via `--packages`/`--jars`.

Full runbook (Data Hub provisioning, Ranger grants, air-gap staging, demo script):
**`Cloudera CAI/OSIRIS-Graph-Analytics-Plan.md`**.
