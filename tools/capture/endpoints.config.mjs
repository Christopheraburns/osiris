/**
 * OSIRIS Capture — Endpoint Catalog
 *
 * Single source of truth that drives capture.mjs, transform.mjs and replay.mjs.
 *
 * Each endpoint entry:
 *   name          unique short id (also the output filename stem)
 *   path          URL path on the OSIRIS server
 *   query         optional query params object (e.g. { region: 'all', v: '2' })
 *   pollMs        recommended polling interval for time-series capture
 *   requiresInput true => param-driven; only captured when --include-sampled is set
 *   captureOnly   true => raw JSON is stored but it has no geo entities to transform
 *   extract       array of extraction specs (an endpoint can expose several arrays)
 *
 * Extraction spec fields:
 *   arrayPath     dotted path to the entity array inside the payload (e.g. 'events')
 *   domain        Polybolos domain: AIR | SEA | SPACE | LAND | CYBER
 *   entityType    free-form type label (TRACK, SEISMIC, INCIDENT, ...)
 *   id            field name holding the entity id        (optional -> synthesized)
 *   name          field name holding a display name        (optional)
 *   lat / lng     field names holding coordinates          (default 'lat' / 'lng')
 *   coordsField   alternative: a [a,b] array field          (e.g. news 'coords')
 *   coordsOrder   'latlng' | 'lnglat' for coordsField      (default 'latlng')
 *   alt/heading/speed  optional field names copied into position
 *   threat        optional constant threat level for every entity
 */

export const ENDPOINTS = [
  // ── Movement-heavy feeds (poll fast) ──────────────────────────────────────
  {
    name: 'flights',
    path: '/api/flights',
    pollMs: 30_000,
    extract: [
      { arrayPath: 'commercial_flights', domain: 'AIR', entityType: 'COMMERCIAL', id: 'icao24', name: 'callsign', alt: 'alt', heading: 'heading', speed: 'speed_knots' },
      { arrayPath: 'private_flights', domain: 'AIR', entityType: 'PRIVATE', id: 'icao24', name: 'callsign', alt: 'alt', heading: 'heading', speed: 'speed_knots' },
      { arrayPath: 'private_jets', domain: 'AIR', entityType: 'JET', id: 'icao24', name: 'callsign', alt: 'alt', heading: 'heading', speed: 'speed_knots' },
      { arrayPath: 'military_flights', domain: 'AIR', entityType: 'MILITARY', id: 'icao24', name: 'callsign', alt: 'alt', heading: 'heading', speed: 'speed_knots', threat: 'ELEVATED' },
    ],
  },
  {
    name: 'satellites',
    path: '/api/satellites',
    pollMs: 60_000,
    extract: [
      { arrayPath: 'satellites', domain: 'SPACE', entityType: 'SATELLITE', id: 'norad_id', name: 'name', lat: 'lat', lng: 'lng', alt: 'alt' },
    ],
  },
  {
    name: 'maritime',
    path: '/api/maritime',
    pollMs: 60_000,
    extract: [
      { arrayPath: 'ports', domain: 'SEA', entityType: 'PORT', name: 'name', lat: 'lat', lng: 'lng' },
      { arrayPath: 'chokepoints', domain: 'SEA', entityType: 'CHOKEPOINT', name: 'name', lat: 'lat', lng: 'lng' },
      { arrayPath: 'ships', domain: 'SEA', entityType: 'VESSEL', id: 'mmsi', name: 'name', lat: 'lat', lng: 'lng', heading: 'heading', speed: 'speed' },
    ],
  },

  // ── Event feeds (poll moderate) ───────────────────────────────────────────
  {
    name: 'earthquakes',
    path: '/api/earthquakes',
    pollMs: 300_000,
    extract: [
      { arrayPath: 'earthquakes', domain: 'LAND', entityType: 'SEISMIC', id: 'id', name: 'place', lat: 'lat', lng: 'lng', alt: 'depth' },
    ],
  },
  {
    name: 'gdelt',
    path: '/api/gdelt',
    pollMs: 300_000,
    extract: [
      { arrayPath: 'events', domain: 'LAND', entityType: 'INCIDENT', name: 'title', lat: 'lat', lng: 'lng' },
    ],
  },
  {
    name: 'fires',
    path: '/api/fires',
    pollMs: 600_000,
    extract: [
      { arrayPath: 'fires', domain: 'LAND', entityType: 'FIRE', name: 'brightness', lat: 'lat', lng: 'lng', threat: 'ELEVATED' },
    ],
  },
  {
    name: 'weather',
    path: '/api/weather',
    pollMs: 600_000,
    extract: [
      { arrayPath: 'events', domain: 'LAND', entityType: 'WEATHER', id: 'id', name: 'title', lat: 'lat', lng: 'lng' },
    ],
  },
  {
    name: 'malware',
    path: '/api/malware',
    pollMs: 600_000,
    extract: [
      { arrayPath: 'threats', domain: 'CYBER', entityType: 'MALWARE', id: 'id', name: 'ip', lat: 'lat', lng: 'lng', threat: 'HIGH' },
    ],
  },
  {
    name: 'radar',
    path: '/api/radar',
    pollMs: 300_000,
    extract: [
      { arrayPath: 'outages', domain: 'CYBER', entityType: 'OUTAGE', name: 'name', lat: 'lat', lng: 'lng' },
    ],
  },
  {
    name: 'air-quality',
    path: '/api/air-quality',
    pollMs: 900_000,
    extract: [
      { arrayPath: 'stations', domain: 'LAND', entityType: 'AIR_QUALITY', name: 'location', lat: 'lat', lng: 'lng' },
    ],
  },
  {
    name: 'news',
    path: '/api/news',
    pollMs: 600_000,
    extract: [
      { arrayPath: 'news', domain: 'LAND', entityType: 'NEWS', id: 'id', name: 'title', coordsField: 'coords', coordsOrder: 'latlng' },
    ],
  },

  // ── Mostly-static reference feeds (poll slow / capture once) ───────────────
  {
    name: 'infrastructure',
    path: '/api/infrastructure',
    pollMs: 3_600_000,
    extract: [
      { arrayPath: 'infrastructure', domain: 'LAND', entityType: 'NUCLEAR', id: 'id', name: 'name', lat: 'lat', lng: 'lng' },
    ],
  },
  {
    name: 'scm-suppliers',
    path: '/api/scm-suppliers',
    pollMs: 600_000,
    extract: [
      { arrayPath: 'suppliers', domain: 'LAND', entityType: 'SUPPLIER', id: 'id', name: 'name', lat: 'lat', lng: 'lng' },
    ],
  },
  {
    name: 'cctv',
    path: '/api/cctv',
    query: { region: 'all', v: '2' },
    pollMs: 3_600_000,
    extract: [
      { arrayPath: 'cameras', domain: 'LAND', entityType: 'CCTV', id: 'id', name: 'name', lat: 'lat', lng: 'lng' },
    ],
  },
  {
    name: 'live-news',
    path: '/api/live-news',
    pollMs: 3_600_000,
    extract: [
      { arrayPath: 'feeds', domain: 'LAND', entityType: 'BROADCAST', id: 'id', name: 'name', lat: 'lat', lng: 'lng' },
    ],
  },

  // ── Capture-only (no point geometry to map, still useful context) ──────────
  { name: 'markets', path: '/api/markets', pollMs: 300_000, captureOnly: true },
  { name: 'crypto', path: '/api/crypto', pollMs: 300_000, captureOnly: true },
  { name: 'space-weather', path: '/api/space-weather', pollMs: 900_000, captureOnly: true },
  { name: 'country-risk', path: '/api/country-risk', pollMs: 600_000, captureOnly: true },
  { name: 'cyber-threats', path: '/api/cyber-threats', pollMs: 600_000, captureOnly: true },
  { name: 'frontlines', path: '/api/frontlines', pollMs: 1_800_000, captureOnly: true },
  { name: 'stats', path: '/api/stats', pollMs: 300_000, captureOnly: true },
  { name: 'geo', path: '/api/geo', pollMs: 3_600_000, captureOnly: true },
  { name: 'health', path: '/api/health', pollMs: 60_000, captureOnly: true },

  // ── Param-driven (only captured with --include-sampled) ────────────────────
  {
    name: 'region-dossier',
    path: '/api/region-dossier',
    requiresInput: true,
    captureOnly: true,
    pollMs: 3_600_000,
    sampleParam: (s) => ({ lat: String(s.lat), lng: String(s.lng) }),
  },
  {
    name: 'sentinel',
    path: '/api/sentinel',
    requiresInput: true,
    captureOnly: true,
    pollMs: 3_600_000,
    sampleParam: (s) => ({ lat: String(s.lat), lng: String(s.lng), radius: '2', days: '30' }),
  },
  {
    name: 'osint-ip',
    path: '/api/osint/ip',
    requiresInput: true,
    captureOnly: true,
    pollMs: 3_600_000,
    sampleParam: (s) => ({ ip: s.ip }),
    sampleKey: 'ips',
  },
  {
    name: 'osint-whois',
    path: '/api/osint/whois',
    requiresInput: true,
    captureOnly: true,
    pollMs: 3_600_000,
    sampleParam: (s) => ({ domain: s.domain }),
    sampleKey: 'domains',
  },
  {
    name: 'osint-dns',
    path: '/api/osint/dns',
    requiresInput: true,
    captureOnly: true,
    pollMs: 3_600_000,
    sampleParam: (s) => ({ domain: s.domain }),
    sampleKey: 'domains',
  },
];

/**
 * Sample inputs used for param-driven endpoints (only with --include-sampled).
 * Edit freely — these just need to return data, they are demo seeds.
 */
export const SAMPLES = {
  latlng: [
    { lat: 50.45, lng: 30.52 },   // Kyiv
    { lat: 31.78, lng: 35.22 },   // Jerusalem
    { lat: 25.20, lng: 55.27 },   // Dubai
  ],
  ips: ['8.8.8.8', '1.1.1.1', '208.67.222.222'],
  domains: ['example.com', 'cloudflare.com', 'wikipedia.org'],
};

/** Resolve the sample list for an endpoint (lat/lng default, or its sampleKey). */
export function samplesFor(ep) {
  if (ep.sampleKey === 'ips') return SAMPLES.ips.map((ip) => ({ ip }));
  if (ep.sampleKey === 'domains') return SAMPLES.domains.map((domain) => ({ domain }));
  return SAMPLES.latlng;
}
