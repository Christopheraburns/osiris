"""Normalization — faithful Python port of tools/capture/lib.mjs.

Turns a raw OSIRIS API payload into normalized point records (extract_entities),
then into provenance-stamped rows for the three Iceberg tables. The PolyBolos
contract is identical to the Flink Kafka path so both writers produce the same
silver schema.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
from typing import Any

_HERE = os.path.dirname(os.path.abspath(__file__))


def load_endpoints(path: str | None = None) -> dict:
    with open(path or os.path.join(_HERE, "endpoints.json"), encoding="utf-8") as fh:
        return json.load(fh)


def _slug(s: Any) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "_", str(s))


def _get_by_path(obj: Any, dotted: str | None) -> Any:
    if not dotted:
        return None
    cur = obj
    for k in dotted.split("."):
        if cur is None or not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def _to_num(v: Any) -> float | None:
    try:
        n = float(v) if v is not None and v != "" else None
    except (TypeError, ValueError):
        return None
    return n if n is not None and n == n and n not in (float("inf"), float("-inf")) else None


def extract_entities(payload: Any, endpoint: dict) -> list[dict]:
    """Mirror of lib.mjs extractEntities()."""
    specs = endpoint.get("extract")
    if not payload or not isinstance(specs, list):
        return []

    records: list[dict] = []
    for spec in specs:
        arr = _get_by_path(payload, spec.get("arrayPath"))
        if not isinstance(arr, list):
            continue

        idx = 0
        for item in arr:
            if item is None or not isinstance(item, dict):
                continue
            idx += 1

            if spec.get("coordsField"):
                pair = item.get(spec["coordsField"])
                if not isinstance(pair, list) or len(pair) < 2:
                    continue
                if spec.get("coordsOrder", "latlng") == "lnglat":
                    lng, lat = _to_num(pair[0]), _to_num(pair[1])
                else:
                    lat, lng = _to_num(pair[0]), _to_num(pair[1])
            else:
                lat = _to_num(item.get(spec.get("lat", "lat")))
                lng = _to_num(item.get(spec.get("lng", "lng")))
            if lat is None or lng is None:
                continue

            raw_id = item.get(spec["id"]) if spec.get("id") else None
            entity_id = (
                str(raw_id)
                if raw_id is not None and len(str(raw_id)) > 0
                else f"{endpoint['name']}-{_slug(spec.get('arrayPath'))}-{idx}"
            )

            name_field = spec.get("name")
            name = (
                str(item.get(name_field))
                if name_field and item.get(name_field) is not None
                else entity_id
            )

            records.append(
                {
                    "lat": lat,
                    "lng": lng,
                    "id": entity_id,
                    "name": name,
                    "domain": spec.get("domain", "LAND"),
                    "entityType": spec.get("entityType", "TRACK"),
                    "alt": _to_num(item.get(spec["alt"])) if spec.get("alt") else None,
                    "heading": _to_num(item.get(spec["heading"])) if spec.get("heading") else None,
                    "speed": _to_num(item.get(spec["speed"])) if spec.get("speed") else None,
                    "threat": spec.get("threat"),
                    "original": item,
                }
            )
    return records


def is_event_feed(name: str, endpoints_cfg: dict) -> bool:
    return name in set(endpoints_cfg.get("event_sources", []))


# ── Iceberg row builders ────────────────────────────────────────────────────

def build_raw_row(endpoint: dict, payload: Any, captured_at, ingested_at, ingest_run_id) -> dict:
    """One bronze row per API response — the loss-less long-tail safety net."""
    return {
        "source": endpoint["name"],
        "payload": json.dumps(payload, separators=(",", ":"), ensure_ascii=False),
        "provider": "osiris",
        "feed": endpoint["name"],
        "ingest_run_id": ingest_run_id,
        "schema_version": 1,
        "captured_at": captured_at,
        "ingested_at": ingested_at,
        "ingest_date": ingested_at.date(),
    }


def build_obs_row(rec: dict, endpoint: dict, captured_at, ingested_at, ingest_run_id) -> dict:
    return {
        "entity_id": rec["id"],
        "canonical_id": None,
        "domain": rec["domain"],
        "entity_type": rec["entityType"],
        "name": rec["name"],
        "lat": rec["lat"],
        "lng": rec["lng"],
        "alt": rec["alt"],
        "heading": rec["heading"],
        "speed": rec["speed"],
        "threat": rec["threat"] or "NONE",
        "classification": "UNCLASSIFIED",
        "confidence": 0.9,
        "provider": "osiris",
        "feed": endpoint["name"],
        "source_original_id": rec["id"],
        "observed_at": captured_at,
        "captured_at": captured_at,
        "ingested_at": ingested_at,
        "ingest_run_id": ingest_run_id,
        "schema_version": 1,
        "properties": json.dumps(rec["original"], separators=(",", ":"), ensure_ascii=False),
        "obs_date": captured_at.date(),
    }


def build_event_row(rec: dict, endpoint: dict, captured_at, ingested_at, ingest_run_id) -> dict:
    original = rec["original"]
    return {
        "event_id": rec["id"],
        "event_type": rec["entityType"],
        "domain": rec["domain"],
        "name": rec["name"],
        "lat": rec["lat"],
        "lng": rec["lng"],
        "magnitude": _to_num(original.get("mag")) if isinstance(original, dict) else None,
        "brightness": _to_num(original.get("brightness")) if isinstance(original, dict) else None,
        "provider": "osiris",
        "feed": endpoint["name"],
        "source_original_id": rec["id"],
        "occurred_at": captured_at,
        "captured_at": captured_at,
        "ingested_at": ingested_at,
        "ingest_run_id": ingest_run_id,
        "schema_version": 1,
        "properties": json.dumps(original, separators=(",", ":"), ensure_ascii=False),
        "event_date": captured_at.date(),
    }
