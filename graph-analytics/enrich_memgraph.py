#!/usr/bin/env python3
"""Stage 3 — write the Spark metrics back onto the live graph.

Reads the metrics the GraphFrames job produced and SETs pagerank / community /
degree as properties on the matching Memgraph nodes, through the same HTTP shim
(POST /cypher). After this runs, OSIRIS's own graph panel and the MCP tools see
the analytics with no schema change — nodes just gained three properties.

Nodes are matched by Memgraph's internal id (the id exported in stage 1). Run
this in the SAME cycle as the export/analyze steps; do not reload Memgraph in
between (ids would shift). See the runbook for the stable-key hardening.

Env:
    MEMGRAPH_URL        base of the shim (POST /cypher)
    MEMGRAPH_API_TOKEN  optional bearer token
    METRICS_CSV         file OR directory of part-*.csv (default ./graph-export/metrics.csv_spark)
    BATCH_SIZE          nodes per UNWIND write (default 2000)
"""
from __future__ import annotations

import csv
import glob
import os
import sys
import time

import httpx

MEMGRAPH_URL = os.environ.get("MEMGRAPH_URL", "http://localhost:8090").rstrip("/")
MEMGRAPH_API_TOKEN = os.environ.get("MEMGRAPH_API_TOKEN")
METRICS_CSV = os.environ.get("METRICS_CSV", "./graph-export/metrics.csv_spark")
BATCH_SIZE = int(os.environ.get("BATCH_SIZE", "2000"))
TIMEOUT = float(os.environ.get("ENRICH_TIMEOUT", "120"))

SET_QUERY = (
    "UNWIND $rows AS row "
    "MATCH (n) WHERE id(n) = row.id "
    "SET n.pagerank = row.pagerank, n.community = row.community, n.degree = row.degree"
)


def _headers() -> dict:
    return {"Authorization": f"Bearer {MEMGRAPH_API_TOKEN}"} if MEMGRAPH_API_TOKEN else {}


def _csv_paths(path: str) -> list[str]:
    if os.path.isdir(path):
        return sorted(glob.glob(os.path.join(path, "*.csv")))
    return [path]


def _load_rows(path: str) -> list[dict]:
    rows: list[dict] = []
    for p in _csv_paths(path):
        with open(p, encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                try:
                    rows.append({
                        "id": int(r["node_id"]),
                        "pagerank": float(r.get("pagerank") or 0.0),
                        "community": (r.get("community") or None),
                        "degree": int(float(r.get("degree") or 0)),
                    })
                except (KeyError, ValueError):
                    continue
    return rows


def main() -> int:
    started = time.monotonic()
    rows = _load_rows(METRICS_CSV)
    if not rows:
        print(f"no metrics rows found at {METRICS_CSV}", file=sys.stderr)
        return 2
    print(f"enriching {len(rows):,} nodes in batches of {BATCH_SIZE} ...", flush=True)

    done = 0
    with httpx.Client(timeout=TIMEOUT) as client:
        for i in range(0, len(rows), BATCH_SIZE):
            batch = rows[i:i + BATCH_SIZE]
            r = client.post(
                f"{MEMGRAPH_URL}/cypher",
                json={"query": SET_QUERY, "params": {"rows": batch}},
                headers=_headers(),
            )
            r.raise_for_status()
            done += len(batch)
            print(f"  {done:,}/{len(rows):,}", flush=True)

    print(f"Done: enriched {done:,} nodes in {time.monotonic() - started:.1f}s", flush=True)
    print("OSIRIS graph nodes now carry .pagerank / .community / .degree", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
