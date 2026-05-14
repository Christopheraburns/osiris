import { NextResponse } from 'next/server';

/**
 * OSIRIS — Scanner Proxy (Hardened)
 * Rate-limited, target-validated, scope-restricted
 */

const SCANNER_URL = process.env.SCANNER_URL || 'http://100.68.100.15:7700';
const SCANNER_KEY = process.env.SCANNER_KEY || '';

// ── RATE LIMITER (in-memory, per-IP) ──
const rateMap = new Map<string, { count: number; resetAt: number }>();
const RATE_LIMIT = 5;          // max requests per window
const RATE_WINDOW_MS = 60_000; // 1 minute window

function isRateLimited(ip: string): boolean {
  const now = Date.now();
  const entry = rateMap.get(ip);
  if (!entry || now > entry.resetAt) {
    rateMap.set(ip, { count: 1, resetAt: now + RATE_WINDOW_MS });
    return false;
  }
  entry.count++;
  return entry.count > RATE_LIMIT;
}

// Cleanup stale entries every 5 min
setInterval(() => {
  const now = Date.now();
  for (const [ip, entry] of rateMap) {
    if (now > entry.resetAt) rateMap.delete(ip);
  }
}, 300_000);

// ── TARGET VALIDATION ──
function isPrivateOrReserved(target: string): boolean {
  // Block scanning of private/internal IPs and localhost
  const blocked = [
    /^10\./,
    /^172\.(1[6-9]|2[0-9]|3[01])\./,
    /^192\.168\./,
    /^127\./,
    /^0\./,
    /^100\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\./,  // CGNAT / Tailscale
    /^169\.254\./,   // Link-local
    /^224\./,        // Multicast
    /^255\./,
    /^localhost$/i,
    /^host\.docker\.internal$/i,
    /\.local$/i,
    /\.internal$/i,
  ];
  return blocked.some(re => re.test(target));
}

// ── ALLOWED SCAN TYPES (safe subset only) ──
const ALLOWED_SCANS: Record<string, { endpoint: string; timeout: number }> = {
  quick:      { endpoint: '/scan/quick',      timeout: 15000 },
  ssl:        { endpoint: '/scan/ssl',        timeout: 10000 },
  headers:    { endpoint: '/scan/headers',    timeout: 10000 },
  rdns:       { endpoint: '/scan/rdns',       timeout: 8000  },
  subdomains: { endpoint: '/scan/subdomains', timeout: 15000 },
  tech:       { endpoint: '/scan/tech',       timeout: 15000 },
  whois:      { endpoint: '/scan/whois',      timeout: 10000 },
  geoloc:     { endpoint: '/scan/geoloc',     timeout: 8000  },
};

// REMOVED from public access: deep, ports, banner, traceroute, vuln
// These are dangerous in an unauthenticated context:
//   deep     → scans 65,535 ports (DDoS amplifier)
//   banner   → harvests software versions from targets using our IP
//   traceroute → reveals hosting infrastructure
//   vuln     → active vulnerability probing from our server
//   ports    → arbitrary port range scanning

export async function GET(req: Request) {
  // 1. Check scanner is configured
  if (!SCANNER_KEY) {
    return NextResponse.json({ error: 'Scanner not configured', hint: 'Set SCANNER_URL and SCANNER_KEY in .env' }, { status: 503 });
  }

  // 2. Rate limit by client IP
  const forwarded = req.headers.get('x-forwarded-for');
  const clientIp = forwarded?.split(',')[0]?.trim() || req.headers.get('x-real-ip') || 'unknown';
  if (isRateLimited(clientIp)) {
    return NextResponse.json({
      error: 'Rate limit exceeded',
      detail: `Maximum ${RATE_LIMIT} scans per minute. Please wait before scanning again.`,
    }, { status: 429 });
  }

  // 3. Validate params
  const { searchParams } = new URL(req.url);
  const target = searchParams.get('target')?.trim();
  const scanType = searchParams.get('type') || 'quick';

  if (!target) {
    return NextResponse.json({ error: 'Missing target parameter' }, { status: 400 });
  }

  // 4. Block private/internal targets
  if (isPrivateOrReserved(target)) {
    return NextResponse.json({
      error: 'Target blocked',
      detail: 'Scanning private, internal, or reserved addresses is not permitted.',
    }, { status: 403 });
  }

  // 5. Validate scan type (only safe scans allowed)
  const scanConfig = ALLOWED_SCANS[scanType];
  if (!scanConfig) {
    return NextResponse.json({
      error: 'Scan type not available',
      detail: `"${scanType}" is restricted. Available: ${Object.keys(ALLOWED_SCANS).join(', ')}`,
      available_scans: Object.keys(ALLOWED_SCANS),
    }, { status: 403 });
  }

  // 6. Execute scan with tight timeout
  try {
    const params = new URLSearchParams({ key: SCANNER_KEY, target });
    const res = await fetch(`${SCANNER_URL}${scanConfig.endpoint}?${params.toString()}`, {
      signal: AbortSignal.timeout(scanConfig.timeout),
    });
    const data = await res.json();
    return NextResponse.json(data, { status: res.status });
  } catch (e: any) {
    return NextResponse.json({
      error: 'Scanner unreachable',
      detail: e.message,
    }, { status: 502 });
  }
}
