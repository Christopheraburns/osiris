/**
 * OSIRIS Capture — transform to Polybolos ingest entities
 *
 * Reads captured data and emits captures/polybolos/<name>.ndjson, one JSON
 * object per line: { capturedAt, source, entity } where `entity` matches the
 * shape POST /api/sdk/ingest expects.
 *
 * Usage:
 *   node tools/capture/transform.mjs                       # from timeseries (default)
 *   node tools/capture/transform.mjs --from snapshot       # from latest snapshot dir
 *   node tools/capture/transform.mjs --from snapshot --snapshot <dirname>
 *   node tools/capture/transform.mjs --only flights,maritime
 *
 * Flags:
 *   --out <dir>        capture directory                 (default ./captures)
 *   --from <src>       timeseries | snapshot             (default timeseries)
 *   --snapshot <name>  specific snapshot subfolder       (default: most recent)
 *   --only <a,b,c>     restrict to these endpoint names
 */

import { readdir, readFile, writeFile, stat } from 'node:fs/promises';
import { createReadStream } from 'node:fs';
import readline from 'node:readline';
import path from 'node:path';
import { ENDPOINTS } from './endpoints.config.mjs';
import {
  parseArgs, ensureDir, slug, extractEntities, toPolybolosEntity, paths,
} from './lib.mjs';

const args = parseArgs();
const OUT = path.resolve(args.out || 'captures');
const FROM = args.from || 'timeseries';
const ONLY = args.only ? new Set(String(args.only).split(',').map((s) => s.trim())) : null;

const epByName = new Map(ENDPOINTS.map((e) => [e.name, e]));
const transformable = ENDPOINTS.filter(
  (e) => Array.isArray(e.extract) && e.extract.length > 0 && (!ONLY || ONLY.has(e.name)),
);

async function exists(p) {
  try { await stat(p); return true; } catch { return false; }
}

/** Write entities for one endpoint to its polybolos NDJSON file. */
async function writeOut(name, lines) {
  const dir = await ensureDir(paths.polybolos(OUT));
  const file = path.join(dir, `${slug(name)}.ndjson`);
  await writeFile(file, lines.join('\n') + (lines.length ? '\n' : ''));
  return { file, count: lines.length };
}

function entitiesFromPayload(ep, payload, capturedAt) {
  return extractEntities(payload, ep).map((rec) => JSON.stringify({
    capturedAt,
    source: ep.name,
    entity: toPolybolosEntity(rec, capturedAt),
  }));
}

// ── From time-series NDJSON ───────────────────────────────────────────────────
async function fromTimeseries() {
  const dir = paths.timeseries(OUT);
  if (!(await exists(dir))) {
    console.error(`No timeseries directory at ${dir}. Run capture with --mode poll first.`);
    process.exit(1);
  }
  const files = (await readdir(dir)).filter((f) => f.endsWith('.ndjson'));
  let grand = 0;

  for (const ep of transformable) {
    // time-series files may have sample suffixes; match by stem prefix
    const matches = files.filter((f) => f === `${slug(ep.name)}.ndjson` || f.startsWith(`${slug(ep.name)}-`));
    if (matches.length === 0) continue;

    const out = [];
    for (const f of matches) {
      const rl = readline.createInterface({ input: createReadStream(path.join(dir, f)), crlfDelay: Infinity });
      for await (const raw of rl) {
        if (!raw.trim()) continue;
        let row;
        try { row = JSON.parse(raw); } catch { continue; }
        if (!row.payload) continue;
        out.push(...entitiesFromPayload(ep, row.payload, row.capturedAt));
      }
    }
    if (out.length) {
      const { file, count } = await writeOut(ep.name, out);
      grand += count;
      console.log(`  ✓ ${ep.name}: ${count} entities -> ${path.relative(process.cwd(), file)}`);
    }
  }
  console.log(`\nTransform complete. ${grand} entities written to ${paths.polybolos(OUT)}`);
}

// ── From a snapshot directory ─────────────────────────────────────────────────
async function fromSnapshot() {
  const base = paths.snapshots(OUT);
  if (!(await exists(base))) {
    console.error(`No snapshots directory at ${base}. Run capture with --mode snapshot first.`);
    process.exit(1);
  }
  let sub = args.snapshot;
  if (!sub) {
    const dirs = (await readdir(base)).sort();
    sub = dirs[dirs.length - 1];
  }
  const dir = path.join(base, sub);
  if (!sub || !(await exists(dir))) {
    console.error(`Snapshot folder not found: ${dir}`);
    process.exit(1);
  }
  console.log(`[transform] snapshot ${sub}`);
  const files = (await readdir(dir)).filter((f) => f.endsWith('.json'));
  const capturedAt = new Date().toISOString();
  let grand = 0;

  for (const ep of transformable) {
    const matches = files.filter((f) => f === `${slug(ep.name)}.json` || f.startsWith(`${slug(ep.name)}-`));
    if (matches.length === 0) continue;
    const out = [];
    for (const f of matches) {
      let payload;
      try { payload = JSON.parse(await readFile(path.join(dir, f), 'utf8')); } catch { continue; }
      out.push(...entitiesFromPayload(ep, payload, capturedAt));
    }
    if (out.length) {
      const { file, count } = await writeOut(ep.name, out);
      grand += count;
      console.log(`  ✓ ${ep.name}: ${count} entities -> ${path.relative(process.cwd(), file)}`);
    }
  }
  console.log(`\nTransform complete. ${grand} entities written to ${paths.polybolos(OUT)}`);
}

async function main() {
  if (epByName.size === 0) { console.error('No endpoints configured.'); process.exit(1); }
  if (FROM === 'snapshot') await fromSnapshot();
  else await fromTimeseries();
}

main().catch((e) => {
  console.error('transform failed:', e);
  process.exit(1);
});
