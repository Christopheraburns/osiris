'use client';

import { useCallback, useEffect, useState } from 'react';
import { Circle, Loader2 } from 'lucide-react';

type RecorderStatus = {
  recording: boolean;
  available: boolean;
  run_id?: string | null;
};

export default function RecorderControl() {
  const [status, setStatus] = useState<RecorderStatus>({ recording: false, available: false });
  const [loading, setLoading] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const res = await fetch('/api/recorder', { cache: 'no-store' });
      const data = await res.json();
      setStatus({
        recording: Boolean(data.recording),
        available: res.ok && data.available !== false,
        run_id: data.run_id ?? null,
      });
    } catch {
      setStatus({ recording: false, available: false });
    }
  }, []);

  useEffect(() => {
    refresh();
    const iv = setInterval(refresh, 5000);
    return () => clearInterval(iv);
  }, [refresh]);

  const toggle = async () => {
    if (loading || !status.available) return;
    setLoading(true);
    try {
      const res = await fetch('/api/recorder', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: status.recording ? 'stop' : 'start' }),
      });
      const data = await res.json();
      if (res.ok) {
        setStatus((s) => ({
          ...s,
          recording: Boolean(data.recording),
          run_id: data.run_id ?? s.run_id,
        }));
      }
      await refresh();
    } finally {
      setLoading(false);
    }
  };

  const recording = status.recording;
  const dotColor = !status.available
    ? 'bg-[var(--text-muted)]'
    : recording
      ? 'bg-[var(--alert-green)] animate-osiris-pulse'
      : 'bg-[var(--alert-red)]';
  const labelColor = !status.available
    ? 'text-[var(--text-muted)]'
    : recording
      ? 'text-[var(--alert-green)]'
      : 'text-[var(--alert-red)]';

  return (
    <button
      type="button"
      onClick={toggle}
      disabled={loading || !status.available}
      title={
        !status.available
          ? 'Lakehouse recorder unavailable'
          : recording
            ? 'Stop recording to Iceberg + Neo4j'
            : 'Start recording to Iceberg + Neo4j'
      }
      className="pointer-events-auto glass-panel px-3 py-1.5 flex items-center gap-1.5 text-[8px] font-mono tracking-widest hover:opacity-80 transition-opacity border-white/10 bg-black/20 disabled:opacity-50 disabled:cursor-not-allowed"
    >
      {loading ? (
        <Loader2 className="w-3 h-3 animate-spin text-[var(--text-muted)]" />
      ) : (
        <Circle className={`w-2 h-2 fill-current ${dotColor}`} strokeWidth={0} />
      )}
      <span className={`font-bold ${labelColor}`}>
        REC:{recording ? 'ON' : 'OFF'}
      </span>
    </button>
  );
}
