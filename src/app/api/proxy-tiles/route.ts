import { NextRequest, NextResponse } from 'next/server';

const TILE_TIMEOUT_MS = 30_000;
const MAX_ATTEMPTS = 3;
const RETRY_DELAY_MS = 500;

function isAllowedCartoHost(host: string): boolean {
  const h = host.toLowerCase();
  return h === 'cartocdn.com' || h.endsWith('.cartocdn.com');
}

async function fetchTile(targetUrl: string): Promise<Response> {
  let lastError: unknown;
  for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt++) {
    try {
      const response = await fetch(targetUrl, {
        headers: {
          Accept: '*/*',
          'User-Agent': 'Osiris-Tile-Proxy/1.0',
        },
        signal: AbortSignal.timeout(TILE_TIMEOUT_MS),
        next: { revalidate: 31536000 },
      });
      if (response.ok || response.status === 304) return response;
      if (response.status >= 500 && attempt < MAX_ATTEMPTS) {
        await new Promise((r) => setTimeout(r, RETRY_DELAY_MS * attempt));
        continue;
      }
      return response;
    } catch (error) {
      lastError = error;
      if (attempt < MAX_ATTEMPTS) {
        await new Promise((r) => setTimeout(r, RETRY_DELAY_MS * attempt));
        continue;
      }
    }
  }
  throw lastError ?? new Error('Tile fetch failed');
}

export async function GET(request: NextRequest) {
  const url = request.nextUrl.searchParams.get('url');

  if (!url) {
    return NextResponse.json({ error: 'Missing url parameter' }, { status: 400 });
  }

  try {
    const targetUrl = new URL(url);
    if (!isAllowedCartoHost(targetUrl.hostname)) {
      return NextResponse.json({ error: 'Forbidden domain' }, { status: 403 });
    }

    const response = await fetchTile(targetUrl.toString());

    if (!response.ok) {
      return NextResponse.json(
        { error: 'Failed to fetch tile' },
        { status: response.status, headers: { 'Retry-After': '30' } },
      );
    }

    const data = await response.arrayBuffer();
    const contentType = response.headers.get('content-type') || 'application/octet-stream';

    return new NextResponse(data, {
      status: 200,
      headers: {
        'Content-Type': contentType,
        'Cache-Control': 'public, max-age=31536000, immutable',
        'Access-Control-Allow-Origin': '*',
      },
    });
  } catch (error) {
    console.error('Tile proxy error:', error);
    return NextResponse.json(
      { error: 'Upstream tile fetch failed' },
      { status: 502, headers: { 'Retry-After': '15' } },
    );
  }
}
