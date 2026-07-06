import { NextResponse } from 'next/server';
import { gatewayBaseUrl } from '@/lib/connectionMode';

export const dynamic = 'force-dynamic';

/**
 * OSIRIS — TimeTravel window preload.
 * Asks the feeds-gateway to pull a time window into its in-memory replay buffer
 * (one Hive query), after which /history chunk reads in that range are served
 * from RAM. This is the slow call (Tez cold-start); scrubbing after is instant.
 */
export async function GET(req: Request) {
  const { searchParams } = new URL(req.url);
  const qs = searchParams.toString();
  const url = `${gatewayBaseUrl()}/history/load?${qs}`;
  try {
    // Cold Tez load of a multi-hour window can take a while — give it room.
    const res = await fetch(url, { cache: 'no-store', signal: AbortSignal.timeout(150000) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      return NextResponse.json({ count: 0, error: data.detail || `gateway ${res.status}` }, { status: res.status });
    }
    return NextResponse.json(data, { headers: { 'Cache-Control': 'no-store' } });
  } catch {
    return NextResponse.json({ count: 0, error: 'history unavailable' }, { status: 503 });
  }
}
