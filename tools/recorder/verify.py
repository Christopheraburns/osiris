"""Verification — count Iceberg rows + Neo4j nodes/edges for a run."""
from __future__ import annotations

import os
import sys

from catalog import EVENTS_TABLE, OBS_TABLE, RAW_TABLE, load_osiris_catalog


def iceberg_counts(run_id: str | None) -> None:
    cat = load_osiris_catalog()
    print("=== Iceberg ===")
    for name in (RAW_TABLE, OBS_TABLE, EVENTS_TABLE):
        tbl = cat.load_table(name)
        total = len(tbl.scan().to_arrow())
        if run_id:
            n = len(tbl.scan(row_filter=f"ingest_run_id = '{run_id}'").to_arrow())
            print(f"  {name:24s} total={total:8d}  run[{run_id}]={n}")
        else:
            print(f"  {name:24s} total={total:8d}")


def neo4j_counts() -> None:
    from neo4j import GraphDatabase

    uri = os.environ.get("NEO4J_URI", "bolt://neo4j:7687")
    pwd = os.environ.get("NEO4J_PASSWORD", "osirisgraph1")
    drv = GraphDatabase.driver(uri, auth=(os.environ.get("NEO4J_USER", "neo4j"), pwd))
    print("=== Neo4j nodes by label ===")
    with drv.session() as s:
        for rec in s.run(
            "MATCH (n) UNWIND labels(n) AS l RETURN l AS label, count(*) AS c ORDER BY c DESC"
        ):
            print(f"  {rec['label']:18s} {rec['c']}")
        print("=== Neo4j relationships by type ===")
        for rec in s.run(
            "MATCH ()-[r]->() RETURN type(r) AS t, count(*) AS c ORDER BY c DESC"
        ):
            print(f"  {rec['t']:18s} {rec['c']}")
        total = s.run("MATCH (n) RETURN count(n) AS c").single()["c"]
        rels = s.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"]
        print(f"  TOTAL nodes={total} rels={rels}")
    drv.close()


if __name__ == "__main__":
    run_id = sys.argv[1] if len(sys.argv) > 1 else None
    iceberg_counts(run_id)
    neo4j_counts()
