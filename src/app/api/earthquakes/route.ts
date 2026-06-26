
import { NextResponse } from 'next/server';
import { shouldUseGateway, securedProxy } from '@/lib/connectionMode';
import { log } from '@/lib/log';

/**
 * OSIRIS — Earthquake Data API
 * Fetches real-time seismic events from USGS (last 24h, M2.5+)
 * No API key required
 *
 * In SECURED CONNECTION mode (and once earthquakes is a migrated feed) this
 * serves data from the feeds-gateway (NiFi -> Kafka -> gateway) instead of
 * calling USGS directly. Each request is logged with its provenance so the
 * data path (streaming lakehouse vs direct USGS) is auditable in /logs.
 */

export async function GET() {
  if (shouldUseGateway('earthquakes')) {
    const data = await securedProxy<{ earthquakes: unknown[]; total: number }>(
      '/feeds/earthquakes',
      { earthquakes: [], total: 0 },
    );
    const prov = (data as { provenance?: { ingest_run_id?: string; captured_at?: string } }).provenance;
    const count = Array.isArray(data.earthquakes) ? data.earthquakes.length : 0;
    log.info('earthquakes', 'served from streaming lakehouse (gateway)', {
      count,
      source: 'streaming-lakehouse',
      ingest_run_id: prov?.ingest_run_id ?? null,
      captured_at: prov?.captured_at ?? null,
      securedNoData: (data as { securedNoData?: boolean }).securedNoData ?? false,
    });
    return NextResponse.json(data, {
      headers: { 'Cache-Control': 'no-store' },
    });
  }

  try {
    const url = 'https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_day.geojson';
    const res = await fetch(url, {
      signal: AbortSignal.timeout(10000),
    });

    if (!res.ok) {
      return NextResponse.json({ earthquakes: [], error: 'USGS unavailable' });
    }

    const data = await res.json();
    const features = data.features || [];

    const earthquakes = features.map((f: any) => {
      const coords = f.geometry?.coordinates || [0, 0, 0];
      const props = f.properties || {};
      return {
        id: f.id,
        lat: coords[1],
        lng: coords[0],
        depth: coords[2],
        magnitude: props.mag,
        place: props.place,
        time: props.time,
        url: props.url,
        tsunami: props.tsunami,
        type: props.type,
        felt: props.felt,
        alert: props.alert,
      };
    });

    log.info('earthquakes', 'served from USGS direct', {
      count: earthquakes.length,
      source: 'direct-usgs',
    });
    return NextResponse.json({
      earthquakes,
      total: earthquakes.length,
      source: 'direct-usgs',
      timestamp: new Date().toISOString(),
    }, {
      headers: {
        'Cache-Control': 'public, s-maxage=60, stale-while-revalidate=120',
      },
    });
  } catch (error) {
    log.error('earthquakes', 'USGS fetch failed', {
      error: error instanceof Error ? error.message : String(error),
    });
    return NextResponse.json({ earthquakes: [], error: 'Failed to fetch earthquake data' }, { status: 500 });
  }
}

