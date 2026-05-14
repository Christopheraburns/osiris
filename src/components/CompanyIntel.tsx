'use client';

import { useState, useCallback } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
  Search, Building2, Users, Globe, Mail, Phone, MapPin,
  ChevronDown, ChevronUp, Loader2, ExternalLink, Shield,
  Briefcase, DollarSign, Calendar, Hash, Network, User,
  Copy, Check, AlertTriangle, LinkIcon, Building,
} from 'lucide-react';

interface Officer {
  name: string;
  role: string;
  appointed?: string;
  resigned?: string;
  nationality?: string;
}

interface Contact {
  name: string;
  title: string;
  email_guess?: string;
  linkedin_url?: string;
  confidence: number;
}

interface CompanyResult {
  name: string;
  jurisdiction?: string;
  company_number?: string;
  status?: string;
  type?: string;
  incorporation_date?: string;
  address?: string;
  officers?: Officer[];
  industry?: string;
  domain?: string;
  logo_url?: string;
  description?: string;
  employee_estimate?: string;
  revenue_estimate?: string;
  phone?: string;
  email_pattern?: string;
  contacts?: Contact[];
  related_companies?: { name: string; relationship: string }[];
  social?: Record<string, string>;
  source: string;
}

export default function CompanyIntel({ isMobile }: { isMobile?: boolean }) {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<CompanyResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [expanded, setExpanded] = useState(true);
  const [selectedCompany, setSelectedCompany] = useState<CompanyResult | null>(null);
  const [copiedField, setCopiedField] = useState('');
  const [totalResults, setTotalResults] = useState(0);
  const [sources, setSources] = useState<string[]>([]);

  const copyToClipboard = useCallback((text: string, field: string) => {
    navigator.clipboard.writeText(text);
    setCopiedField(field);
    setTimeout(() => setCopiedField(''), 2000);
  }, []);

  const searchCompanies = useCallback(async () => {
    if (!query.trim() || loading) return;
    setLoading(true); setError(''); setResults([]); setSelectedCompany(null);
    try {
      const res = await fetch(`/api/company-intel?q=${encodeURIComponent(query)}`);
      const data = await res.json();
      if (res.ok && data.companies) {
        setResults(data.companies);
        setTotalResults(data.total);
        setSources(data.sources || []);
        if (data.companies.length === 1) {
          setSelectedCompany(data.companies[0]);
        }
      } else {
        setError(data.error || 'Search failed');
      }
    } catch { setError('Network error — check connection'); }
    finally { setLoading(false); }
  }, [query, loading]);

  // ── Status color helper ──
  const statusColor = (status?: string) => {
    if (!status) return '#888';
    const s = status.toLowerCase();
    if (s.includes('active') || s.includes('open') || s.includes('live')) return '#00E676';
    if (s.includes('dissolv') || s.includes('closed') || s.includes('struck')) return '#FF3D3D';
    if (s.includes('dormant') || s.includes('inactive')) return '#FF9500';
    return '#FFD700';
  };

  // ── Confidence color helper ──
  const confidenceColor = (c: number) => {
    if (c >= 80) return '#00E676';
    if (c >= 60) return '#FFD700';
    if (c >= 40) return '#FF9500';
    return '#FF3D3D';
  };

  // ── Company Card (search results list) ──
  const CompanyCard = ({ company, index }: { company: CompanyResult; index: number }) => (
    <motion.button
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: index * 0.05 }}
      onClick={() => setSelectedCompany(company)}
      className={`w-full text-left p-3 rounded-lg border transition-all hover:bg-[var(--hover-accent)] ${
        selectedCompany?.company_number === company.company_number
          ? 'border-[#D4AF37]/50 bg-[#D4AF37]/5'
          : 'border-[var(--border-secondary)]/30 bg-[var(--bg-primary)]/30'
      }`}
    >
      <div className="flex items-start gap-3">
        {/* Logo */}
        <div className="w-9 h-9 rounded-lg border border-[var(--border-secondary)]/30 bg-[var(--bg-tertiary)] flex items-center justify-center flex-shrink-0 overflow-hidden">
          {company.logo_url ? (
            <img
              src={company.logo_url}
              alt=""
              className="w-full h-full object-contain"
              onError={(e) => { (e.target as HTMLImageElement).style.display = 'none'; (e.target as HTMLImageElement).parentElement!.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#D4AF37" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 22V4a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v18Z"/><path d="M6 12H4a2 2 0 0 0-2 2v6a2 2 0 0 0 2 2h2"/><path d="M18 9h2a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2h-2"/><path d="M10 6h4"/><path d="M10 10h4"/><path d="M10 14h4"/><path d="M10 18h4"/></svg>'; }}
            />
          ) : (
            <Building2 className="w-4 h-4 text-[var(--gold-primary)]" />
          )}
        </div>

        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-[11px] font-mono font-bold text-[var(--text-primary)] truncate">{company.name}</span>
          </div>
          <div className="flex items-center gap-2 mt-0.5 flex-wrap">
            {company.jurisdiction && (
              <span className="text-[8px] font-mono px-1.5 py-0.5 rounded bg-[var(--cyan-primary)]/10 text-[var(--cyan-primary)] border border-[var(--cyan-primary)]/20">
                {company.jurisdiction}
              </span>
            )}
            {company.status && (
              <span className="text-[8px] font-mono px-1.5 py-0.5 rounded border" style={{ color: statusColor(company.status), borderColor: `${statusColor(company.status)}30`, backgroundColor: `${statusColor(company.status)}10` }}>
                {company.status.toUpperCase()}
              </span>
            )}
            {company.industry && (
              <span className="text-[8px] font-mono text-[var(--text-muted)]">{company.industry}</span>
            )}
          </div>
          {company.address && (
            <p className="text-[9px] font-mono text-[var(--text-muted)] mt-1 truncate flex items-center gap-1">
              <MapPin className="w-2.5 h-2.5 flex-shrink-0" />{company.address}
            </p>
          )}
        </div>

        <span className="text-[7px] font-mono text-[var(--text-muted)] flex-shrink-0">{company.source}</span>
      </div>
    </motion.button>
  );

  // ── Company Detail View ──
  const CompanyDetail = ({ company }: { company: CompanyResult }) => (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      className="space-y-3"
    >
      {/* Back button + Header */}
      <div className="flex items-center gap-2">
        <button onClick={() => setSelectedCompany(null)} className="text-[9px] font-mono text-[var(--gold-primary)] hover:text-white transition-colors px-2 py-1 rounded border border-[var(--gold-primary)]/30 hover:border-[var(--gold-primary)]/60">
          ← RESULTS
        </button>
        <span className="text-[8px] font-mono text-[var(--text-muted)]">{company.source}</span>
      </div>

      {/* Company header card */}
      <div className="p-3 rounded-lg border border-[var(--border-primary)] bg-[var(--bg-primary)]/40">
        <div className="flex items-start gap-3">
          <div className="w-12 h-12 rounded-lg border border-[var(--border-secondary)]/30 bg-[var(--bg-tertiary)] flex items-center justify-center flex-shrink-0 overflow-hidden">
            {company.logo_url ? (
              <img src={company.logo_url} alt="" className="w-full h-full object-contain" onError={(e) => { (e.target as HTMLImageElement).style.display = 'none'; }} />
            ) : (
              <Building2 className="w-6 h-6 text-[var(--gold-primary)]" />
            )}
          </div>
          <div className="flex-1">
            <h3 className="text-[13px] font-mono font-bold text-[var(--text-heading)]">{company.name}</h3>
            <div className="flex items-center gap-2 mt-1 flex-wrap">
              {company.status && (
                <span className="text-[8px] font-mono font-bold px-2 py-0.5 rounded border" style={{ color: statusColor(company.status), borderColor: `${statusColor(company.status)}40`, backgroundColor: `${statusColor(company.status)}10` }}>
                  {company.status.toUpperCase()}
                </span>
              )}
              {company.jurisdiction && (
                <span className="text-[8px] font-mono px-1.5 py-0.5 rounded bg-[var(--cyan-primary)]/10 text-[var(--cyan-primary)] border border-[var(--cyan-primary)]/20">
                  {company.jurisdiction}
                </span>
              )}
              {company.type && (
                <span className="text-[8px] font-mono text-[var(--text-muted)]">{company.type}</span>
              )}
            </div>
          </div>
        </div>
        {company.description && (
          <p className="text-[10px] font-mono text-[var(--text-secondary)] mt-2 leading-relaxed">{company.description}</p>
        )}
      </div>

      {/* Key Metrics Grid */}
      <div className="grid grid-cols-2 gap-2">
        {[
          { icon: Briefcase, label: 'INDUSTRY', value: company.industry, color: '#D4AF37' },
          { icon: Users, label: 'EMPLOYEES', value: company.employee_estimate, color: '#00E5FF' },
          { icon: DollarSign, label: 'EST. REVENUE', value: company.revenue_estimate, color: '#00E676' },
          { icon: Calendar, label: 'FOUNDED', value: company.incorporation_date, color: '#E040FB' },
          { icon: Hash, label: 'REG. NUMBER', value: company.company_number, color: '#FF9500' },
          { icon: Globe, label: 'DOMAIN', value: company.domain, color: '#448AFF' },
        ].filter(m => m.value).map((metric, i) => (
          <div key={i} className="p-2 rounded-lg border border-[var(--border-secondary)]/20 bg-[var(--bg-primary)]/30">
            <div className="flex items-center gap-1.5 mb-0.5">
              <metric.icon className="w-3 h-3" style={{ color: metric.color }} />
              <span className="text-[7px] font-mono tracking-wider text-[var(--text-muted)]">{metric.label}</span>
            </div>
            <div className="flex items-center gap-1">
              <span className="text-[10px] font-mono font-bold text-[var(--text-primary)] truncate">{metric.value}</span>
              {(metric.label === 'DOMAIN' || metric.label === 'REG. NUMBER') && (
                <button onClick={() => copyToClipboard(metric.value!, metric.label)} className="text-[var(--text-muted)] hover:text-white transition-colors flex-shrink-0">
                  {copiedField === metric.label ? <Check className="w-2.5 h-2.5 text-[var(--alert-green)]" /> : <Copy className="w-2.5 h-2.5" />}
                </button>
              )}
            </div>
          </div>
        ))}
      </div>

      {/* Address */}
      {company.address && (
        <div className="p-2.5 rounded-lg border border-[var(--border-secondary)]/20 bg-[var(--bg-primary)]/30">
          <div className="flex items-center gap-1.5 mb-1">
            <MapPin className="w-3 h-3 text-[#FFD700]" />
            <span className="text-[7px] font-mono tracking-wider text-[var(--text-muted)]">REGISTERED ADDRESS</span>
          </div>
          <p className="text-[10px] font-mono text-[var(--text-primary)]">{company.address}</p>
        </div>
      )}

      {/* Email Pattern */}
      {company.email_pattern && (
        <div className="p-2.5 rounded-lg border border-[var(--border-secondary)]/20 bg-[var(--bg-primary)]/30">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-1.5">
              <Mail className="w-3 h-3 text-[#E040FB]" />
              <span className="text-[7px] font-mono tracking-wider text-[var(--text-muted)]">EMAIL PATTERN</span>
            </div>
            <button onClick={() => copyToClipboard(company.email_pattern!, 'email')} className="text-[var(--text-muted)] hover:text-white transition-colors">
              {copiedField === 'email' ? <Check className="w-2.5 h-2.5 text-[var(--alert-green)]" /> : <Copy className="w-2.5 h-2.5" />}
            </button>
          </div>
          <span className="text-[10px] font-mono text-[#E040FB]">{company.email_pattern}</span>
        </div>
      )}

      {/* Officers / Contacts */}
      {company.contacts && company.contacts.length > 0 && (
        <div className="rounded-lg border border-[var(--border-primary)] overflow-hidden">
          <div className="flex items-center gap-2 px-3 py-2 bg-[var(--bg-primary)]/60 border-b border-[var(--border-secondary)]/30">
            <Users className="w-3.5 h-3.5 text-[var(--cyan-primary)]" />
            <span className="text-[9px] font-mono font-bold tracking-widest text-[var(--cyan-primary)]">KEY CONTACTS</span>
            <span className="text-[8px] font-mono text-[var(--text-muted)]">{company.contacts.length}</span>
          </div>
          <div className="divide-y divide-[var(--border-secondary)]/15">
            {company.contacts.map((contact, i) => (
              <div key={i} className="px-3 py-2 hover:bg-[var(--hover-accent)] transition-colors">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <div className="w-6 h-6 rounded-full bg-[var(--bg-tertiary)] border border-[var(--border-secondary)]/30 flex items-center justify-center">
                      <User className="w-3 h-3 text-[var(--text-muted)]" />
                    </div>
                    <div>
                      <span className="text-[10px] font-mono font-bold text-[var(--text-primary)]">{contact.name}</span>
                      <span className="text-[8px] font-mono text-[var(--text-muted)] ml-2">{contact.title}</span>
                    </div>
                  </div>
                  <span className="text-[7px] font-mono px-1.5 py-0.5 rounded border" style={{ color: confidenceColor(contact.confidence), borderColor: `${confidenceColor(contact.confidence)}30`, backgroundColor: `${confidenceColor(contact.confidence)}10` }}>
                    {contact.confidence}%
                  </span>
                </div>
                <div className="flex items-center gap-3 mt-1 ml-8">
                  {contact.email_guess && (
                    <button onClick={() => copyToClipboard(contact.email_guess!, `email-${i}`)} className="flex items-center gap-1 text-[9px] font-mono text-[#E040FB] hover:text-[#E040FB]/80 transition-colors">
                      <Mail className="w-2.5 h-2.5" />
                      {contact.email_guess}
                      {copiedField === `email-${i}` ? <Check className="w-2.5 h-2.5 text-[var(--alert-green)]" /> : <Copy className="w-2.5 h-2.5 ml-0.5 opacity-40" />}
                    </button>
                  )}
                  {contact.linkedin_url && (
                    <a href={contact.linkedin_url} target="_blank" rel="noopener noreferrer" className="flex items-center gap-1 text-[9px] font-mono text-[#448AFF] hover:text-[#448AFF]/80 transition-colors">
                      <LinkIcon className="w-2.5 h-2.5" />LinkedIn
                    </a>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Officers (when no contacts but officers exist) */}
      {(!company.contacts || company.contacts.length === 0) && company.officers && company.officers.length > 0 && (
        <div className="rounded-lg border border-[var(--border-primary)] overflow-hidden">
          <div className="flex items-center gap-2 px-3 py-2 bg-[var(--bg-primary)]/60 border-b border-[var(--border-secondary)]/30">
            <Users className="w-3.5 h-3.5 text-[var(--gold-primary)]" />
            <span className="text-[9px] font-mono font-bold tracking-widest text-[var(--gold-primary)]">OFFICERS</span>
            <span className="text-[8px] font-mono text-[var(--text-muted)]">{company.officers.length}</span>
          </div>
          <div className="divide-y divide-[var(--border-secondary)]/15">
            {company.officers.map((officer, i) => (
              <div key={i} className="px-3 py-2 flex items-center justify-between hover:bg-[var(--hover-accent)] transition-colors">
                <div>
                  <span className="text-[10px] font-mono font-bold text-[var(--text-primary)]">{officer.name}</span>
                  <span className="text-[8px] font-mono text-[var(--text-muted)] ml-2">{officer.role}</span>
                </div>
                <div className="flex items-center gap-2 text-[8px] font-mono text-[var(--text-muted)]">
                  {officer.nationality && <span>{officer.nationality}</span>}
                  {officer.appointed && <span>Since {officer.appointed}</span>}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Social / Links */}
      {company.social && Object.keys(company.social).length > 0 && (
        <div className="flex items-center gap-2 flex-wrap">
          {Object.entries(company.social).map(([platform, url]) => (
            <a key={platform} href={url} target="_blank" rel="noopener noreferrer"
              className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border border-[var(--border-secondary)]/30 bg-[var(--bg-primary)]/30 hover:bg-[var(--hover-accent)] transition-colors text-[9px] font-mono text-[var(--text-secondary)]">
              <ExternalLink className="w-2.5 h-2.5" />
              {platform.charAt(0).toUpperCase() + platform.slice(1)}
            </a>
          ))}
        </div>
      )}
    </motion.div>
  );

  // ── MAIN RENDER ──
  const renderContent = () => (
    <div className="flex flex-col gap-3">
      {/* Search Bar */}
      <div className="flex gap-1.5">
        <div className="flex-1 relative">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-[var(--text-muted)]" />
          <input
            type="text"
            value={query}
            onChange={e => setQuery(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && searchCompanies()}
            placeholder="Company name, domain, or keyword..."
            className="w-full bg-[var(--bg-primary)]/60 border border-[var(--border-primary)] rounded-lg pl-8 pr-3 py-2.5 text-[11px] font-mono text-[var(--text-primary)] placeholder:text-[var(--text-muted)]/40 focus:outline-none focus:border-[var(--gold-primary)]/40 transition-colors"
          />
        </div>
        <button
          onClick={searchCompanies}
          disabled={loading || !query.trim()}
          className="px-4 py-2 rounded-lg text-[10px] font-mono font-bold tracking-wider disabled:opacity-30 transition-all bg-[var(--gold-primary)]/15 border border-[var(--gold-primary)]/40 text-[var(--gold-primary)] hover:bg-[var(--gold-primary)]/25"
        >
          {loading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : 'SEARCH'}
        </button>
      </div>

      {/* Source badges */}
      {sources.length > 0 && !selectedCompany && (
        <div className="flex items-center gap-2">
          <span className="text-[8px] font-mono text-[var(--text-muted)]">SOURCES:</span>
          {sources.map(s => (
            <span key={s} className="text-[7px] font-mono px-1.5 py-0.5 rounded bg-[var(--bg-tertiary)] text-[var(--text-secondary)] border border-[var(--border-secondary)]/20">
              {s}
            </span>
          ))}
          <span className="text-[8px] font-mono text-[var(--text-muted)] ml-auto">{totalResults} results</span>
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="p-2.5 rounded-lg border border-red-500/30 bg-red-500/10 text-[11px] font-mono text-red-400 flex items-center gap-2">
          <AlertTriangle className="w-3.5 h-3.5 flex-shrink-0" />{error}
        </div>
      )}

      {/* Results */}
      <AnimatePresence mode="wait">
        {selectedCompany ? (
          <CompanyDetail key="detail" company={selectedCompany} />
        ) : results.length > 0 ? (
          <motion.div key="list" className="space-y-2 max-h-[55vh] overflow-y-auto styled-scrollbar pr-1">
            {results.map((company, i) => (
              <CompanyCard key={company.company_number || i} company={company} index={i} />
            ))}
          </motion.div>
        ) : null}
      </AnimatePresence>

      {/* Loading skeleton */}
      {loading && (
        <div className="space-y-2">
          {[1, 2, 3].map(i => (
            <div key={i} className="p-3 rounded-lg border border-[var(--border-secondary)]/20 bg-[var(--bg-primary)]/30 animate-pulse">
              <div className="flex items-center gap-3">
                <div className="w-9 h-9 rounded-lg bg-[var(--bg-tertiary)]" />
                <div className="flex-1 space-y-2">
                  <div className="h-3 w-3/4 bg-[var(--bg-tertiary)] rounded" />
                  <div className="h-2 w-1/2 bg-[var(--bg-tertiary)] rounded" />
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Empty state */}
      {!loading && !error && results.length === 0 && !selectedCompany && (
        <div className="text-center py-6">
          <Building2 className="w-8 h-8 mx-auto text-[var(--gold-primary)]/30 mb-2" />
          <p className="text-[10px] font-mono text-[var(--text-muted)]">Search any company worldwide</p>
          <p className="text-[8px] font-mono text-[var(--text-muted)]/60 mt-1">Officers, contacts, domain intelligence & more</p>
        </div>
      )}
    </div>
  );

  if (isMobile) return renderContent();

  return (
    <motion.div
      initial={{ opacity: 0, x: 20 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ delay: 0.4, duration: 0.6 }}
      className="glass-panel flex flex-col overflow-hidden pointer-events-auto"
    >
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center justify-between px-4 py-3 hover:bg-[var(--hover-accent)] transition-colors"
      >
        <div className="flex items-center gap-2">
          <Building2 className="w-4 h-4 text-[var(--gold-primary)]" />
          <span className="hud-text text-[12px] text-[var(--text-primary)]">COMPANY INTEL</span>
          <span className="text-[9px] font-mono text-[var(--text-muted)]">OSINT</span>
        </div>
        <div className="flex items-center gap-2">
          <div className="w-1.5 h-1.5 rounded-full bg-[var(--gold-primary)] animate-osiris-pulse" />
          {expanded ? <ChevronUp className="w-3.5 h-3.5 text-[var(--text-muted)]" /> : <ChevronDown className="w-3.5 h-3.5 text-[var(--text-muted)]" />}
        </div>
      </button>
      <AnimatePresence>
        {expanded && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="overflow-hidden px-3 pb-3"
          >
            {renderContent()}
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
}
