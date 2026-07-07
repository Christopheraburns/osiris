#!/usr/bin/env python3
"""Stage 1 — export the OSIRIS knowledge graph (Memgraph) to flat files for Spark.

The Spark 3 Data Hub cannot reach Memgraph's Bolt port cross-project, but it can
read files from the data-lake bucket. So we page the graph out through the same
HTTP shim the UI uses (POST /cypher) and write two CSVs that GraphFrames consumes:

    nodes.csv   id,label,name
    edges.csv   src,dst,rel

`id` is Memgraph's internal node id — stable *within one snapshot*. Run
export → analyze → enrich as one cycle and do NOT reload Memgraph in between
(the enrichment step matches nodes back by this same id). For a production
pipeline, swap in a stable business key; see the runbook.

Env:
    MEMGRAPH_URL        base of the shim, e.g. http://osiris-graphdb.<ns>:8090  (POST /cypher)
    MEMGRAPH_API_TOKEN  optional bearer token for the shim
    EXPORT_DIR          output dir — local path or s3://bucket/prefix (default ./graph-export)
    PAGE_SIZE           rows per Cypher page (default 50000)
"""
from __future__ import annotations

import csv
import io
import os
import sys
import time

import httpx

MEMGRAPH_URL = os.environ.get("MEMGRAPH_URL", "http://localhost:8090").rstrip("/")
MEMGRAPH_API_TOKEN = os.environ.get("MEMGRAPH_API_TOKEN")
EXPORT_DIR = os.environ.get("EXPORT_DIR", "./graph-export").rstrip("/")
PAGE_SIZE = int(os.environ.get("PAGE_SIZE", "50000"))
TIMEOUT = float(os.environ.get("EXPORT_TIMEOUT", "120"))


def _headers() -> dict:
    return {"Authorization": f"Bearer {MEMGRAPH_API_TOKEN}"} if MEMGRAPH_API_TOKEN else {}


def _cypher(client: httpx.Client, query: str, params: dict | None = None) -> list[dict]:
    r = client.post(f"{MEMGRAPH_URL}/cypher", json={"query": query, "params": params or {}}, headers=_headers())
    r.raise_for_status()
    data = r.json()
    # the shim returns {"rows":[...]} or a bare list — accept either
    if isinstance(data, dict):
        for v in data.values():
            if isinstance(v, list):
                return v
        return []
    return data if isinstance(data, list) else []


def _page(client: httpx.Client, base_query: str, label: str) -> list[dict]:
    """Run `base_query` with $skip/$limit until it stops returning rows."""
    out: list[dict] = []
    skip = 0
    while True:
        rows = _cypher(client, base_query, {"skip": skip, "limit": PAGE_SIZE})
        if not rows:
            break
        out.extend(rows)
        print(f"  {label}: {len(out):,}", flush=True)
        if len(rows) < PAGE_SIZE:
            break
        skip += PAGE_SIZE
    return out


def _write_csv(path: str, fieldnames: list[str], rows: list[dict]) -> None:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    w.writeheader()
    for row in rows:
        w.writerow(row)
    payload = buf.getvalue()

    if path.startswith("s3://"):
        import boto3  # lazy — only needed for the s3 path

        _, _, rest = path.partition("s3://")
        bucket, _, key = rest.partition("/")
        boto3.client("s3").put_object(Bucket=bucket, Key=key, Body=payload.encode("utf-8"))
    else:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(payload)
    print(f"  wrote {path}  ({len(rows):,} rows)", flush=True)


def main() -> int:
    started = time.monotonic()
    node_q = (
        "MATCH (n) RETURN id(n) AS id, labels(n)[0] AS label, "
        "coalesce(n.name, n.callsign, n.imo, n.icao, toString(id(n))) AS name "
        "ORDER BY id(n) SKIP $skip LIMIT $limit"
    )
    edge_q = (
        "MATCH (a)-[r]->(b) RETURN id(a) AS src, id(b) AS dst, type(r) AS rel "
        "ORDER BY id(a) SKIP $skip LIMIT $limit"
    )

    with httpx.Client(timeout=TIMEOUT) as client:
        print("Exporting nodes ...", flush=True)
        nodes = _page(client, node_q, "nodes")
        print("Exporting edges ...", flush=True)
        edges = _page(client, edge_q, "edges")

    _write_csv(f"{EXPORT_DIR}/nodes.csv", ["id", "label", "name"], nodes)
    _write_csv(f"{EXPORT_DIR}/edges.csv", ["src", "dst", "rel"], edges)

    print(f"Done: {len(nodes):,} nodes, {len(edges):,} edges in "
          f"{time.monotonic() - started:.1f}s → {EXPORT_DIR}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
