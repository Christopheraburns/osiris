/**
 * SECURED CONNECTION mode — direct upstream vs streaming lakehouse.
 *
 * When secured mode is OFF, OSIRIS /api/* routes fetch upstream APIs directly
 * (the original architecture). When ON, *migrated* feeds are served from the
 * feeds-gateway (NiFi -> Kafka -> gateway), while not-yet-migrated feeds fall
 * back to their direct fetch so the app stays usable. This makes the toggle a
 * live demonstration of the migration to the streaming lakehouse.
 *
 * The mode flag is a process-global singleton (single standalone node process),
 * the same pattern used by the SDK entity store. It is server-wide, like the
 * recorder toggle, and resets to OFF on restart.
 */

type ConnectionModeState = { secured: boolean };

const globalForMode = globalThis as unknown as {
  __osirisConnectionMode?: ConnectionModeState;
};

function state(): ConnectionModeState {
  if (!globalForMode.__osirisConnectionMode) {
    globalForMode.__osirisConnectionMode = { secured: false };
  }
  return globalForMode.__osirisConnectionMode;
}

/** Feeds that have a NiFi -> Kafka -> gateway path. Extend as feeds migrate. */
export const MIGRATED_FEEDS = ['earthquakes'] as const;
export type MigratedFeed = (typeof MIGRATED_FEEDS)[number];

export function isSecured(): boolean {
  return state().secured;
}

export function setSecured(secured: boolean): boolean {
  state().secured = secured;
  return state().secured;
}

export function isFeedMigrated(feed: string): boolean {
  return (MIGRATED_FEEDS as readonly string[]).includes(feed);
}

/** Should this feed be served from the gateway right now? */
export function shouldUseGateway(feed: string): boolean {
  return isSecured() && isFeedMigrated(feed);
}

export function gatewayBaseUrl(): string {
  return (
    process.env.FEEDS_GATEWAY_URL ||
    (process.env.NODE_ENV === 'production'
      ? 'http://osiris-feeds-gateway:8091'
      : 'http://localhost:8091')
  );
}

/**
 * Fetch a feed from the gateway. On any failure, returns the provided empty
 * shape stamped with `securedNoData: true` so the caller/UI can degrade
 * gracefully instead of throwing.
 */
export async function securedProxy<T extends Record<string, unknown>>(
  feedPath: string,
  emptyShape: T,
): Promise<T & { source?: string; securedNoData?: boolean }> {
  const url = `${gatewayBaseUrl()}${feedPath}`;
  try {
    const res = await fetch(url, { cache: 'no-store', signal: AbortSignal.timeout(8000) });
    if (!res.ok) {
      return { ...emptyShape, source: 'streaming-lakehouse', securedNoData: true };
    }
    const data = await res.json();
    return { ...emptyShape, ...data, source: 'streaming-lakehouse' };
  } catch {
    return { ...emptyShape, source: 'streaming-lakehouse', securedNoData: true };
  }
}
