import { NextResponse } from 'next/server';
import { gatewayBaseUrl } from '@/lib/connectionMode';

export const dynamic = 'force-dynamic';

/**
 * OSIRIS — TimeTravel bounds.
 * Proxies the feeds-gateway /history/bounds (Hive over the Iceberg lake). Always
 * reads the lake via the gateway, independent of Secure Mode.
 */
export async function GET() {
  const url = `${gatewayBaseUrl()}/history/bounds`;
  try {
    // Hive/Tez cold-start can take 20-30s for the MIN/MAX/COUNT — give it room.
    const res = await fetch(url, { cache: 'no-store', signal: AbortSignal.timeout(90000) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      return NextResponse.json(
        { min_time: null, max_time: null, count: 0, error: data.detail || `gateway ${res.status}` },
        { status: res.status },
      );
    }
    return NextResponse.json(data, { headers: { 'Cache-Control': 'no-store' } });
  } catch {
    return NextResponse.json(
      { min_time: null, max_time: null, count: 0, error: 'history unavailable' },
      { status: 503 },
    );
  }
}
