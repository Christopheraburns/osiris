import { NextRequest, NextResponse } from 'next/server';
import { isSecured, setSecured, MIGRATED_FEEDS, gatewayBaseUrl } from '@/lib/connectionMode';

/**
 * SECURED CONNECTION mode control.
 *
 * GET  /api/connection-mode  -> { secured, available, migratedFeeds }
 * POST /api/connection-mode  -> { action: 'secure' | 'direct' }
 *
 * `secured` is a server-wide flag (like recording). When ON, migrated feeds are
 * served from the feeds-gateway and all other layers are suppressed in the UI.
 *
 * Persistence to the lake is handled independently by the Flink Kafka -> Iceberg
 * job (a separate consumer of osiris.entities), not by this toggle.
 */

export const dynamic = 'force-dynamic';

async function gatewayAvailable(): Promise<boolean> {
  try {
    const res = await fetch(`${gatewayBaseUrl()}/health`, {
      cache: 'no-store',
      signal: AbortSignal.timeout(3000),
    });
    return res.ok;
  } catch {
    return false;
  }
}

export async function GET() {
  return NextResponse.json({
    secured: isSecured(),
    available: await gatewayAvailable(),
    migratedFeeds: MIGRATED_FEEDS,
    timestamp: new Date().toISOString(),
  });
}

export async function POST(request: NextRequest) {
  let body: { action?: string };
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ ok: false, error: 'invalid json' }, { status: 400 });
  }

  const action = body.action;
  if (action !== 'secure' && action !== 'direct') {
    return NextResponse.json(
      { ok: false, error: "action must be 'secure' or 'direct'" },
      { status: 400 },
    );
  }

  const secured = setSecured(action === 'secure');
  return NextResponse.json({
    ok: true,
    secured,
    migratedFeeds: MIGRATED_FEEDS,
    timestamp: new Date().toISOString(),
  });
}
