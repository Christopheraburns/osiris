"""Entity -> feed-JSON reshapers.

The gateway consumes the canonical PolyBolos Kafka envelope published by NiFi to
``osiris.entities`` (see nifi/scripts/earthquakes-ingest.groovy), which has the
shape::

    {
      "schema_version": 1,
      "ingest_run_id": "...",
      "source": "earthquakes",
      "captured_at": "...",
      "entity": {
        "id": "...", "name": "...", "domain": "LAND", "entityType": "SEISMIC",
        "position": {"lat": .., "lng": .., "alt": ..},
        "timestamp": "ISO-8601",
        "source": {"provider": "osiris", "feed": "earthquakes", ...},
        "properties": {"mag": .., "depth": .., "url": .., "tsunami": .., ...}
      }
    }

Reshapers turn that back into the exact JSON shape the OSIRIS ``/api/<feed>``
route returns. They are tolerant: they accept the full envelope, a bare entity,
or an already feed-shaped item, pulling each field from whichever source exists.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any


def _as_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except (ValueError, TypeError):
            return {}
    return {}


def _first(*values: Any) -> Any:
    for v in values:
        if v is not None:
            return v
    return None


def _unwrap_entity(rec: dict) -> dict:
    """Return the entity dict whether ``rec`` is the envelope or the entity."""
    entity = rec.get("entity")
    return entity if isinstance(entity, dict) else rec


def _epoch_ms(*values: Any) -> Any:
    """Coerce a time to epoch milliseconds (frontend expects a number).

    Accepts epoch ms (int/float), numeric strings, or ISO-8601 strings.
    """
    for v in values:
        if v is None:
            continue
        if isinstance(v, (int, float)):
            return int(v)
        if isinstance(v, str):
            s = v.strip()
            if s.isdigit():
                return int(s)
            try:
                iso = s.replace("Z", "+00:00")
                return int(datetime.fromisoformat(iso).timestamp() * 1000)
            except ValueError:
                continue
    return None


def reshape_earthquake(rec: dict) -> dict:
    """Map a canonical envelope/entity to the ``/api/earthquakes`` item shape.

    Mirrors src/app/api/earthquakes/route.ts and
    nifi/scripts/earthquakes-ingest.groovy.
    """
    entity = _unwrap_entity(rec)
    position = _as_dict(entity.get("position"))
    props = _as_dict(entity.get("properties"))
    # Fallbacks if a raw USGS GeoJSON feature ever arrives instead of an entity.
    original = _as_dict(entity.get("original"))
    geometry = _as_dict(original.get("geometry"))
    coords = geometry.get("coordinates") if isinstance(geometry.get("coordinates"), list) else None
    raw_props = _as_dict(original.get("properties")) or original

    return {
        "id": _first(entity.get("id"), original.get("id")),
        "lat": _first(position.get("lat"), entity.get("lat"), original.get("lat"),
                      coords[1] if coords and len(coords) > 1 else None),
        "lng": _first(position.get("lng"), entity.get("lng"), original.get("lng"),
                      coords[0] if coords and len(coords) > 0 else None),
        "depth": _first(position.get("alt"), props.get("depth"), entity.get("depth"),
                        coords[2] if coords and len(coords) > 2 else None),
        "magnitude": _first(props.get("mag"), entity.get("magnitude"), raw_props.get("mag")),
        "place": _first(entity.get("name"), props.get("place"), raw_props.get("place")),
        "time": _epoch_ms(props.get("time"), entity.get("timestamp"), raw_props.get("time")),
        "url": _first(props.get("url"), raw_props.get("url")),
        "tsunami": _first(props.get("tsunami"), raw_props.get("tsunami")),
        "type": _first(props.get("type"), entity.get("entityType"), raw_props.get("type")),
        "felt": _first(props.get("felt"), raw_props.get("felt")),
        "alert": _first(props.get("alert"), raw_props.get("alert")),
    }
