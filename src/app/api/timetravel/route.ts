import { NextResponse } from 'next/server';
import { gatewayBaseUrl } from '@/lib/connectionMode';

export const dynamic = 'force-dynamic';

/**
 * OSIRIS — TimeTravel window.
 * Proxies the feeds-gateway /history (Hive over the Iceberg lake). Passes through
 * start/end (epoch ms), optional types (comma-separated asset_type), and limit.
 */
export async function GET(req: Request) {
  const { searchParams } = new URL(req.url);
  const qs = searchParams.toString();
  const url = `${gatewayBaseUrl()}/history?${qs}`;
  try {
    const res = await fetch(url, { cache: 'no-store', signal: AbortSignal.timeout(60000) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      return NextResponse.json(
        { events: [], count: 0, error: data.detail || `gateway ${res.status}` },
        { status: res.status },
      );
    }
    return NextResponse.json(data, { headers: { 'Cache-Control': 'no-store' } });
  } catch {
    return NextResponse.json({ events: [], count: 0, error: 'history unavailable' }, { status: 503 });
  }
}
