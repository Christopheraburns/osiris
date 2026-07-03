/**
 * Reusable logger with durable Postgres persistence.
 *
 * Writes structured log rows to the `osiris_logs.app_logs` table in the existing
 * metastore Postgres instance, so logs survive restarts (the datalake is the
 * source of truth for data; this is the operational/provenance trail). Each call
 * also mirrors to console (so `docker logs osiris` still works).
 *
 * Design:
 *  - A module-global pg Pool (long-lived standalone Next process).
 *  - Non-blocking: log calls enqueue rows; a timer flushes batched INSERTs so a
 *    request is never blocked on the DB.
 *  - Resilient: if Postgres is unavailable, rows stay in a bounded in-memory
 *    fallback buffer and are still printed to console; getLogs() merges the DB
 *    results with any buffered rows.
 *  - Secret-free: never pass API keys/tokens in `data`.
 */
import { Pool } from 'pg';

export type LogLevel = 'debug' | 'info' | 'warn' | 'error';

export interface LogRecord {
  ts: string;
  service: string;
  level: LogLevel;
  scope: string | null;
  msg: string;
  ingest_run_id: string | null;
  data: Record<string, unknown> | null;
}

const LEVEL_ORDER: Record<LogLevel, number> = { debug: 10, info: 20, warn: 30, error: 40 };
const MIN_LEVEL: LogLevel = (process.env.LOG_LEVEL as LogLevel) || 'info';
const SERVICE = 'osiris';
const FLUSH_MS = 1500;
const MAX_BUFFER = 500;

type GlobalLog = {
  pool?: Pool;
  ready?: Promise<boolean>;
  queue: LogRecord[];
  fallback: LogRecord[];
  timer?: ReturnType<typeof setInterval>;
};

const g = globalThis as unknown as { __osirisLog?: GlobalLog };
function store(): GlobalLog {
  if (!g.__osirisLog) {
    g.__osirisLog = { queue: [], fallback: [] };
  }
  return g.__osirisLog;
}

function pgConfig() {
  return {
    host: process.env.LOGS_PG_HOST || 'osiris-metastore-db',
    port: Number(process.env.LOGS_PG_PORT || 5432),
    user: process.env.LOGS_PG_USER || 'hive',
    password: process.env.LOGS_PG_PASSWORD || 'hive',
    database: process.env.LOGS_PG_DB || 'osiris_logs',
  };
}

const DDL = `
CREATE TABLE IF NOT EXISTS app_logs (
  id            BIGSERIAL PRIMARY KEY,
  ts            TIMESTAMPTZ NOT NULL DEFAULT now(),
  service       TEXT NOT NULL,
  level         TEXT NOT NULL,
  scope         TEXT,
  msg           TEXT NOT NULL,
  ingest_run_id TEXT,
  data          JSONB
);
CREATE INDEX IF NOT EXISTS app_logs_ts_idx ON app_logs (ts DESC);
CREATE INDEX IF NOT EXISTS app_logs_svc_lvl_idx ON app_logs (service, level);
`;

/** Ensure the logs database + table exist; create the DB if missing. */
async function bootstrap(): Promise<boolean> {
  const cfg = pgConfig();
  const bootstrapDb = process.env.LOGS_PG_BOOTSTRAP_DB || 'metastore';
  // 1) Ensure the database exists (connect to a known-existing db).
  const admin = new Pool({ ...cfg, database: bootstrapDb });
  try {
    const exists = await admin.query('SELECT 1 FROM pg_database WHERE datname = $1', [cfg.database]);
    if (exists.rowCount === 0) {
      try {
        await admin.query(`CREATE DATABASE ${cfg.database}`);
      } catch (e: unknown) {
        // 42P04 = duplicate_database (created concurrently) — ignore.
        if ((e as { code?: string })?.code !== '42P04') throw e;
      }
    }
  } finally {
    await admin.end().catch(() => {});
  }
  // 2) Ensure the table exists in the logs database.
  const pool = new Pool(cfg);
  await pool.query(DDL);
  store().pool = pool;
  return true;
}

function ensureReady(): Promise<boolean> {
  const s = store();
  if (!s.ready) {
    s.ready = bootstrap().catch((e) => {
      console.error('[log] Postgres bootstrap failed; using console + memory fallback:', e instanceof Error ? e.message : e);
      return false;
    });
  }
  if (!s.timer) {
    s.timer = setInterval(() => { void flush(); }, FLUSH_MS);
    // Don't keep the event loop alive solely for flushing.
    (s.timer as unknown as { unref?: () => void }).unref?.();
  }
  return s.ready;
}

async function flush(): Promise<void> {
  const s = store();
  if (s.queue.length === 0) return;
  const ok = await ensureReady();
  if (!ok || !s.pool) {
    // DB unavailable: spill to bounded fallback buffer.
    s.fallback.push(...s.queue.splice(0));
    if (s.fallback.length > MAX_BUFFER) s.fallback.splice(0, s.fallback.length - MAX_BUFFER);
    return;
  }
  // Drain anything previously buffered first, then the queue.
  const batch = [...s.fallback.splice(0), ...s.queue.splice(0)];
  if (batch.length === 0) return;
  // Build a multi-row INSERT with 7 columns per row.
  const params: unknown[] = [];
  const rows = batch.map((r, i) => {
    const o = i * 7;
    params.push(r.ts, r.service, r.level, r.scope, r.msg, r.ingest_run_id, JSON.stringify(r.data ?? null));
    return `($${o + 1}, $${o + 2}, $${o + 3}, $${o + 4}, $${o + 5}, $${o + 6}, $${o + 7}::jsonb)`;
  });
  try {
    await s.pool.query(
      `INSERT INTO app_logs (ts, service, level, scope, msg, ingest_run_id, data) VALUES ${rows.join(', ')}`,
      params,
    );
  } catch (e) {
    console.error('[log] flush failed; re-buffering:', e instanceof Error ? e.message : e);
    s.fallback.push(...batch);
    if (s.fallback.length > MAX_BUFFER) s.fallback.splice(0, s.fallback.length - MAX_BUFFER);
  }
}

function emit(level: LogLevel, scope: string, msg: string, data?: Record<string, unknown>): void {
  if (LEVEL_ORDER[level] < LEVEL_ORDER[MIN_LEVEL]) return;
  const ingest_run_id = (data?.ingest_run_id as string | undefined) ?? null;
  const rec: LogRecord = {
    ts: new Date().toISOString(),
    service: SERVICE,
    level,
    scope: scope || null,
    msg,
    ingest_run_id,
    data: data ?? null,
  };
  // Mirror to console.
  const line = `[${rec.ts}] ${level.toUpperCase()} ${scope} ${msg}`;
  if (level === 'error') console.error(line, data ?? '');
  else if (level === 'warn') console.warn(line, data ?? '');
  else console.log(line, data ?? '');

  store().queue.push(rec);
  ensureReady();
}

export const log = {
  debug: (scope: string, msg: string, data?: Record<string, unknown>) => emit('debug', scope, msg, data),
  info: (scope: string, msg: string, data?: Record<string, unknown>) => emit('info', scope, msg, data),
  warn: (scope: string, msg: string, data?: Record<string, unknown>) => emit('warn', scope, msg, data),
  error: (scope: string, msg: string, data?: Record<string, unknown>) => emit('error', scope, msg, data),
};

export interface GetLogsParams {
  q?: string;
  level?: LogLevel;
  service?: string;
  limit?: number;
}

/** Query persisted logs for the viewer; merges in-memory fallback if any. */
export async function getLogs(params: GetLogsParams): Promise<LogRecord[]> {
  const limit = Math.min(Math.max(params.limit ?? 200, 1), 1000);
  await ensureReady();
  const s = store();

  let dbRows: LogRecord[] = [];
  if (s.pool) {
    const where: string[] = [];
    const args: unknown[] = [];
    if (params.level) { args.push(params.level); where.push(`level = $${args.length}`); }
    if (params.service) { args.push(params.service); where.push(`service = $${args.length}`); }
    if (params.q) {
      args.push(`%${params.q}%`);
      const p = `$${args.length}`;
      where.push(`(msg ILIKE ${p} OR scope ILIKE ${p} OR coalesce(ingest_run_id,'') ILIKE ${p} OR coalesce(data::text,'') ILIKE ${p})`);
    }
    args.push(limit);
    const sql = `SELECT ts, service, level, scope, msg, ingest_run_id, data
                 FROM app_logs
                 ${where.length ? 'WHERE ' + where.join(' AND ') : ''}
                 ORDER BY ts DESC
                 LIMIT $${args.length}`;
    try {
      const res = await s.pool.query(sql, args);
      dbRows = res.rows.map((r) => ({
        ts: new Date(r.ts).toISOString(),
        service: r.service,
        level: r.level,
        scope: r.scope,
        msg: r.msg,
        ingest_run_id: r.ingest_run_id,
        data: r.data,
      }));
    } catch (e) {
      console.error('[log] getLogs query failed:', e instanceof Error ? e.message : e);
    }
  }

  if (dbRows.length === 0 && s.fallback.length > 0) {
    return [...s.fallback].reverse().slice(0, limit);
  }
  return dbRows;
}
