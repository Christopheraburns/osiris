import { NextRequest, NextResponse } from 'next/server';

/**
 * LLM test proxy — health check and raw prompt streaming via the feeds-gateway.
 *
 * GET  /api/llm  → gateway /llm/health (model name, Ollama availability)
 * POST /api/llm  → gateway /llm/chat  (NDJSON stream: meta, token, done)
 */

const GATEWAY_URL =
  process.env.FEEDS_GATEWAY_URL ||
  (process.env.NODE_ENV === 'production'
    ? 'http://osiris-feeds-gateway:8091'
    : 'http://localhost:8091');

export const dynamic = 'force-dynamic';

export async function GET() {
  try {
    const upstream = await fetch(`${GATEWAY_URL}/llm/health`, { cache: 'no-store' });
    const data = await upstream.json().catch(() => ({}));
    if (!upstream.ok) {
      return NextResponse.json(
        { error: 'llm health check failed', detail: data },
        { status: upstream.status >= 400 ? upstream.status : 502 },
      );
    }
    return NextResponse.json(data);
  } catch (e) {
    return NextResponse.json(
      { error: 'llm layer unavailable', detail: e instanceof Error ? e.message : String(e) },
      { status: 502 },
    );
  }
}

export async function POST(request: NextRequest) {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: 'invalid json' }, { status: 400 });
  }

  try {
    const upstream = await fetch(`${GATEWAY_URL}/llm/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      cache: 'no-store',
    });

    if (!upstream.ok || !upstream.body) {
      const text = await upstream.text().catch(() => '');
      return NextResponse.json(
        { error: `llm gateway ${upstream.status}`, detail: text.slice(0, 200) },
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
      { error: 'llm layer unavailable', detail: e instanceof Error ? e.message : String(e) },
      { status: 502 },
    );
  }
}
