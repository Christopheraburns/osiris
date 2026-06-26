'use client';

import { useCallback, useEffect, useState } from 'react';
import { Loader2, ShieldCheck, ShieldOff } from 'lucide-react';

type ModeStatus = {
  secured: boolean;
  available: boolean;
  migratedFeeds: string[];
};

/** Broadcast so page.tsx can clear caches + refetch when the mode flips. */
function broadcast(secured: boolean) {
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new CustomEvent('osiris-connection-mode', { detail: { secured } }));
  }
}

export default function SecuredConnection() {
  const [status, setStatus] = useState<ModeStatus>({ secured: false, available: false, migratedFeeds: [] });
  const [loading, setLoading] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const res = await fetch('/api/connection-mode', { cache: 'no-store' });
      const data = await res.json();
      setStatus({
        secured: Boolean(data.secured),
        available: res.ok && data.available !== false,
        migratedFeeds: Array.isArray(data.migratedFeeds) ? data.migratedFeeds : [],
      });
    } catch {
      setStatus((s) => ({ ...s, available: false }));
    }
  }, []);

  useEffect(() => {
    refresh();
    const iv = setInterval(refresh, 5000);
    return () => clearInterval(iv);
  }, [refresh]);

  const toggle = async () => {
    if (loading) return;
    setLoading(true);
    try {
      const res = await fetch('/api/connection-mode', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: status.secured ? 'direct' : 'secure' }),
      });
      const data = await res.json();
      if (res.ok) {
        const secured = Boolean(data.secured);
        setStatus((s) => ({ ...s, secured }));
        broadcast(secured);
      }
      await refresh();
    } finally {
      setLoading(false);
    }
  };

  const secured = status.secured;
  const count = status.migratedFeeds.length;
  const labelColor = secured ? 'text-[var(--alert-green)]' : 'text-[var(--text-muted)]';

  const title = secured
    ? `Secured (streaming lakehouse) — ONLY ${count} NiFi feed${count === 1 ? '' : 's'} active; all other layers disabled`
    : 'Direct upstream mode — click to use only NiFi-backed feeds (secured)';

  return (
    <button
      type="button"
      onClick={toggle}
      disabled={loading}
      title={title}
      className="pointer-events-auto glass-panel px-3 py-1.5 flex items-center gap-1.5 text-[8px] font-mono tracking-widest hover:opacity-80 transition-opacity border-white/10 bg-black/20 disabled:opacity-50 disabled:cursor-not-allowed"
    >
      {loading ? (
        <Loader2 className="w-3 h-3 animate-spin text-[var(--text-muted)]" />
      ) : secured ? (
        <ShieldCheck className={`w-3 h-3 ${secured ? 'text-[var(--alert-green)] animate-osiris-pulse' : ''}`} />
      ) : (
        <ShieldOff className="w-3 h-3 text-[var(--text-muted)]" />
      )}
      <span className={`font-bold ${labelColor}`}>SEC:{secured ? 'ON' : 'OFF'}</span>
    </button>
  );
}
