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
}

interface Props {
  /** Called with the entity positions visible at the current playhead. */
  onFrame: (events: HistEvent[]) => void;
  /** Exit TimeTravel mode. */
  onExit: () => void;
}

const SPEEDS = [1, 10, 60, 300, 1200];
const TRAIL_MS = 120_000; // show each asset's last known position within a 2-min trail
const TICK_MS = 100; // playback resolution (10 fps)

const fmt = (ms: number) =>
  new Date(ms).toISOString().replace('T', '  ').replace('.000Z', 'Z').slice(0, 21) + ' UTC';

export default function TimeTravelBar({ onFrame, onExit }: Props) {
  const [status, setStatus] = useState<'loading' | 'ready' | 'error'>('loading');
  const [errorMsg, setErrorMsg] = useState<string>('');
  const [bounds, setBounds] = useState<{ min: number; max: number; count: number } | null>(null);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(60);
  const [playhead, setPlayhead] = useState(0);

  const eventsRef = useRef<HistEvent[]>([]);
  const playheadRef = useRef(0);
  const playingRef = useRef(false);
  const speedRef = useRef(60);

  playingRef.current = playing;
  speedRef.current = speed;

  // Compute the frame (latest position per asset within the trailing window) and emit it.
  const emitFrame = useCallback(
    (head: number) => {
      const from = head - TRAIL_MS;
      const latest = new Map<string, HistEvent>();
      const evs = eventsRef.current;
      for (let i = 0; i < evs.length; i++) {
        const e = evs[i];
        if (e.t > head) break; // sorted ascending
        if (e.t >= from) latest.set(e.asset_id, e);
      }
      onFrame(Array.from(latest.values()));
    },
    [onFrame],
  );

  const setHead = useCallback(
    (head: number) => {
      playheadRef.current = head;
      setPlayhead(head);
      emitFrame(head);
    },
    [emitFrame],
  );

  // Load bounds, then the whole window's events once.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const br = await fetch('/api/timetravel/bounds', { cache: 'no-store' });
        const b = await br.json();
        if (!br.ok || b.min_time == null || b.max_time == null) {
          throw new Error(b.error || 'no data in the lake yet');
        }
        if (cancelled) return;
        setBounds({ min: b.min_time, max: b.max_time, count: b.count });

        const params = new URLSearchParams({ start: String(b.min_time), end: String(b.max_time), limit: '50000' });
        const er = await fetch(`/api/timetravel?${params}`, { cache: 'no-store' });
        const data = await er.json();
        if (!er.ok) throw new Error(data.error || 'history fetch failed');
        if (cancelled) return;
        const evs: HistEvent[] = (data.events || []).filter((e: HistEvent) => e.t != null).sort((a: HistEvent, b: HistEvent) => a.t - b.t);
        eventsRef.current = evs;
        setStatus('ready');
        setHead(b.min_time); // start at the beginning
      } catch (e) {
        if (cancelled) return;
        setErrorMsg(e instanceof Error ? e.message : 'failed to load history');
        setStatus('error');
      }
    })();
    return () => {
      cancelled = true;
      onFrame([]); // clear the layer on unmount
    };
  }, [onFrame, setHead]);

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

  return (
    <div className="desktop-only absolute bottom-0 left-0 right-0 z-[300] pointer-events-auto">
      <div
        className="mx-auto max-w-[980px] mb-3 rounded-lg border px-4 py-2.5 backdrop-blur-md"
        style={{ background: 'rgba(8,10,14,0.88)', borderColor: 'rgba(0,229,255,0.35)', boxShadow: '0 0 30px rgba(0,229,255,0.12)' }}
      >
        <div className="flex items-center gap-3">
          <span className="text-[10px] font-mono font-bold tracking-widest text-[#00E5FF] whitespace-nowrap">⏱ TIME TRAVEL</span>

          {status === 'loading' && (
            <span className="flex items-center gap-2 text-[10px] font-mono text-white/60">
              <span className="inline-block w-3 h-3 rounded-full border-2 border-[#00E5FF]/40 border-t-[#00E5FF] animate-spin" /> loading lake history…
            </span>
          )}

          {status === 'error' && (
            <span className="text-[10px] font-mono text-[#FF6B6B] flex-1">
              {errorMsg} — check the gateway HIVE_* config and that rows exist in events_iceberg.
            </span>
          )}

          {status === 'ready' && bounds && (
            <>
              <button
                onClick={() => (playhead >= bounds.max ? setHead(bounds.min) : setPlaying((p) => !p))}
                className="flex items-center justify-center w-8 h-8 rounded border border-[#00E5FF]/40 bg-[#00E5FF]/10 hover:bg-[#00E5FF]/20 transition-colors"
                title={playing ? 'Pause' : 'Play'}
              >
                {playing ? <Pause className="w-4 h-4 text-[#00E5FF]" /> : <Play className="w-4 h-4 text-[#00E5FF]" />}
              </button>
              <button
                onClick={() => { setPlaying(false); setHead(bounds.min); }}
                className="flex items-center justify-center w-8 h-8 rounded border border-white/15 bg-white/5 hover:bg-white/10 transition-colors text-white/70 text-xs font-mono"
                title="Rewind to start"
              >
                ⏮
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

              <span className="text-[10px] font-mono text-white/80 tabular-nums whitespace-nowrap min-w-[190px] text-right">
                {fmt(playhead)}
              </span>

              <select
                value={speed}
                onChange={(e) => setSpeed(Number(e.target.value))}
                className="text-[10px] font-mono bg-black/50 border border-white/15 rounded px-1.5 py-1 text-white/80"
                title="Playback speed"
              >
                {SPEEDS.map((s) => (
                  <option key={s} value={s}>{s}×</option>
                ))}
              </select>
            </>
          )}

          <button
            onClick={onExit}
            className="flex items-center justify-center w-8 h-8 rounded border border-white/15 bg-white/5 hover:bg-white/10 transition-colors ml-1"
            title="Exit TimeTravel"
          >
            <X className="w-4 h-4 text-white/70" />
          </button>
        </div>
        {status === 'ready' && bounds && (
          <div className="mt-1 flex justify-between text-[8px] font-mono text-white/35">
            <span>{fmt(bounds.min)}</span>
            <span>{bounds.count.toLocaleString()} events · replaying from lakehouse</span>
            <span>{fmt(bounds.max)}</span>
          </div>
        )}
      </div>
    </div>
  );
}
