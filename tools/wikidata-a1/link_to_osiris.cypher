// A1 step 4 - link the Wikidata reference core to the live OSIRIS graph.
//
// Two layers, run in order:
//   A. Identifier-based (high confidence) - join on a globally-unique code that
//      is the SAME string on both sides (ISO, IMO, ICAO). Near-certain matches.
//   B. Name-slug fallback (approximate) - for entities with no shared identifier,
//      join on the lower-case name slug (recorder orgId/iso == Wikidata nameKey).
//
// Run AFTER the offline import and after the recorder has repopulated the live
// OSIRIS entities. Create the indexes first:
//   CREATE INDEX wd_namekey IF NOT EXISTS FOR (n:Wikidata) ON (n.nameKey);
//   CREATE INDEX wd_iso     IF NOT EXISTS FOR (n:Wikidata) ON (n.isoA2);
//   CREATE INDEX wd_imo     IF NOT EXISTS FOR (n:Ship)     ON (n.imo);
//   CREATE INDEX wd_icao    IF NOT EXISTS FOR (n:Airline)  ON (n.icao);
//
// =============================================================================
// A. IDENTIFIER-BASED (high confidence, method-stamped)
//
// These activate only when the OSIRIS node carries the identifier. The recorder
// does NOT persist these yet (it keys Country by name slug, Vessel by mmsi, and
// stores airlines as Organizations) - so each clause is guarded by IS NOT NULL
// and is a no-op until the source fields are persisted. To enable them, extend
// the recorder to set:
//   Country.isoCode   <- real ISO 3166-1 alpha-2 (not the name slug)
//   Vessel.imo        <- IMO number from the AIS record (alongside mmsi)
//   Organization.icao <- airline ICAO designator (e.g. from callsign prefix)

// A1) Countries by ISO 3166-1 alpha-2
MATCH (r:Country) WHERE r.isoCode IS NOT NULL AND NOT r:Wikidata
MATCH (w:Wikidata:Country)
WHERE toUpper(w.isoA2) = toUpper(r.isoCode)
MERGE (r)-[s:SAME_AS]->(w)
SET s.method = 'iso-a2', s.confidence = 0.99;

// A2) Vessels by IMO number (same physical ship regardless of name)
MATCH (r:Vessel) WHERE r.imo IS NOT NULL
MATCH (s2:Wikidata:Ship {imo: toString(r.imo)})
MERGE (r)-[s:SAME_AS]->(s2)
SET s.method = 'imo', s.confidence = 0.99;

// A3) Airlines by ICAO designator (recorder stores airlines as Organization)
MATCH (r:Organization) WHERE r.icao IS NOT NULL
MATCH (w:Wikidata:Airline)
WHERE toUpper(w.icao) = toUpper(r.icao)
MERGE (r)-[s:SAME_AS]->(w)
SET s.method = 'icao', s.confidence = 0.97;

// =============================================================================
// B. NAME-SLUG FALLBACK (only where no identifier link was made above)
//
// The recorder keys Organization by orgId and Country by iso, both the
// lower-case name slug (graph_sink._org_id) - the same slug the import stores as
// nameKey. Skip any node already linked by identifier in section A.

// B1) Organizations
MATCH (r:Organization) WHERE r.orgId IS NOT NULL AND NOT (r)-[:SAME_AS]->(:Wikidata)
MATCH (w:Wikidata:Organization {nameKey: r.orgId})
WHERE r <> w
MERGE (r)-[s:SAME_AS]->(w)
SET s.method = 'name-slug', s.confidence = 0.6;

// B2) Airlines stored as Organization (slug fallback when no ICAO)
MATCH (r:Organization) WHERE r.orgId IS NOT NULL AND NOT (r)-[:SAME_AS]->(:Wikidata)
MATCH (w:Wikidata:Airline {nameKey: r.orgId})
WHERE r <> w
MERGE (r)-[s:SAME_AS]->(w)
SET s.method = 'name-slug', s.confidence = 0.6;

// B3) Countries
MATCH (r:Country) WHERE r.iso IS NOT NULL AND NOT r:Wikidata AND NOT (r)-[:SAME_AS]->(:Wikidata)
MATCH (w:Wikidata:Country {nameKey: r.iso})
WHERE r <> w
MERGE (r)-[s:SAME_AS]->(w)
SET s.method = 'name-slug', s.confidence = 0.8;

// =============================================================================
// After linking, an OSIRIS platform reaches Wikidata facts in two hops:
//   MATCH (v:Vessel)-[:OWNED_BY]->(:Organization)-[:SAME_AS]->(w:Wikidata)
//   MATCH (w)-[:HAS_CEO|PARENT_ORG|LOCATED_IN]->(x) RETURN v, w, x;
//
// Inspect link quality by method:
//   MATCH ()-[s:SAME_AS]->(:Wikidata) RETURN s.method, count(*) ORDER BY count(*) DESC;
