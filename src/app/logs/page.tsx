'use client';

import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { ArrowLeft, RefreshCw, Search } from 'lucide-react';

interface LogRecord {
  ts: string;
  service: string;
  level: 'debug' | 'info' | 'warn' | 'error';
  scope: string | null;
  msg: string;
  ingest_run_id: string | null;
  data: Record<string, unknown> | null;
}

const LEVEL_COLOR: Record<string, string> = {
  debug: 'var(--text-muted)',
  info: 'var(--cyan-primary)',
  warn: '#FF9500',
  error: 'var(--alert-red)',
};

export default function LogsPage() {
  const [logs, setLogs] = useState<LogRecord[]>([]);
  const [q, setQ] = useState('');
  const [level, setLevel] = useState('');
  const [service, setService] = useState('');
  const [auto, setAuto] = useState(true);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchLogs = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (q) params.set('q', q);
      if (level) params.set('level', level);
      if (service) params.set('service', service);
      params.set('limit', '300');
      const res = await fetch(`/api/logs?${params.toString()}`, { cache: 'no-store' });
      const data = await res.json();
      if (res.ok) {
        setLogs(Array.isArray(data.logs) ? data.logs : []);
        setError(null);
      } else {
        setError(data.error || `HTTP ${res.status}`);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'fetch failed');
    } finally {
      setLoading(false);
    }
  }, [q, level, service]);

  useEffect(() => {
    fetchLogs();
  }, [fetchLogs]);

  useEffect(() => {
    if (!auto) return;
    const iv = setInterval(fetchLogs, 5000);
    return () => clearInterval(iv);
  }, [auto, fetchLogs]);

  return (
    <div className="min-h-screen bg-[#0A0A0A] text-[var(--text-primary)] font-mono p-4 md:p-6">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-3 mb-4 border-b border-[var(--border-primary)] pb-3">
        <div className="flex items-center gap-3">
          <Link
            href="/"
            className="glass-panel px-3 py-1.5 flex items-center gap-1.5 text-[10px] tracking-widest hover:opacity-80 transition-opacity border-white/10"
          >
            <ArrowLeft className="w-3.5 h-3.5" />
            BACK TO MAP
          </Link>
          <h1 className="text-sm font-bold tracking-[0.3em] text-[#D4AF37]">OSIRIS LOGS</h1>
        </div>
        <div className="flex items-center gap-2 text-[10px]">
          <button
            onClick={() => setAuto((a) => !a)}
            className={`glass-panel px-3 py-1.5 flex items-center gap-1.5 tracking-widest border-white/10 ${auto ? 'text-[var(--alert-green)]' : 'text-[var(--text-muted)]'}`}
          >
            <RefreshCw className={`w-3 h-3 ${loading ? 'animate-spin' : ''}`} />
            AUTO {auto ? 'ON' : 'OFF'}
          </button>
          <button onClick={fetchLogs} className="glass-panel px-3 py-1.5 tracking-widest border-white/10 hover:opacity-80">
            REFRESH
          </button>
        </div>
      </div>

      {/* Filters */}
      <div className="flex items-center flex-wrap gap-2 mb-4 text-[11px]">
        <div className="flex items-center gap-1.5 glass-panel px-2 py-1 border-white/10 flex-1 min-w-[220px]">
          <Search className="w-3.5 h-3.5 text-[var(--text-muted)]" />
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="search msg / scope / ingest_run_id / data…"
            className="bg-transparent outline-none w-full text-[var(--text-primary)] placeholder:text-[var(--text-muted)]"
          />
        </div>
        <select value={level} onChange={(e) => setLevel(e.target.value)} className="glass-panel px-2 py-1.5 bg-black/40 border-white/10 outline-none">
          <option value="">all levels</option>
          <option value="debug">debug</option>
          <option value="info">info</option>
          <option value="warn">warn</option>
          <option value="error">error</option>
        </select>
        <select value={service} onChange={(e) => setService(e.target.value)} className="glass-panel px-2 py-1.5 bg-black/40 border-white/10 outline-none">
          <option value="">all services</option>
          <option value="osiris">osiris</option>
          <option value="gateway">gateway</option>
        </select>
        <span className="text-[10px] text-[var(--text-muted)] ml-1">{logs.length} rows</span>
      </div>

      {error && (
        <div className="mb-3 text-[11px] text-[var(--alert-red)]">Error: {error}</div>
      )}

      {/* Table */}
      <div className="overflow-auto border border-[var(--border-primary)] rounded">
        <table className="w-full text-[11px] border-collapse">
          <thead className="sticky top-0 bg-black/80 backdrop-blur">
            <tr className="text-left text-[var(--text-muted)] tracking-widest">
              <th className="px-2 py-2 font-normal whitespace-nowrap">TIME (UTC)</th>
              <th className="px-2 py-2 font-normal">LVL</th>
              <th className="px-2 py-2 font-normal">SVC</th>
              <th className="px-2 py-2 font-normal">SCOPE</th>
              <th className="px-2 py-2 font-normal">MESSAGE</th>
              <th className="px-2 py-2 font-normal">INGEST_RUN_ID</th>
              <th className="px-2 py-2 font-normal">DATA</th>
            </tr>
          </thead>
          <tbody>
            {logs.map((r, i) => (
              <tr key={i} className="border-t border-white/5 hover:bg-white/5 align-top">
                <td className="px-2 py-1.5 whitespace-nowrap text-[var(--text-muted)]">{r.ts.replace('T', ' ').replace('Z', '').slice(0, 19)}</td>
                <td className="px-2 py-1.5 font-bold" style={{ color: LEVEL_COLOR[r.level] }}>{r.level.toUpperCase()}</td>
                <td className="px-2 py-1.5">{r.service}</td>
                <td className="px-2 py-1.5 text-[var(--cyan-primary)]">{r.scope}</td>
                <td className="px-2 py-1.5 text-[var(--text-primary)]">{r.msg}</td>
                <td className="px-2 py-1.5 whitespace-nowrap" style={{ color: r.ingest_run_id?.startsWith('nifi-') ? 'var(--alert-green)' : 'var(--text-muted)' }}>
                  {r.ingest_run_id || '—'}
                </td>
                <td className="px-2 py-1.5 text-[var(--text-muted)] max-w-[360px] truncate" title={r.data ? JSON.stringify(r.data) : ''}>
                  {r.data ? JSON.stringify(r.data) : ''}
                </td>
              </tr>
            ))}
            {logs.length === 0 && !loading && (
              <tr><td colSpan={7} className="px-2 py-6 text-center text-[var(--text-muted)]">No logs match the current filters.</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
