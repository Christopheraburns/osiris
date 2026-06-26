import { NextRequest, NextResponse } from 'next/server';

/**
 * GraphRAG intelligence proxy.
 *
 * Forwards a flight-intel question to the feeds-gateway GraphRAG layer and
 * streams the NDJSON response back to the browser unchanged. All graph/LLM
 * logic lives in the gateway; this route only validates and pipes the stream.
 *
 * POST /api/intel  body: { entity: {...}, tier?, question? }
 */

const GATEWAY_URL =
  process.env.FEEDS_GATEWAY_URL ||
  (process.env.NODE_ENV === 'production'
    ? 'http://osiris-feeds-gateway:8091'
    : 'http://localhost:8091');

export const dynamic = 'force-dynamic';

export async function POST(request: NextRequest) {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: 'invalid json' }, { status: 400 });
  }

  try {
    const upstream = await fetch(`${GATEWAY_URL}/intel/ask`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      cache: 'no-store',
    });

    if (!upstream.ok || !upstream.body) {
      const text = await upstream.text().catch(() => '');
      return NextResponse.json(
        { error: `intel gateway ${upstream.status}`, detail: text.slice(0, 200) },
        { status: upstream.status >= 400 ? upstream.status : 502 },
      );
    }

    return new Response(upstream.body, {
      headers: {
        'Content-Type': 'application/x-ndjson',
        'Cache-Control': 'no-store',
        'X-Accel-Buffering': 'no',
      },
    });
  } catch (e) {
    return NextResponse.json(
      { error: 'intelligence layer unavailable', detail: e instanceof Error ? e.message : String(e) },
      { status: 502 },
    );
  }
}
