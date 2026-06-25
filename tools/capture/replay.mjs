/**
 * OSIRIS Capture — replay into POST /api/sdk/ingest
 *
 * Reads captures/polybolos/*.ndjson and pushes the entities into a running
 * OSIRIS instance through the Polybolos SDK ingest endpoint. Use this to prove
 * the round-trip before wiring Kafka/NiFi.
 *
 * Usage:
 *   node tools/capture/replay.mjs --key <SDK_INGEST_KEY>
 *   node tools/capture/replay.mjs --key abc --speed 10        # time-ordered, 10x
 *   node tools/capture/replay.mjs --key abc --rate 200 --loop # flood at 200/s, forever
 *   node tools/capture/replay.mjs --key abc --only flights,maritime
 *
 * Flags:
 *   --base <url>     OSIRIS base URL                (default http://localhost:3000)
 *   --key <secret>   SDK_INGEST_KEY (or env SDK_INGEST_KEY)
 *   --out <dir>      capture directory              (default ./captures)
 *   --only <a,b,c>   restrict to these endpoint sources
 *   --batch <n>      entities per POST              (default 100)
 *   --rate <n>       max entities/sec (flat mode)   (default unthrottled)
 *   --speed <x>      replay by capturedAt at x speed (e.g. 10). Omit for flat mode.
 *   --loop           repeat the whole dataset forever
 *   --source <name>  override the `source` sent to the API (default per-file source)
 */

import { readdir, stat } from 'node:fs/promises';
import { createReadStream } from 'node:fs';
import readline from 'node:readline';
import path from 'node:path';
import { parseArgs, sleep, paths } from './lib.mjs';

const args = parseArgs();
const BASE = args.base || 'http://localhost:3000';
const KEY = args.key || process.env.SDK_INGEST_KEY;
const OUT = path.resolve(args.out || 'captures');
const ONLY = args.only ? new Set(String(args.only).split(',').map((s) => s.trim())) : null;
const BATCH = Math.max(1, parseInt(args.batch || '100', 10));
const RATE = args.rate ? Math.max(1, parseInt(args.rate, 10)) : 0;
const SPEED = args.speed ? Math.max(0.1, parseFloat(args.speed)) : 0;
const LOOP = !!args.loop;
const SOURCE_OVERRIDE = args.source || null;

if (!KEY) {
  console.error('Missing ingest key. Pass --key <secret> or set SDK_INGEST_KEY env var.');
  console.error('It must match SDK_INGEST_KEY configured on the osiris container.');
  process.exit(1);
}

const ingestUrl = new URL('/api/sdk/ingest', BASE).toString();
const tally = { posted: 0, accepted: 0, rejected: 0, errors: 0 };

async function exists(p) {
  try { await stat(p); return true; } catch { return false; }
}

/** Load every polybolos line as { capturedAt, source, entity }. */
async function loadLines() {
  const dir = paths.polybolos(OUT);
  if (!(await exists(dir))) {
    console.error(`No polybolos directory at ${dir}. Run transform.mjs first.`);
    process.exit(1);
  }
  const files = (await readdir(dir)).filter((f) => f.endsWith('.ndjson'));
  const lines = [];
  for (const f of files) {
    const name = f.replace(/\.ndjson$/, '');
    if (ONLY && !ONLY.has(name)) continue;
    const rl = readline.createInterface({ input: createReadStream(path.join(dir, f)), crlfDelay: Infinity });
    for await (const raw of rl) {
      if (!raw.trim()) continue;
      try {
        const row = JSON.parse(raw);
        if (row.entity) lines.push(row);
      } catch { /* skip */ }
    }
  }
  return lines;
}

async function postBatch(source, entities) {
  if (entities.length === 0) return;
  try {
    const res = await fetch(ingestUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ source: SOURCE_OVERRIDE || source, apiKey: KEY, entities }),
    });
    const json = await res.json().catch(() => ({}));
    if (!res.ok) {
      tally.errors++;
      console.log(`  ✗ [${res.status}] ${source}: ${(json.errors || []).join('; ') || res.statusText}`);
      return;
    }
    tally.posted++;
    tally.accepted += json.accepted || 0;
    tally.rejected += json.rejected || 0;
    console.log(`  ✓ ${source}: accepted=${json.accepted} rejected=${json.rejected} (entities=${entities.length})`);
  } catch (e) {
    tally.errors++;
    console.log(`  ✗ ${source}: ${e?.message || e}`);
  }
}

/** Flat mode: push everything grouped by source, throttled by --rate. */
async function replayFlat(lines) {
  const bySource = new Map();
  for (const { source, entity } of lines) {
    if (!bySource.has(source)) bySource.set(source, []);
    bySource.get(source).push(entity);
  }
  for (const [source, entities] of bySource) {
    for (let i = 0; i < entities.length; i += BATCH) {
      const chunk = entities.slice(i, i + BATCH);
      await postBatch(source, chunk);
      if (RATE) await sleep((chunk.length / RATE) * 1000);
    }
  }
}

/** Time-ordered mode: replay by capturedAt, scaled by --speed. */
async function replayTimed(lines) {
  const sorted = [...lines].sort((a, b) => new Date(a.capturedAt) - new Date(b.capturedAt));
  // Group consecutive lines that share the same capturedAt into frames.
  const frames = [];
  let cur = null;
  for (const row of sorted) {
    if (!cur || cur.t !== row.capturedAt) {
      cur = { t: row.capturedAt, rows: [] };
      frames.push(cur);
    }
    cur.rows.push(row);
  }

  console.log(`[replay] timed mode: ${frames.length} frames at ${SPEED}x`);
  for (let i = 0; i < frames.length; i++) {
    const frame = frames[i];
    const bySource = new Map();
    for (const { source, entity } of frame.rows) {
      if (!bySource.has(source)) bySource.set(source, []);
      bySource.get(source).push(entity);
    }
    for (const [source, entities] of bySource) {
      for (let j = 0; j < entities.length; j += BATCH) {
        await postBatch(source, entities.slice(j, j + BATCH));
      }
    }
    const next = frames[i + 1];
    if (next) {
      const gapMs = new Date(next.t) - new Date(frame.t);
      const wait = Math.max(0, gapMs / SPEED);
      if (wait > 0) await sleep(Math.min(wait, 60_000));
    }
  }
}

async function main() {
  const lines = await loadLines();
  if (lines.length === 0) {
    console.error('No entities to replay. Did transform.mjs produce captures/polybolos/*.ndjson?');
    process.exit(1);
  }
  console.log(`OSIRIS replay | ${lines.length} entities -> ${ingestUrl} | mode=${SPEED ? `timed ${SPEED}x` : 'flat'}${LOOP ? ' (loop)' : ''}`);

  let pass = 0;
  do {
    pass++;
    if (LOOP) console.log(`\n--- pass ${pass} ---`);
    if (SPEED) await replayTimed(lines);
    else await replayFlat(lines);
  } while (LOOP);

  console.log(`\nDone. POSTs=${tally.posted} accepted=${tally.accepted} rejected=${tally.rejected} errors=${tally.errors}`);
  console.log(`Verify count: GET ${new URL('/api/sdk/ingest', BASE).toString()}`);
}

main().catch((e) => {
  console.error('replay failed:', e);
  process.exit(1);
});
