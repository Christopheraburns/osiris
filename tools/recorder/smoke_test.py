"""De-risk: prove PyIceberg can read + append to the Ozone-backed tables.

Run inside the recorder image on osiris-net:
    python smoke_test.py
"""
from __future__ import annotations

import datetime as dt

import pyarrow as pa

from catalog import OBS_TABLE, load_osiris_catalog


def main() -> None:
    cat = load_osiris_catalog()
    print("catalog loaded:", cat)

    tbl = cat.load_table(OBS_TABLE)
    print("loaded table:", OBS_TABLE)
    print("location:", tbl.location())

    before = len(tbl.scan(row_filter="ingest_run_id = 'smoke-001'").to_arrow())
    print("rows tagged smoke-001 BEFORE:", before)

    now = dt.datetime.now(dt.timezone.utc)
    today = now.date()
    row = {
        "entity_id": "smoke-entity-1",
        "canonical_id": None,
        "domain": "AIR",
        "entity_type": "TEST",
        "name": "SMOKE TEST",
        "lat": 1.0, "lng": 2.0, "alt": None, "heading": None, "speed": None,
        "threat": "NONE", "classification": "UNCLASSIFIED", "confidence": 0.5,
        "provider": "recorder", "feed": "smoke", "source_original_id": "smoke-entity-1",
        "observed_at": now, "captured_at": now, "ingested_at": now,
        "ingest_run_id": "smoke-001", "schema_version": 1,
        "properties": "{}", "obs_date": today,
    }
    arrow = pa.Table.from_pylist([row], schema=tbl.schema().as_arrow())
    tbl.append(arrow)
    print("append OK")

    after = len(tbl.scan(row_filter="ingest_run_id = 'smoke-001'").to_arrow())
    print("rows tagged smoke-001 AFTER:", after)
    print("SMOKE TEST PASSED" if after == before + 1 else "SMOKE TEST UNEXPECTED COUNT")


if __name__ == "__main__":
    main()
