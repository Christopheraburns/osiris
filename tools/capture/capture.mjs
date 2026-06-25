/**
 * OSIRIS Capture — collector
 *
 * Captures data from the OSIRIS API endpoints in the catalog.
 *
 * Usage:
 *   node tools/capture/capture.mjs --mode both --duration 600
 *   node tools/capture/capture.mjs --mode snapshot
 *   node tools/capture/capture.mjs --mode poll --duration 1800 --only flights,maritime
 *   node tools/capture/capture.mjs --base http://localhost:3000 --include-sampled
 *
 * Flags:
 *   --base <url>          OSIRIS base URL              (default http://localhost:3000)
 *   --mode <m>           snapshot | poll | both        (default both)
 *   --duration <sec>     poll window in seconds         (default 300)
 *   --only <a,b,c>       restrict to these endpoint names
 *   --out <dir>          output directory               (default ./captures)
 *   --include-sampled    also capture param-driven endpoints using SAMPLES
 *   --timeout <ms>       per-request timeout            (default 20000)
 *   --concurrency <n>    max parallel snapshot requests (default 6)
 */

import { writeFile, appendFile } from 'node:fs/promises';
import path from 'node:path';
import { ENDPOINTS, samplesFor } from './endpoints.config.mjs';
import {
  parseArgs, ensureDir, sleep, slug, fetchJson, buildUrl,
  countEntities, paths,
} from './lib.mjs';

const args = parseArgs();
const BASE = args.base || 'http://localhost:3000';
const MODE = args.mode || 'both';
const DURATION = parseInt(args.duration || '300', 10) * 1000;
const OUT = path.resolve(args.out || 'captures');
const TIMEOUT = parseInt(args.timeout || '20000', 10);
const CONCURRENCY = Math.max(1, parseInt(args.concurrency || '6', 10));
const INCLUDE_SAMPLED = !!args['include-sampled'];
const ONLY = args.only ? new Set(String(args.only).split(',').map((s) => s.trim())) : null;

function selectedEndpoints() {
  return ENDPOINTS.filter((ep) => {
    if (ONLY && !ONLY.has(ep.name)) return false;
    if (ep.requiresInput && !INCLUDE_SAMPLED) return false;
    return true;
  });
}

/** Expand an endpoint into one-or-more concrete request targets. */
function targetsFor(ep) {
  if (!ep.requiresInput) return [{ ep, query: undefined, suffix: '' }];
  const samples = samplesFor(ep);
  return samples.map((s, i) => ({
    ep,
    query: ep.sampleParam ? ep.sampleParam(s) : s,
    suffix: `-${i}`,
  }));
}

const manifest = {
  startedAt: new Date().toISOString(),
  base: BASE,
  mode: MODE,
  durationSec: DURATION / 1000,
  endpoints: {},
};

function record(name, status, count, ms, extra) {
  const m = manifest.endpoints[name] || { requests: 0, ok: 0, failed: 0, totalEntities: 0, lastStatus: null, lastMs: 0 };
  m.requests++;
  if (status >= 200 && status < 400) m.ok++; else m.failed++;
  m.totalEntities += count || 0;
  m.lastStatus = status;
  m.lastMs = ms;
  if (extra) m.note = extra;
  manifest.endpoints[name] = m;
}

async function runPool(items, worker, concurrency) {
  const queue = [...items];
  const runners = Array.from({ length: Math.min(concurrency, queue.length) }, async () => {
    while (queue.length) {
      const item = queue.shift();
      await worker(item);
    }
  });
  await Promise.all(runners);
}

// ── Snapshot mode ───────────────────────────────────────────────────────────
async function snapshot() {
  const stamp = new Date().toISOString().replace(/[:.]/g, '-');
  const dir = await ensureDir(path.join(paths.snapshots(OUT), stamp));
  const eps = selectedEndpoints();
  const targets = eps.flatMap(targetsFor);

  console.log(`[snapshot] ${targets.length} requests -> ${dir}`);
  await runPool(targets, async ({ ep, query, suffix }) => {
    const url = buildUrl(BASE, ep, query);
    const res = await fetchJson(url, { timeoutMs: TIMEOUT });
    const count = res.ok ? countEntities(res.payload, ep) : 0;
    record(ep.name, res.status, count, res.ms, res.error);
    if (res.ok && res.payload != null) {
      const file = path.join(dir, `${slug(ep.name)}${suffix}.json`);
      await writeFile(file, JSON.stringify(res.payload, null, 2));
      console.log(`  ✓ ${ep.name}${suffix} [${res.status}] ${count} entities ${res.ms}ms`);
    } else {
      console.log(`  ✗ ${ep.name}${suffix} [${res.status}] ${res.error || 'no body'}`);
    }
  }, CONCURRENCY);

  manifest.snapshotDir = dir;
}

// ── Poll mode (time-series) ──────────────────────────────────────────────────
async function poll() {
  const dir = await ensureDir(paths.timeseries(OUT));
  const eps = selectedEndpoints();
  const endAt = Date.now() + DURATION;

  console.log(`[poll] ${eps.length} endpoints for ${DURATION / 1000}s -> ${dir}`);

  // Each endpoint runs on its own cadence until the window closes.
  const loops = eps.map((ep) => (async () => {
    const interval = Math.max(5_000, ep.pollMs || 60_000);
    const targets = targetsFor(ep);
    while (Date.now() < endAt) {
      for (const { query, suffix } of targets) {
        const url = buildUrl(BASE, ep, query);
        const res = await fetchJson(url, { timeoutMs: TIMEOUT });
        const capturedAt = new Date().toISOString();
        const count = res.ok ? countEntities(res.payload, ep) : 0;
        record(ep.name, res.status, count, res.ms, res.error);
        if (res.ok && res.payload != null) {
          const line = JSON.stringify({ capturedAt, status: res.status, count, payload: res.payload }) + '\n';
          await appendFile(path.join(dir, `${slug(ep.name)}${suffix}.ndjson`), line);
          console.log(`  ✓ ${ep.name}${suffix} [${res.status}] ${count} @ ${capturedAt}`);
        } else {
          console.log(`  ✗ ${ep.name}${suffix} [${res.status}] ${res.error || 'no body'}`);
        }
      }
      // Sleep the interval, but wake up promptly when the window ends.
      const remaining = endAt - Date.now();
      if (remaining <= 0) break;
      await sleep(Math.min(interval, remaining));
    }
  })());

  await Promise.all(loops);
}

async function main() {
  await ensureDir(OUT);
  console.log(`OSIRIS capture | base=${BASE} mode=${MODE} out=${OUT}`);

  if (MODE === 'snapshot' || MODE === 'both') await snapshot();
  if (MODE === 'poll' || MODE === 'both') await poll();

  manifest.finishedAt = new Date().toISOString();
  await writeFile(paths.manifest(OUT), JSON.stringify(manifest, null, 2));

  const totals = Object.values(manifest.endpoints).reduce(
    (a, m) => ({ req: a.req + m.requests, ok: a.ok + m.ok, ent: a.ent + m.totalEntities }),
    { req: 0, ok: 0, ent: 0 },
  );
  console.log(`\nDone. requests=${totals.req} ok=${totals.ok} entities=${totals.ent}`);
  console.log(`Manifest: ${paths.manifest(OUT)}`);
}

main().catch((e) => {
  console.error('capture failed:', e);
  process.exit(1);
});
