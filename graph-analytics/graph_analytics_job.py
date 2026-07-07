#!/usr/bin/env python3
"""Stage 2 — distributed graph analytics with Spark GraphFrames.

This is the "distributed graph" tier the customer asked about: whole-graph
algorithms that a single-node graph *database* (Memgraph) is not built to run.
Reads the exported nodes/edges, computes per-node metrics across the cluster,
and writes them back as an Iceberg table (snapshot per run = audit + trend).

    PageRank            → influence / centrality of each entity
    connectedComponents → which entities form one isolated network
    labelPropagation    → community / cluster membership (fleets, rings)
    degree              → raw connectivity

Output:
    Iceberg table  osiris.graph_metrics
        node_id, label, name, pagerank, component, community, degree, computed_at
    plus a flat  metrics.csv  in EXPORT_DIR for the (Spark-free) enrichment step.

Run on the Spark 3 Data Hub, e.g.:
    spark-submit \
      --packages graphframes:graphframes:0.8.4-spark3.5-s_2.12 \
      --conf spark.sql.catalog.spark_catalog=org.apache.iceberg.spark.SparkSessionCatalog \
      --conf spark.sql.catalog.spark_catalog.type=hive \
      graph_analytics_job.py

Air-gapped: pre-stage the GraphFrames jar and pass it with --jars instead of
--packages (see the runbook). Match the coordinate to your Spark/Scala version.

Env:
    EXPORT_DIR       where export_graph.py wrote nodes.csv/edges.csv (local or s3://)
    METRICS_TABLE    Iceberg table to write (default osiris.graph_metrics)
    CHECKPOINT_DIR   Spark checkpoint dir (required by connectedComponents)
"""
from __future__ import annotations

import os
import sys

from pyspark.sql import SparkSession
from pyspark.sql import functions as F
from graphframes import GraphFrame

EXPORT_DIR = os.environ.get("EXPORT_DIR", "./graph-export").rstrip("/")
METRICS_TABLE = os.environ.get("METRICS_TABLE", "osiris.graph_metrics")
CHECKPOINT_DIR = os.environ.get("CHECKPOINT_DIR", "/tmp/osiris-graph-checkpoints")
PR_MAX_ITER = int(os.environ.get("PAGERANK_ITERS", "5"))
LPA_MAX_ITER = int(os.environ.get("LPA_ITERS", "2"))
METRICS_CSV = os.environ.get("METRICS_CSV", f"{EXPORT_DIR}/metrics.csv")


def main() -> int:
    spark = (
        SparkSession.builder.appName("osiris-graph-analytics")
        .config("spark.sql.iceberg.handle-timestamp-without-timezone", "true")
        .getOrCreate()
    )
    spark.sparkContext.setCheckpointDir(CHECKPOINT_DIR)

    vertices = (
        spark.read.option("header", True).csv(f"{EXPORT_DIR}/nodes.csv")
        .withColumnRenamed("id", "id")
        .select(F.col("id").cast("string"), "label", "name")
        .dropna(subset=["id"])
    )
    edges = (
        spark.read.option("header", True).csv(f"{EXPORT_DIR}/edges.csv")
        .select(F.col("src").cast("string"), F.col("dst").cast("string"), "rel")
        .dropna(subset=["src", "dst"])
    )

    n_v, n_e = vertices.count(), edges.count()
    print(f"[graph] {n_v:,} vertices, {n_e:,} edges", flush=True)
    if n_v == 0:
        print("[graph] no vertices — did export_graph.py run? aborting.", file=sys.stderr)
        return 2

    g = GraphFrame(vertices, edges)

    pr = g.pageRank(resetProbability=0.15, maxIter=PR_MAX_ITER).vertices.select(
        "id", F.col("pagerank")
    )
    deg = g.degrees.select("id", F.col("degree"))  # undirected total degree

    metrics = (
        vertices.alias("v")
        .join(pr, "id", "left")
        .join(deg, "id", "left")
    )

    # labelPropagation (communities) does iterative message-passing that is highly
    # sensitive to hub-node skew — on this graph a few super-connected reference nodes
    # make single tasks run 10-15 min each and stall the whole stage. Off by default so
    # the run completes; set RUN_COMMUNITIES=true (optionally LPA_ITERS higher) to try
    # it on a bigger cluster. When off, 'community' is null.
    if os.environ.get("RUN_COMMUNITIES", "false").lower() == "true":
        lpa = g.labelPropagation(maxIter=LPA_MAX_ITER).select(
            "id", F.col("label").alias("community").cast("string")
        )
        metrics = metrics.join(lpa, "id", "left")
    else:
        metrics = metrics.withColumn("community", F.lit(None).cast("string"))

    # connectedComponents is UNBOUNDED (iterates to convergence) and checkpoints to
    # S3 every step — on a small cluster it dominates runtime (an hour+). Off by
    # default; set RUN_CONNECTED_COMPONENTS=true to include it. When off, 'component'
    # is null.
    if os.environ.get("RUN_CONNECTED_COMPONENTS", "false").lower() == "true":
        cc = g.connectedComponents().select("id", F.col("component").cast("string"))
        metrics = metrics.join(cc, "id", "left")
    else:
        metrics = metrics.withColumn("component", F.lit(None).cast("string"))

    metrics = metrics.select(
        F.col("id").alias("node_id"),
        F.col("v.label").alias("label"),
        F.col("v.name").alias("name"),
        F.coalesce(F.col("pagerank"), F.lit(0.0)).alias("pagerank"),
        F.col("component"),
        F.col("community"),
        F.coalesce(F.col("degree"), F.lit(0)).alias("degree"),
        F.current_timestamp().alias("computed_at"),
    )
    metrics.cache()
    print(f"[graph] metrics rows: {metrics.count():,}", flush=True)

    # 1) Iceberg — snapshot per run (audit + trend-over-time via time travel).
    (
        metrics.writeTo(METRICS_TABLE)
        .using("iceberg")
        .tableProperty("format-version", "2")
        .createOrReplace()
    )
    print(f"[graph] wrote Iceberg table {METRICS_TABLE}", flush=True)

    # 2) Flat CSV for the Spark-free enrichment step (enrich_memgraph.py).
    (
        metrics.select("node_id", "label", "name", "pagerank", "community", "degree")
        .coalesce(1)
        .write.mode("overwrite").option("header", True)
        .csv(METRICS_CSV.rstrip("/") + "_spark")
    )
    print(f"[graph] wrote metrics CSV under {METRICS_CSV}_spark", flush=True)

    # Quick top-10 to stdout so the job log shows something meaningful.
    print("[graph] top-10 by PageRank:", flush=True)
    for row in metrics.orderBy(F.desc("pagerank")).limit(10).collect():
        print(f"    {row['pagerank']:.4f}  {row['label']:<14} {row['name']}", flush=True)

    spark.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
