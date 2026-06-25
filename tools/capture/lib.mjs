/**
 * OSIRIS Capture — shared helpers (zero dependencies, Node 22+).
 */

import { mkdir } from 'node:fs/promises';
import path from 'node:path';

/** Parse `--flag value` and `--bool` style argv into an object. */
export function parseArgs(argv = process.argv.slice(2)) {
  const out = {};
  for (let i = 0; i < argv.length; i++) {
    const tok = argv[i];
    if (!tok.startsWith('--')) continue;
    const key = tok.slice(2);
    const next = argv[i + 1];
    if (next === undefined || next.startsWith('--')) {
      out[key] = true;
    } else {
      out[key] = next;
      i++;
    }
  }
  return out;
}

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export async function ensureDir(dir) {
  await mkdir(dir, { recursive: true });
  return dir;
}

/** Filesystem-safe slug for filenames. */
export function slug(s) {
  return String(s).replace(/[^a-zA-Z0-9._-]+/g, '_');
}

/** Read a dotted path out of an object: getByPath(o, 'a.b.c'). */
export function getByPath(obj, dotted) {
  if (!dotted) return undefined;
  return dotted.split('.').reduce((acc, k) => (acc == null ? undefined : acc[k]), obj);
}

function toNum(v) {
  const n = typeof v === 'string' ? parseFloat(v) : v;
  return Number.isFinite(n) ? n : undefined;
}

/**
 * Pull normalized point records out of one payload using an endpoint's
 * extract specs. Returns [{ lat, lng, id?, name?, domain, entityType,
 * alt?, heading?, speed?, threat?, original }].
 */
export function extractEntities(payload, endpoint) {
  if (!payload || !Array.isArray(endpoint.extract)) return [];
  const records = [];

  for (const spec of endpoint.extract) {
    const arr = getByPath(payload, spec.arrayPath);
    if (!Array.isArray(arr)) continue;

    let idx = 0;
    for (const item of arr) {
      if (item == null || typeof item !== 'object') continue;
      idx++;

      let lat;
      let lng;
      if (spec.coordsField) {
        const pair = item[spec.coordsField];
        if (!Array.isArray(pair) || pair.length < 2) continue;
        if ((spec.coordsOrder || 'latlng') === 'lnglat') {
          lng = toNum(pair[0]);
          lat = toNum(pair[1]);
        } else {
          lat = toNum(pair[0]);
          lng = toNum(pair[1]);
        }
      } else {
        lat = toNum(item[spec.lat || 'lat']);
        lng = toNum(item[spec.lng || 'lng']);
      }
      if (lat === undefined || lng === undefined) continue;

      const rawId = spec.id ? item[spec.id] : undefined;
      const id = rawId != null && String(rawId).length > 0
        ? String(rawId)
        : `${endpoint.name}-${slug(spec.arrayPath)}-${idx}`;

      records.push({
        lat,
        lng,
        id,
        name: spec.name && item[spec.name] != null ? String(item[spec.name]) : id,
        domain: spec.domain || 'LAND',
        entityType: spec.entityType || 'TRACK',
        alt: spec.alt ? toNum(item[spec.alt]) : undefined,
        heading: spec.heading ? toNum(item[spec.heading]) : undefined,
        speed: spec.speed ? toNum(item[spec.speed]) : undefined,
        threat: spec.threat,
        original: item,
      });
    }
  }
  return records;
}

const DISPLAY_BY_DOMAIN = {
  AIR: { color: '#00E5FF', icon: 'plane-cyan', layerType: 'symbol', glow: false, scale: 1.0 },
  SEA: { color: '#26C6DA', icon: 'dot-gold', layerType: 'circle', glow: false, scale: 1.0 },
  SPACE: { color: '#D4AF37', icon: 'dot-gold', layerType: 'circle', glow: true, scale: 1.0 },
  CYBER: { color: '#D32F2F', icon: 'dot-red', layerType: 'circle', glow: true, scale: 1.0 },
  LAND: { color: '#D4AF37', icon: 'dot-gold', layerType: 'circle', glow: false, scale: 1.0 },
};

/**
 * Convert a normalized record into the flat entity shape that
 * POST /api/sdk/ingest reads (id, position.{lat,lng,...}, threat,
 * classification, confidence, timestamp, properties, display).
 */
export function toPolybolosEntity(rec, capturedAt) {
  return {
    id: rec.id,
    name: rec.name,
    domain: rec.domain,
    entityType: rec.entityType,
    position: {
      lat: rec.lat,
      lng: rec.lng,
      alt: rec.alt,
      heading: rec.heading,
      speed: rec.speed,
    },
    threat: rec.threat || 'NONE',
    classification: 'UNCLASSIFIED',
    confidence: 0.9,
    timestamp: capturedAt || new Date().toISOString(),
    properties: rec.original || {},
    display: DISPLAY_BY_DOMAIN[rec.domain] || DISPLAY_BY_DOMAIN.LAND,
  };
}

/** fetch with timeout + simple retry/backoff on 429/5xx and network errors. */
export async function fetchJson(url, { timeoutMs = 20_000, retries = 2 } = {}) {
  let attempt = 0;
  let lastErr;
  while (attempt <= retries) {
    const ac = new AbortController();
    const timer = setTimeout(() => ac.abort(), timeoutMs);
    const started = Date.now();
    try {
      const res = await fetch(url, { signal: ac.signal, cache: 'no-store' });
      clearTimeout(timer);
      const ms = Date.now() - started;
      if (res.status === 429 || res.status >= 500) {
        lastErr = new Error(`HTTP ${res.status}`);
        if (attempt < retries) {
          await sleep(500 * Math.pow(2, attempt));
          attempt++;
          continue;
        }
        return { ok: false, status: res.status, ms, payload: null };
      }
      let payload = null;
      try {
        payload = await res.json();
      } catch {
        payload = null;
      }
      return { ok: res.ok, status: res.status, ms, payload };
    } catch (e) {
      clearTimeout(timer);
      lastErr = e;
      if (attempt < retries) {
        await sleep(500 * Math.pow(2, attempt));
        attempt++;
        continue;
      }
      return { ok: false, status: 0, ms: Date.now() - started, payload: null, error: String(e?.message || e) };
    }
  }
  return { ok: false, status: 0, ms: 0, payload: null, error: String(lastErr?.message || lastErr) };
}

/** Count entities a payload would yield (for manifests/logging). */
export function countEntities(payload, endpoint) {
  if (endpoint.captureOnly || !Array.isArray(endpoint.extract)) return 0;
  return extractEntities(payload, endpoint).length;
}

/** Build a full URL from base + path + query object. */
export function buildUrl(base, ep, extraQuery) {
  const u = new URL(ep.path, base);
  const q = { ...(ep.query || {}), ...(extraQuery || {}) };
  for (const [k, v] of Object.entries(q)) u.searchParams.set(k, v);
  return u.toString();
}

export const paths = {
  root: (out) => out,
  snapshots: (out) => path.join(out, 'snapshots'),
  timeseries: (out) => path.join(out, 'timeseries'),
  polybolos: (out) => path.join(out, 'polybolos'),
  manifest: (out) => path.join(out, 'manifest.json'),
};
