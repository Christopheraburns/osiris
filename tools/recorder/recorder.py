"""OSIRIS Recorder — direct-API -> Iceberg lakehouse (+ Neo4j projection).

Modes:
  idle     (default) HTTP control server — recording OFF until POST /control start.
  poll     continuously poll each /api/* endpoint on its own cadence,
           append to Iceberg, project durable entities into Neo4j.
  once     run exactly one cycle over every endpoint, then exit (CI / smoke).
  rebuild  ignore the APIs; scan the Iceberg silver tables and replay them
           through the SAME graph projection — proves the graph is a
           rebuildable view of the lake (system of record = Iceberg).

Iceberg is the system of record; Neo4j is a derived, idempotent projection.
Run:
  python -m recorder --mode idle
  python -m recorder --mode once
  python -m recorder --mode poll
  python -m recorder --mode rebuild
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
import time
import uuid
from urllib.parse import urlencode, urljoin

import requests

from iceberg_sink import IcebergSink
from normalize import (
    build_event_row,
    build_obs_row,
    build_raw_row,
    extract_entities,
    is_event_feed,
    load_endpoints,
)

try:
    from graph_sink import GraphSink
except Exception:  # neo4j import issues shouldn't block Iceberg writes
    GraphSink = None


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _log(msg: str) -> None:
    print(f"[{_now().isoformat()}] {msg}", flush=True)


def fetch_json(url: str, timeout: int = 20, retries: int = 2):
    """Port of lib.mjs fetchJson: retry/backoff on 429/5xx and network errors."""
    attempt = 0
    while attempt <= retries:
        try:
            resp = requests.get(url, timeout=timeout)
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt < retries:
                    time.sleep(0.5 * (2 ** attempt))
                    attempt += 1
                    continue
                return None
            return resp.json()
        except Exception as exc:  # noqa: BLE001
            if attempt < retries:
                time.sleep(0.5 * (2 ** attempt))
                attempt += 1
                continue
            _log(f"  fetch error {url}: {exc}")
            return None
    return None


def build_url(base: str, endpoint: dict) -> str:
    url = urljoin(base.rstrip("/") + "/", endpoint["path"].lstrip("/"))
    query = endpoint.get("query")
    return f"{url}?{urlencode(query)}" if query else url


class Recorder:
    def __init__(self, base_url: str, ingest_run_id: str, use_graph: bool = True) -> None:
        self.base_url = base_url
        self.ingest_run_id = ingest_run_id
        self.cfg = load_endpoints()
        self.endpoints = self.cfg["endpoints"]
        self.iceberg = IcebergSink()
        self.graph = None
        if use_graph and GraphSink is not None:
            try:
                self.graph = GraphSink()
                self.graph.ensure_constraints()
                _log("graph: connected to Neo4j, constraints ensured")
            except Exception as exc:  # noqa: BLE001
                _log(f"graph: disabled (Neo4j unavailable: {exc})")
                self.graph = None

    # ── one endpoint, one cycle ─────────────────────────────────────────────
    def poll_endpoint(self, endpoint: dict) -> None:
        captured_at = _now()
        payload = fetch_json(build_url(self.base_url, endpoint))
        ingested_at = _now()
        if payload is None:
            _log(f"  {endpoint['name']}: no payload")
            return

        self.iceberg.append_raw(
            [build_raw_row(endpoint, payload, captured_at, ingested_at, self.ingest_run_id)]
        )

        recs = extract_entities(payload, endpoint)
        if not recs:
            _log(f"  {endpoint['name']}: raw recorded, 0 entities")
            return

        if is_event_feed(endpoint["name"], self.cfg):
            rows = [build_event_row(r, endpoint, captured_at, ingested_at, self.ingest_run_id) for r in recs]
            self.iceberg.append_events(rows)
            gc = self._project(self.graph.project_events, rows) if self.graph else {}
            _log(f"  {endpoint['name']}: events={len(rows)} graph={gc}")
        else:
            rows = [build_obs_row(r, endpoint, captured_at, ingested_at, self.ingest_run_id) for r in recs]
            self.iceberg.append_observations(rows)
            gc = self._project(self.graph.project_observations, rows) if self.graph else {}
            _log(f"  {endpoint['name']}: observations={len(rows)} graph={gc}")

    @staticmethod
    def _project(fn, rows: list[dict]) -> dict:
        try:
            return fn(rows)
        except Exception as exc:  # noqa: BLE001
            _log(f"    graph projection failed: {exc}")
            return {"error": str(exc)}

    # ── modes ───────────────────────────────────────────────────────────────
    def run_once(self) -> None:
        _log(f"mode=once run_id={self.ingest_run_id} endpoints={len(self.endpoints)}")
        for ep in self.endpoints:
            self.poll_endpoint(ep)
        _log("once cycle complete")

    def run_poll(self) -> None:
        _log(f"mode=poll run_id={self.ingest_run_id} endpoints={len(self.endpoints)}")
        next_due = {ep["name"]: 0.0 for ep in self.endpoints}
        while True:
            now = time.time()
            for ep in self.endpoints:
                if now >= next_due[ep["name"]]:
                    self.poll_endpoint(ep)
                    next_due[ep["name"]] = now + ep.get("pollMs", 300_000) / 1000.0
            sleep_for = max(1.0, min(next_due.values()) - time.time())
            time.sleep(min(sleep_for, 5.0))

    def run_rebuild(self) -> None:
        """Replay Iceberg silver -> graph projection (rebuildability proof)."""
        if not self.graph:
            _log("rebuild requires Neo4j; aborting")
            sys.exit(1)
        _log("mode=rebuild scanning Iceberg silver tables")
        obs = self.iceberg.scan_observations()
        events = self.iceberg.scan_events()
        oc = self._project(self.graph.project_observations, obs) if obs else {}
        ec = self._project(self.graph.project_events, events) if events else {}
        _log(f"rebuild: observations={len(obs)} graph={oc}; events={len(events)} graph={ec}")


def main() -> None:
    ap = argparse.ArgumentParser(description="OSIRIS Recorder")
    ap.add_argument("--mode", choices=["idle", "poll", "once", "rebuild"], default=os.environ.get("MODE", "idle"))
    ap.add_argument("--base-url", default=os.environ.get("OSIRIS_BASE_URL", "http://osiris:3000"))
    ap.add_argument("--run-id", default=os.environ.get("INGEST_RUN_ID") or f"rec-{uuid.uuid4().hex[:12]}")
    ap.add_argument("--no-graph", action="store_true", default=os.environ.get("DISABLE_GRAPH") == "1")
    ap.add_argument("--control-port", type=int, default=int(os.environ.get("CONTROL_PORT", "8090")))
    args = ap.parse_args()

    if args.mode == "idle":
        from control_server import run_control_server

        run_control_server(
            port=args.control_port,
            base_url=args.base_url,
            use_graph=not args.no_graph,
        )
        return

    rec = Recorder(args.base_url, args.run_id, use_graph=not args.no_graph)
    if args.mode == "once":
        rec.run_once()
    elif args.mode == "rebuild":
        rec.run_rebuild()
    else:
        rec.run_poll()


if __name__ == "__main__":
    main()
