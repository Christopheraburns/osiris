'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { Play, Pause, X } from 'lucide-react';

/** One historical event row from the lake (feeds-gateway /history). */
export interface HistEvent {
  asset_id: string;
  asset_type: string;
  lat: number;
  lng: number;
  t: number; // epoch ms (event_time)
  source_feed?: string;
  name?: string; // display label (callsign / vessel name) from the lake payload
}

interface Props {
  onFrame: (events: HistEvent[]) => void;
  onExit: () => void;
}

const SPEEDS = [1, 10, 60, 300, 1200];
// How close to the playhead a report must be to count as "present at this moment".
// The frame shows each asset's latest report within [T - TRAIL_MS, T]. This MUST be
// >= the lake's per-asset sample interval or assets vanish between samples (an empty
// static frame). The OSIRIS flight feed lands in the lake roughly every ~2 min, so
// anything below that empties the picture — 120s is the safe floor. The "too many"
// count is driven by scope (the lake holds the full firehose), not this window.
const TRAIL_MS = 120_000;
const TICK_MS = 100;
const CHUNK_MS = 5 * 60_000;
const CHUNK_LIMIT = 20000;
const DEFAULT_WINDOW_MS = 2 * 60 * 60_000;

// Feed picker — maps a friendly domain to the lake's source_feed value.
const FEED_OPTIONS: { key: string; label: string }[] = [
  { key: 'flights', label: 'Aviation' },
  { key: 'vessels', label: 'Maritime' },
  { key: 'fires', label: 'Fires' },
  { key: 'weather', label: 'Weather' },
  { key: 'earthquakes', label: 'Earthquakes' },
];

const fmt = (ms: number) => new Date(ms).toISOString().replace('T', ' ').slice(0, 19) + ' UTC';
const chunkKeyOf = (t: number) => Math.floor(t / CHUNK_MS) * CHUNK_MS;

const toInput = (ms: number) => {
  const d = new Date(ms);
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getUTCFullYear()}-${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())}T${p(d.getUTCHours())}:${p(d.getUTCMinutes())}`;
};
const fromInput = (v: string): number | null => {
  if (!v) return null;
  const withSec = v.length === 16 ? `${v}:00` : v;
  const ms = Date.parse(`${withSec}Z`);
  return Number.isNaN(ms) ? null : ms;
};

export default function TimeTravelBar({ onFrame, onExit }: Props) {
  const [status, setStatus] = useState<'select' | 'loading' | 'ready' | 'error'>('select');
  const [errorMsg, setErrorMsg] = useState('');
  const [bounds, setBounds] = useState<{ min: number; max: number; count: number } | null>(null);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [playhead, setPlayhead] = useState(0);
  const [buffering, setBuffering] = useState(false);
  const [preparing, setPreparing] = useState(false);
  const [loaded, setLoaded] = useState<{ start: number; end: number } | null>(null);
  const [feedSel, setFeedSel] = useState<Record<string, boolean>>({ flights: true }); // Aviation by default (light)

  const chunksRef = useRef<Map<number, HistEvent[]>>(new Map());
  const inFlightRef = useRef<Set<number>>(new Set());
  const loadedRef = useRef<{ start: number; end: number } | null>(null);
  const feedsRef = useRef<string[]>([]);
  const playheadRef = useRef(0);
  const playingRef = useRef(false);
  const speedRef = useRef(1);
  playingRef.current = playing;
  speedRef.current = speed;

  const feedsQuery = () => (feedsRef.current.length ? `&feeds=${feedsRef.current.join(',')}` : '');
  const inLoaded = (t: number) => {
    const r = loadedRef.current;
    return !!r && t >= r.start && t <= r.end;
  };

  const eventsInRange = useCallback((from: number, to: number): HistEvent[] => {
    const out: HistEvent[] = [];
    for (let k = chunkKeyOf(from); k <= chunkKeyOf(to); k += CHUNK_MS) {
      const arr = chunksRef.current.get(k);
      if (!arr) continue;
      for (let i = 0; i < arr.length; i++) {
        const e = arr[i];
        if (e.t >= from && e.t <= to) out.push(e);
      }
    }
    return out;
  }, []);

  const emitFrame = useCallback(
    (head: number) => {
      const evs = eventsInRange(head - TRAIL_MS, head);
      const latest = new Map<string, HistEvent>();
      for (const e of evs) {
        const prev = latest.get(e.asset_id);
        if (!prev || e.t > prev.t) latest.set(e.asset_id, e);
      }
      onFrame(Array.from(latest.values()));
    },
    [eventsInRange, onFrame],
  );

  const loadChunk = useCallback(
    async (key: number) => {
      if (key < 0 || chunksRef.current.has(key) || inFlightRef.current.has(key)) return;
      inFlightRef.current.add(key);
      setBuffering(true);
      try {
        const r = await fetch(`/api/timetravel?start=${key}&end=${key + CHUNK_MS}&limit=${CHUNK_LIMIT}${feedsQuery()}`, { cache: 'no-store' });
        const data = await r.json();
        if (r.ok) {
          const evs: HistEvent[] = (data.events || [])
            .filter((e: HistEvent) => e.t != null)
            .sort((a: HistEvent, b: HistEvent) => a.t - b.t);
          chunksRef.current.set(key, evs);
          if (key <= playheadRef.current && key + CHUNK_MS >= playheadRef.current - TRAIL_MS) emitFrame(playheadRef.current);
        }
      } catch {
        /* retry on a later ensure() */
      } finally {
        inFlightRef.current.delete(key);
        if (inFlightRef.current.size === 0) setBuffering(false);
      }
    },
    [emitFrame],
  );

  const ensureChunks = useCallback(
    (head: number, prefetch: boolean) => {
      loadChunk(chunkKeyOf(head));
      loadChunk(chunkKeyOf(head - TRAIL_MS));
      if (prefetch) loadChunk(chunkKeyOf(head) + CHUNK_MS);
    },
    [loadChunk],
  );

  const setHead = useCallback(
    (head: number) => {
      playheadRef.current = head;
      setPlayhead(head);
      ensureChunks(head, playingRef.current);
      emitFrame(head);
    },
    [ensureChunks, emitFrame],
  );

  const loadWindow = useCallback(
    async (start: number, end: number) => {
      setPreparing(true);
      setPlaying(false);
      try {
        const r = await fetch(`/api/timetravel/load?start=${Math.round(start)}&end=${Math.round(end)}${feedsQuery()}`, { cache: 'no-store' });
        const data = await r.json().catch(() => ({}));
        if (r.ok) {
          loadedRef.current = { start: Math.round(start), end: Math.round(end) };
          setLoaded({ start: Math.round(start), end: Math.round(end) });
          chunksRef.current.clear();
          inFlightRef.current.clear();
          ensureChunks(playheadRef.current, false);
          emitFrame(playheadRef.current);
          return true;
        }
        setErrorMsg(data.error || 'failed to load window');
        return false;
      } catch {
        setErrorMsg('failed to load window');
        return false;
      } finally {
        setPreparing(false);
      }
    },
    [ensureChunks, emitFrame],
  );

  const loadAround = useCallback(
    (t: number) => {
      if (!bounds) return;
      const half = DEFAULT_WINDOW_MS / 2;
      loadWindow(Math.max(bounds.min, t - half), Math.min(bounds.max, t + half));
    },
    [bounds, loadWindow],
  );

  // Kicked off by the operator after picking feeds — nothing queries the lake before this.
  const beginLoad = useCallback(async () => {
    const feeds = FEED_OPTIONS.map((f) => f.key).filter((k) => feedSel[k]);
    if (feeds.length === 0) return;
    feedsRef.current = feeds;
    setStatus('loading');
    setErrorMsg('');
    try {
      const br = await fetch('/api/timetravel/bounds', { cache: 'no-store' });
      const b = await br.json();
      if (!br.ok || b.min_time == null || b.max_time == null) throw new Error(b.error || 'no data in the lake yet');
      setBounds({ min: b.min_time, max: b.max_time, count: b.count });
      const winStart = Math.max(b.min_time, b.max_time - DEFAULT_WINDOW_MS);
      setStatus('ready');
      playheadRef.current = winStart;
      setPlayhead(winStart);
      await loadWindow(winStart, b.max_time);
      setHead(winStart);
    } catch (e) {
      setErrorMsg(e instanceof Error ? e.message : 'failed to load history');
      setStatus('error');
    }
  }, [feedSel, loadWindow, setHead]);

  // Clear the replay layer on unmount.
  useEffect(() => () => onFrame([]), [onFrame]);

  // Playback loop.
  useEffect(() => {
    if (status !== 'ready' || !bounds) return;
    const id = setInterval(() => {
      if (!playingRef.current) return;
      let next = playheadRef.current + TICK_MS * speedRef.current;
      if (next >= bounds.max) {
        next = bounds.max;
        setPlaying(false);
      }
      setHead(next);
    }, TICK_MS);
    return () => clearInterval(id);
  }, [status, bounds, setHead]);

  const pct = bounds && bounds.max > bounds.min ? ((playhead - bounds.min) / (bounds.max - bounds.min)) * 100 : 0;
  const outsideWindow = status === 'ready' && !!bounds && !inLoaded(playhead);
  const anyFeed = FEED_OPTIONS.some((f) => feedSel[f.key]);

  return (
    <div className="desktop-only absolute bottom-0 left-0 right-0 z-[300] pointer-events-auto">
      <div
        className="mx-auto max-w-[980px] mb-3 rounded-lg border px-4 py-2.5 backdrop-blur-md"
        style={{ background: 'rgba(8,10,14,0.88)', borderColor: 'rgba(0,229,255,0.35)', boxShadow: '0 0 30px rgba(0,229,255,0.12)' }}
      >
        <div className="flex items-center gap-3 flex-wrap">
          <span className="text-[10px] font-mono font-bold tracking-widest text-[#00E5FF] whitespace-nowrap">⏱ TIME TRAVEL</span>

          {status === 'select' && (
            <>
              <span className="text-[10px] font-mono text-white/50">pick feeds to replay:</span>
              {FEED_OPTIONS.map((f) => (
                <label key={f.key} className="flex items-center gap-1.5 text-[10px] font-mono text-white/75 cursor-pointer select-none">
                  <input
                    type="checkbox"
                    checked={!!feedSel[f.key]}
                    onChange={(e) => setFeedSel((s) => ({ ...s, [f.key]: e.target.checked }))}
                    className="accent-[#00E5FF] w-3 h-3"
                  />
                  {f.label}
                </label>
              ))}
              <button
                onClick={beginLoad}
                disabled={!anyFeed}
                className="text-[10px] font-mono font-bold px-3 py-1 rounded border border-[#00E5FF]/50 bg-[#00E5FF]/10 text-[#00E5FF] hover:bg-[#00E5FF]/20 disabled:opacity-30 disabled:cursor-not-allowed"
                title="Query only the selected feeds"
              >
                LOAD TIME TRAVEL ▸
              </button>
              <span className="text-[9px] font-mono text-white/30 whitespace-nowrap">Maritime is heavy — leave it off unless you need it</span>
            </>
          )}

          {(status === 'loading' || preparing) && (
            <span className="flex items-center gap-2 text-[10px] font-mono text-white/60">
              <span className="inline-block w-3 h-3 rounded-full border-2 border-[#00E5FF]/40 border-t-[#00E5FF] animate-spin" />
              {status === 'loading' && !preparing ? 'reading lake extent…' : 'loading window (one-time)…'}
            </span>
          )}

          {status === 'error' && (
            <span className="text-[10px] font-mono text-[#FF6B6B] flex-1">
              {errorMsg} — check the gateway HIVE_* config and that rows exist for the chosen feeds.
            </span>
          )}

          {status === 'ready' && bounds && (
            <>
              <button
                onClick={() => (playhead >= bounds.max ? setHead(bounds.min) : setPlaying((p) => !p))}
                disabled={preparing}
                className="flex items-center justify-center w-8 h-8 rounded border border-[#00E5FF]/40 bg-[#00E5FF]/10 hover:bg-[#00E5FF]/20 transition-colors disabled:opacity-40"
                title={playing ? 'Pause' : 'Play'}
              >
                {playing ? <Pause className="w-4 h-4 text-[#00E5FF]" /> : <Play className="w-4 h-4 text-[#00E5FF]" />}
              </button>

              <input
                type="range"
                min={bounds.min}
                max={bounds.max}
                value={playhead}
                step={Math.max(1000, Math.round((bounds.max - bounds.min) / 1000))}
                onChange={(e) => { setPlaying(false); setHead(Number(e.target.value)); }}
                className="flex-1 accent-[#00E5FF] h-1 cursor-pointer"
                style={{ background: `linear-gradient(to right, #00E5FF ${pct}%, rgba(255,255,255,0.15) ${pct}%)` }}
              />

              {buffering && <span className="inline-block w-2.5 h-2.5 rounded-full border-2 border-[#00E5FF]/40 border-t-[#00E5FF] animate-spin" title="loading chunk…" />}
              <span className="text-[10px] font-mono text-white/80 tabular-nums whitespace-nowrap min-w-[190px] text-right">{fmt(playhead)}</span>

              <select
                value={speed}
                onChange={(e) => setSpeed(Number(e.target.value))}
                className="text-[10px] font-mono bg-black/50 border border-white/15 rounded px-1.5 py-1 text-white/80"
                title="Playback speed"
              >
                {SPEEDS.map((s) => (<option key={s} value={s}>{s}×</option>))}
              </select>
            </>
          )}

          <button
            onClick={onExit}
            className="flex items-center justify-center w-8 h-8 rounded border border-white/15 bg-white/5 hover:bg-white/10 transition-colors ml-auto"
            title="Exit TimeTravel"
          >
            <X className="w-4 h-4 text-white/70" />
          </button>
        </div>

        {status === 'ready' && bounds && (
          <div className="mt-2 flex items-center gap-2">
            <span className="text-[9px] font-mono text-white/40 tracking-wider whitespace-nowrap">JUMP TO (UTC):</span>
            <input
              type="datetime-local"
              step={1}
              min={toInput(bounds.min)}
              max={toInput(bounds.max)}
              value={toInput(playhead)}
              onChange={(e) => {
                const ms = fromInput(e.target.value);
                if (ms == null) return;
                const clamped = Math.min(bounds.max, Math.max(bounds.min, ms));
                setPlaying(false);
                setHead(clamped);
                if (!inLoaded(clamped)) loadAround(clamped);
              }}
              className="text-[10px] font-mono bg-black/50 border border-white/15 rounded px-2 py-1 text-white/85 [color-scheme:dark]"
            />
            {outsideWindow && !preparing && (
              <button
                onClick={() => loadAround(playhead)}
                className="text-[9px] font-mono px-2 py-1 rounded border border-[#FFB300]/50 bg-[#FFB300]/10 text-[#FFCA28] hover:bg-[#FFB300]/20"
                title="This time is outside the loaded window — load it"
              >
                ⟳ LOAD THIS WINDOW
              </button>
            )}
            <span className="text-[9px] font-mono text-white/30 whitespace-nowrap">feeds: {feedsRef.current.join(', ') || '—'}</span>
          </div>
        )}

        {status === 'ready' && bounds && (
          <div className="mt-1 flex justify-between text-[8px] font-mono text-white/35">
            <span>{fmt(bounds.min)}</span>
            <span>loaded window {loaded ? `${fmt(loaded.start)} → ${fmt(loaded.end)}` : '—'}</span>
            <span>{fmt(bounds.max)}</span>
          </div>
        )}
      </div>
    </div>
  );
}
