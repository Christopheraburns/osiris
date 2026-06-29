'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { ArrowLeft, Loader2, Send, Sparkles, Square } from 'lucide-react';

interface LlmHealth {
  status?: string;
  ollama?: boolean;
  model?: string;
  ollama_url?: string;
}

interface LlmMetrics {
  ttft_ms: number;
  token_count: number;
  tokens_per_sec: number;
  total_ms: number;
}

export default function LlmPage() {
  const [health, setHealth] = useState<LlmHealth | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);
  const [prompt, setPrompt] = useState('');
  const [response, setResponse] = useState('');
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [metrics, setMetrics] = useState<LlmMetrics | null>(null);
  const [liveTtft, setLiveTtft] = useState<number | null>(null);
  const [liveTokens, setLiveTokens] = useState(0);
  const abortRef = useRef<AbortController | null>(null);
  const startRef = useRef<number>(0);
  const firstTokenRef = useRef<number | null>(null);

  const fetchHealth = useCallback(async () => {
    try {
      const res = await fetch('/api/llm', { cache: 'no-store' });
      const data = await res.json();
      if (res.ok) {
        setHealth(data);
        setHealthError(null);
      } else {
        setHealthError(data.error || `HTTP ${res.status}`);
      }
    } catch (e) {
      setHealthError(e instanceof Error ? e.message : 'health check failed');
    }
  }, []);

  // Fresh state on every mount (handles Next.js router cache / browser back).
  useEffect(() => {
    setRunning(false);
    setError(null);
    fetchHealth();
  }, [fetchHealth]);

  // Abort in-flight generation when navigating away without a full page unload.
  useEffect(() => {
    return () => {
      abortRef.current?.abort();
      abortRef.current = null;
    };
  }, []);

  // Refetch when returning via browser back or bfcache restore.
  useEffect(() => {
    const refresh = () => {
      setRunning(false);
      fetchHealth();
    };
    const onVisible = () => {
      if (document.visibilityState === 'visible') refresh();
    };
    const onPageShow = (e: PageTransitionEvent) => {
      if (e.persisted) refresh();
    };
    document.addEventListener('visibilitychange', onVisible);
    window.addEventListener('pageshow', onPageShow);
    return () => {
      document.removeEventListener('visibilitychange', onVisible);
      window.removeEventListener('pageshow', onPageShow);
    };
  }, [fetchHealth]);

  const stop = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setRunning(false);
  }, []);

  const runPrompt = useCallback(async () => {
    const text = prompt.trim();
    if (!text || running) return;

    stop();
    const ac = new AbortController();
    abortRef.current = ac;

    setRunning(true);
    setError(null);
    setResponse('');
    setMetrics(null);
    setLiveTtft(null);
    setLiveTokens(0);
    startRef.current = performance.now();
    firstTokenRef.current = null;

    try {
      const res = await fetch('/api/llm', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt: text }),
        signal: ac.signal,
        cache: 'no-store',
      });

      if (!res.ok || !res.body) {
        const b = await res.json().catch(() => ({}));
        throw new Error(b.error || `HTTP ${res.status}`);
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = '';

      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        let idx;
        while ((idx = buf.indexOf('\n')) >= 0) {
          const line = buf.slice(0, idx).trim();
          buf = buf.slice(idx + 1);
          if (!line) continue;
          try {
            const evt = JSON.parse(line);
            if (evt.type === 'token' && evt.text) {
              if (firstTokenRef.current === null) {
                firstTokenRef.current = performance.now();
                setLiveTtft(Math.round(firstTokenRef.current - startRef.current));
              }
              setLiveTokens((n) => n + 1);
              setResponse((prev) => prev + evt.text);
            } else if (evt.type === 'done') {
              setMetrics({
                ttft_ms: evt.ttft_ms ?? 0,
                token_count: evt.token_count ?? 0,
                tokens_per_sec: evt.tokens_per_sec ?? 0,
                total_ms: evt.total_ms ?? Math.round(performance.now() - startRef.current),
              });
            }
          } catch {
            /* ignore partial JSON */
          }
        }
      }
    } catch (e) {
      if (e instanceof Error && e.name === 'AbortError') return;
      setError(e instanceof Error ? e.message : 'prompt failed');
    } finally {
      setRunning(false);
      abortRef.current = null;
    }
  }, [prompt, running, stop]);

  const ollamaUp = health?.ollama === true;

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
          <h1 className="text-sm font-bold tracking-[0.3em] text-[#D4AF37] flex items-center gap-2">
            <Sparkles className="w-4 h-4 text-[var(--cyan-primary)]" />
            LLM
          </h1>
        </div>
        <button
          onClick={fetchHealth}
          className="glass-panel px-3 py-1.5 text-[10px] tracking-widest border-white/10 hover:opacity-80"
        >
          REFRESH STATUS
        </button>
      </div>

      {/* Model status */}
      <div className="glass-panel border border-white/10 rounded p-4 mb-4 text-[11px]">
        <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
          <span>
            OLLAMA:{' '}
            <span className={ollamaUp ? 'text-[var(--alert-green)] font-bold' : 'text-[var(--alert-red)] font-bold'}>
              {health ? (ollamaUp ? 'ONLINE' : 'OFFLINE') : '…'}
            </span>
          </span>
          <span>
            MODEL:{' '}
            <span className="text-[var(--cyan-primary)] font-bold">{health?.model || '—'}</span>
          </span>
          {health?.ollama_url && (
            <span className="text-[var(--text-muted)] truncate max-w-full">{health.ollama_url}</span>
          )}
        </div>
        {healthError && <p className="mt-2 text-[var(--alert-red)]">Status error: {healthError}</p>}
      </div>

      {/* Metrics bar */}
      {(metrics || liveTtft !== null || running) && (
        <div className="flex flex-wrap gap-4 mb-4 text-[10px] tracking-widest">
          <span className="glass-panel px-3 py-1.5 border-white/10">
            TTFT:{' '}
            <span className="text-[var(--cyan-primary)] font-bold">
              {metrics ? `${metrics.ttft_ms} ms` : liveTtft !== null ? `${liveTtft} ms` : '…'}
            </span>
          </span>
          <span className="glass-panel px-3 py-1.5 border-white/10">
            TOKENS:{' '}
            <span className="text-[var(--cyan-primary)] font-bold">
              {metrics ? metrics.token_count : running ? liveTokens : '—'}
            </span>
          </span>
          <span className="glass-panel px-3 py-1.5 border-white/10">
            TOK/S:{' '}
            <span className="text-[var(--cyan-primary)] font-bold">
              {metrics ? metrics.tokens_per_sec : running ? '…' : '—'}
            </span>
          </span>
          <span className="glass-panel px-3 py-1.5 border-white/10">
            TOTAL:{' '}
            <span className="text-[var(--cyan-primary)] font-bold">
              {metrics ? `${metrics.total_ms} ms` : running ? '…' : '—'}
            </span>
          </span>
        </div>
      )}

      {/* Prompt */}
      <label className="block text-[10px] tracking-widest text-[var(--text-muted)] mb-1">PROMPT</label>
      <textarea
        value={prompt}
        onChange={(e) => setPrompt(e.target.value)}
        disabled={running}
        rows={5}
        placeholder="Enter a test prompt for the local LLM…"
        className="w-full glass-panel border border-white/10 rounded p-3 text-[12px] bg-black/40 outline-none resize-y min-h-[120px] placeholder:text-[var(--text-muted)] disabled:opacity-60"
        onKeyDown={(e) => {
          if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
            e.preventDefault();
            runPrompt();
          }
        }}
      />
      <div className="flex items-center gap-2 mt-2 mb-4">
        <button
          onClick={runPrompt}
          disabled={running || !prompt.trim() || !ollamaUp}
          className="glass-panel px-4 py-2 flex items-center gap-2 text-[10px] tracking-widest border-[var(--cyan-primary)]/40 text-[var(--cyan-primary)] hover:bg-[var(--cyan-primary)]/10 disabled:opacity-40 disabled:cursor-not-allowed"
        >
          {running ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Send className="w-3.5 h-3.5" />}
          {running ? 'GENERATING…' : 'SEND'}
        </button>
        {running && (
          <button
            onClick={stop}
            className="glass-panel px-3 py-2 flex items-center gap-2 text-[10px] tracking-widest border-white/10 text-[var(--text-muted)] hover:opacity-80"
          >
            <Square className="w-3 h-3" />
            STOP
          </button>
        )}
        <span className="text-[9px] text-[var(--text-muted)]">Ctrl+Enter to send</span>
      </div>

      {error && <div className="mb-3 text-[11px] text-[var(--alert-red)]">Error: {error}</div>}

      {/* Response */}
      <label className="block text-[10px] tracking-widest text-[var(--text-muted)] mb-1">RESPONSE</label>
      <div className="min-h-[200px] glass-panel border border-white/10 rounded p-4 text-[12px] leading-relaxed whitespace-pre-wrap text-white/90">
        {response || (running ? '…' : 'Response will appear here.')}
      </div>
    </div>
  );
}
