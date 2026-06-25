"""Iceberg sink — append normalized rows to the lake tables via PyIceberg.

Writes the same three tables the Flink Pattern B job writes
(lake.raw_records / observations / events). Used by the recorder for the
direct-API path, and the scan helpers feed the Neo4j rebuild.
"""
from __future__ import annotations

import pyarrow as pa

from catalog import EVENTS_TABLE, OBS_TABLE, RAW_TABLE, load_osiris_catalog


class IcebergSink:
    def __init__(self) -> None:
        self.catalog = load_osiris_catalog()
        self.raw = self.catalog.load_table(RAW_TABLE)
        self.obs = self.catalog.load_table(OBS_TABLE)
        self.events = self.catalog.load_table(EVENTS_TABLE)

    @staticmethod
    def _append(table, rows: list[dict]) -> int:
        if not rows:
            return 0
        arrow = pa.Table.from_pylist(rows, schema=table.schema().as_arrow())
        table.append(arrow)
        return len(rows)

    def append_raw(self, rows: list[dict]) -> int:
        return self._append(self.raw, rows)

    def append_observations(self, rows: list[dict]) -> int:
        return self._append(self.obs, rows)

    def append_events(self, rows: list[dict]) -> int:
        return self._append(self.events, rows)

    # ── Read side (rebuild) ─────────────────────────────────────────────────
    def scan_observations(self, row_filter: str = "") -> list[dict]:
        scan = self.obs.scan(row_filter=row_filter) if row_filter else self.obs.scan()
        return scan.to_arrow().to_pylist()

    def scan_events(self, row_filter: str = "") -> list[dict]:
        scan = self.events.scan(row_filter=row_filter) if row_filter else self.events.scan()
        return scan.to_arrow().to_pylist()
