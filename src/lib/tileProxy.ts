/** Basemap style URLs and optional CARTO → proxy rewriting (legacy). */

/** OpenFreeMap dark — used when the primary basemap fails to load. */
export const OPENFREEMAP_DARK_STYLE =
  'https://tiles.openfreemap.org/styles/dark';

const LOCAL_BASEMAP_DEFAULT =
  'http://localhost:8080/styles/dark/style.json';

/** Style JSON URL for MapLibre (local tileserver by default). */
export function basemapStyleUrl(): string {
  const fromEnv = process.env.NEXT_PUBLIC_BASEMAP_STYLE_URL?.trim();
  return fromEnv || LOCAL_BASEMAP_DEFAULT;
}

/** True when the basemap is served locally (no CARTO proxy needed). */
export function usesLocalBasemap(styleUrl = basemapStyleUrl()): boolean {
  try {
    const host = new URL(styleUrl).hostname.toLowerCase();
    return host === 'localhost' || host === '127.0.0.1' || host.endsWith('.local');
  } catch {
    return !styleUrl.includes('cartocdn.com');
  }
}

/** nginx osiris-cache tile proxy base (host :8080 by default). */
export function tileCacheBase(): string {
  if (typeof window === 'undefined') return '';
  const fromEnv = process.env.NEXT_PUBLIC_TILE_CACHE_URL?.trim();
  if (fromEnv === 'disabled') return '';
  if (fromEnv) return fromEnv.replace(/\/$/, '');
  const { protocol, hostname } = window.location;
  return `${protocol}//${hostname}:8080`;
}

/** Rewrite a cartocdn.com URL to the nginx allowlisted proxy path. */
export function nginxCartoProxyUrl(originalUrl: string): string | null {
  try {
    const parsed = new URL(originalUrl);
    const host = parsed.hostname.toLowerCase();
    if (host !== 'cartocdn.com' && !host.endsWith('.cartocdn.com')) return null;
    const base = tileCacheBase();
    if (!base) return null;
    return `${base}/proxy/tiles/${parsed.hostname}${parsed.pathname}${parsed.search}`;
  } catch {
    return null;
  }
}

/** MapLibre transformRequest for legacy CARTO basemaps only. */
export function transformCartoRequest(url: string): { url: string } {
  const nginxUrl = nginxCartoProxyUrl(url);
  if (nginxUrl) return { url: nginxUrl };

  if (url.includes('cartocdn.com') && typeof window !== 'undefined') {
    const baseUrl = window.location.origin;
    return { url: `${baseUrl}/api/proxy-tiles?url=${encodeURIComponent(url)}` };
  }
  return { url };
}

/** Pass-through for local tileserver; CARTO proxy rewrite otherwise. */
export function transformBasemapRequest(url: string): { url: string } {
  if (usesLocalBasemap()) return { url };
  return transformCartoRequest(url);
}
