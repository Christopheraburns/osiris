"""Migrated-feed registry.

The single source of truth for which feeds have been migrated to the streaming
lakehouse path. Adding a feed later is a two-step change with no architectural
impact: build a NiFi flow that publishes its entities to ``osiris.entities`` and
add an entry here.

Each entry declares:
  * ``response_key``  -- the top-level key the OSIRIS route returns
                         (e.g. ``earthquakes`` -> ``{"earthquakes": [...]}``).
  * ``entity_types``  -- normalized PolyBolos entityType values that map to this
                         feed (used to route entity-shaped Kafka records).
  * ``reshape``       -- callable(record) -> feed item dict.
"""
from __future__ import annotations

from typing import Callable

from reshape import reshape_earthquake

FeedReshaper = Callable[[dict], dict]


class FeedSpec:
    def __init__(self, name: str, response_key: str, entity_types: set[str], reshape: FeedReshaper) -> None:
        self.name = name
        self.response_key = response_key
        self.entity_types = entity_types
        self.reshape = reshape


# Earthquakes is the first (proof-of-concept) migrated feed.
FEEDS: dict[str, FeedSpec] = {
    "earthquakes": FeedSpec(
        name="earthquakes",
        response_key="earthquakes",
        entity_types={"SEISMIC"},
        reshape=reshape_earthquake,
    ),
}

# entityType -> feed name, derived from FEEDS for O(1) routing of entity records.
ENTITY_TYPE_TO_FEED: dict[str, str] = {
    et: spec.name for spec in FEEDS.values() for et in spec.entity_types
}

# response_key -> feed name, for routing feed-shaped envelope messages.
RESPONSE_KEY_TO_FEED: dict[str, str] = {
    spec.response_key: spec.name for spec in FEEDS.values()
}


def migrated_feeds() -> list[str]:
    return list(FEEDS.keys())
