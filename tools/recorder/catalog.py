"""PyIceberg catalog wiring for the OSIRIS lakehouse.

Connects to the existing Hive Metastore (catalog) and Ozone S3 gateway
(storage). The same warehouse/tables the Flink Pattern B job writes — the
recorder is just the direct-API writer for the same contract.
"""
from __future__ import annotations

import os

from pyiceberg.catalog import load_catalog

# Database + table names (must match flink/sql/lakehouse_ddl.sql).
DB = "lake"
RAW_TABLE = f"{DB}.raw_records"
OBS_TABLE = f"{DB}.observations"
EVENTS_TABLE = f"{DB}.events"


def load_osiris_catalog():
    """Load the Hive-backed Iceberg catalog pointed at Ozone via the S3 gateway.

    Note on the s3a scheme: the Hive Metastore stores table locations as
    `s3a://…`. PyIceberg's PyArrow FileIO treats s3a/s3n as s3, and the
    `s3.endpoint` below routes it at the Ozone S3 gateway.
    """
    return load_catalog(
        "osiris",
        **{
            "type": "hive",
            "uri": os.environ.get("ICEBERG_URI", "thrift://hive-metastore:9083"),
            "warehouse": os.environ.get("ICEBERG_WAREHOUSE", "s3a://osiris-lake/warehouse"),
            # Use the fsspec/s3fs FileIO (single PutObject for small files) — the
            # pyarrow S3 writer's multipart upload is rejected by Ozone's S3 gateway.
            "py-io-impl": "pyiceberg.io.fsspec.FsspecFileIO",
            "s3.endpoint": os.environ.get("S3_ENDPOINT", "http://ozone-s3g:9878"),
            "s3.access-key-id": os.environ.get("S3_ACCESS_KEY", "osiris"),
            "s3.secret-access-key": os.environ.get("S3_SECRET_KEY", "osiris"),
            "s3.path-style-access": "true",
            "s3.region": os.environ.get("S3_REGION", "us-east-1"),
        },
    )
