import { NextRequest, NextResponse } from 'next/server';

/**
 * Lakehouse recorder control — proxy to osiris-recorder HTTP control plane.
 *
 * GET  /api/recorder  → { recording, available, run_id?, started_at? }
 * POST /api/recorder  → { action: 'start' | 'stop' }
 *
 * The recorder container runs in idle mode by default (recording OFF until
 * explicitly started from the UI).
 */

const RECORDER_URL =
  process.env.RECORDER_URL ||
  (process.env.NODE_ENV === 'production'
    ? 'http://osiris-recorder:8090'
    : 'http://localhost:8090');

export async function GET() {
  try {
    const res = await fetch(`${RECORDER_URL}/`, { cache: 'no-store' });
    if (!res.ok) {
      return NextResponse.json(
        { recording: false, available: false, error: `recorder HTTP ${res.status}` },
        { status: 503 },
      );
    }
    const data = await res.json();
    return NextResponse.json({
      recording: Boolean(data.recording),
      available: true,
      run_id: data.run_id ?? null,
      started_at: data.started_at ?? null,
      timestamp: data.timestamp ?? new Date().toISOString(),
    });
  } catch {
    return NextResponse.json(
      { recording: false, available: false, error: 'recorder unreachable' },
      { status: 503 },
    );
  }
}

export async function POST(request: NextRequest) {
  let body: { action?: string };
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ ok: false, error: 'invalid json' }, { status: 400 });
  }

  const action = body.action;
  if (action !== 'start' && action !== 'stop') {
    return NextResponse.json(
      { ok: false, error: 'action must be start or stop' },
      { status: 400 },
    );
  }

  try {
    const res = await fetch(`${RECORDER_URL}/control`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action }),
      cache: 'no-store',
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      return NextResponse.json(
        { ok: false, error: data.error || `recorder HTTP ${res.status}` },
        { status: res.status >= 400 && res.status < 600 ? res.status : 502 },
      );
    }
    return NextResponse.json({
      ok: true,
      recording: Boolean(data.recording),
      run_id: data.run_id ?? null,
      started_at: data.started_at ?? null,
      already: data.already ?? false,
      timestamp: new Date().toISOString(),
    });
  } catch {
    return NextResponse.json(
      { ok: false, error: 'recorder unreachable' },
      { status: 503 },
    );
  }
}
