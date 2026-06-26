import { NextRequest, NextResponse } from 'next/server';
import { getLogs, type LogLevel } from '@/lib/log';

/**
 * Persisted log query for the /logs viewer.
 *
 * GET /api/logs?q=&level=&service=&limit=
 * Reads the shared osiris_logs.app_logs table (both OSIRIS and the feeds-gateway
 * write to it). `q` does a substring match across msg/scope/ingest_run_id/data.
 */

export const dynamic = 'force-dynamic';

const LEVELS = new Set(['debug', 'info', 'warn', 'error']);

export async function GET(request: NextRequest) {
  const sp = request.nextUrl.searchParams;
  const q = sp.get('q') || undefined;
  const levelParam = sp.get('level') || undefined;
  const level = levelParam && LEVELS.has(levelParam) ? (levelParam as LogLevel) : undefined;
  const service = sp.get('service') || undefined;
  const limit = sp.get('limit') ? Number(sp.get('limit')) : undefined;

  try {
    const logs = await getLogs({ q, level, service, limit });
    return NextResponse.json({ logs, count: logs.length, timestamp: new Date().toISOString() });
  } catch (e) {
    return NextResponse.json(
      { logs: [], count: 0, error: e instanceof Error ? e.message : 'log query failed' },
      { status: 500 },
    );
  }
}
