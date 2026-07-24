import { useEffect, useState } from 'react';
import PageHeader from '../components/PageHeader';
import SourceBadge from '../components/SourceBadge';
import { fetchJson } from '../lib/api';
import {
  AUTO,
  formatDateTime,
  listTimeZones,
  resolvedTimeZone,
  setTimeZonePref,
  useTimeZone,
} from '../lib/time';
import Projects from './Projects';
import ApiKeys from './ApiKeys';

interface AuditEntry {
  id: number;
  timestamp: number;
  entity_type: string;
  entity_id: string;
  action: string;
  changes: Record<string, unknown> | null;
  source: string;
}

const TABS = ['API Keys', 'Projects', 'General', 'Audit Log'] as const;
type Tab = (typeof TABS)[number];

export default function Settings({ tab: initialTab }: { tab?: string }) {
  const [activeTab, setActiveTab] = useState<Tab>(
    initialTab === 'audit' ? 'Audit Log' : 'API Keys'
  );

  // API Keys and Projects tabs render their own page (with its own PageHeader);
  // only the header-less tabs (General, Audit Log) get the Settings header, so
  // no tab shows a doubled header.
  const ownsHeader = activeTab === 'API Keys' || activeTab === 'Projects';

  return (
    <div>
      {!ownsHeader && (
        <PageHeader title="Settings" subtitle="Manage projects and configuration" accent="pink" />
      )}

      <div className="neo-tabs">
        {TABS.map((t) => (
          <button
            key={t}
            className={`neo-tab${activeTab === t ? ' neo-tab--active' : ''}`}
            onClick={() => setActiveTab(t)}
          >
            {t}
          </button>
        ))}
      </div>

      {activeTab === 'API Keys' && <ApiKeys />}
      {activeTab === 'Projects' && <Projects />}
      {activeTab === 'General' && <GeneralTab />}
      {activeTab === 'Audit Log' && <AuditLogTab />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// General Tab
// ---------------------------------------------------------------------------

interface OverviewData {
  total_requests: number;
  total_cost_today: number;
  total_cost_all: number;
  active_models: number;
  providers_configured: number;
}

function GeneralTab() {
  const [data, setData] = useState<OverviewData | null>(null);

  useEffect(() => {
    fetchJson<OverviewData>('/api/overview').then(setData);
  }, []);

  if (!data) return <div className="empty-state">Loading...</div>;

  return (
    <div className="mt-lg">
      <TimeZoneCard />
      <div className="neo-card mt-lg">
        <div className="label">Gateway Info</div>
        <table className="info-table mt-md">
          <tbody>
            <tr><td className="label">Providers</td><td>{data.providers_configured}</td></tr>
            <tr><td className="label">Active Models</td><td>{data.active_models}</td></tr>
            <tr><td className="label">Total Requests</td><td>{data.total_requests}</td></tr>
            <tr><td className="label">Total Cost (All Time)</td><td>${data.total_cost_all.toFixed(4)}</td></tr>
          </tbody>
        </table>
      </div>
    </div>
  );
}

// Timezone preference: a frontend-only choice (localStorage) that every
// dashboard timestamp renders in. "Auto" resolves to the browser zone, which
// on a co-located dashboard is the machine hosting the gateway.
function TimeZoneCard() {
  const pref = useTimeZone();
  const zones = listTimeZones();
  const resolved = resolvedTimeZone();
  return (
    <div className="neo-card">
      <div className="label">Timezone</div>
      <div style={{ marginTop: 6, fontSize: 13, color: 'var(--vg-muted)' }}>
        All dashboard times render in this zone.
      </div>
      <div className="flex-row gap-sm mt-md" style={{ alignItems: 'center', flexWrap: 'wrap' }}>
        <select
          className="neo-select"
          value={pref}
          onChange={(e) => setTimeZonePref(e.target.value)}
        >
          <option value={AUTO}>Auto (browser: {resolved})</option>
          {zones.map((z) => (
            <option key={z} value={z}>{z}</option>
          ))}
        </select>
        <span className="mono" style={{ fontSize: 13, color: 'var(--vg-ink)' }}>
          now {formatDateTime(Date.now())}
        </span>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Audit Log Tab
// ---------------------------------------------------------------------------

function AuditLogTab() {
  const [entries, setEntries] = useState<AuditEntry[]>([]);
  const [filterType, setFilterType] = useState('');
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setError(null);
    const url = filterType ? `/v1/audit-log?entity_type=${filterType}` : '/v1/audit-log';
    fetchJson<AuditEntry[]>(url)
      .then(setEntries)
      .catch((e) => { setEntries([]); setError(e.message || 'Failed to load audit log'); });
  }, [filterType]);

  return (
    <div className="mt-lg">
      {error && <div className="neo-card" style={{ borderLeft: '6px solid var(--accent-pink)', marginBottom: 16 }}>{error}</div>}
      <div className="filter-bar">
        <span className="label">Entity Type</span>
        <select className="neo-select" value={filterType} onChange={(e) => setFilterType(e.target.value)}>
          <option value="">All</option>
          <option value="provider">Provider</option>
          <option value="model">Model</option>
          <option value="project">Project</option>
        </select>
      </div>
      <table className="neo-table neo-table--pink">
        <thead>
          <tr><th>Time</th><th>Action</th><th>Type</th><th>ID</th><th>Source</th></tr>
        </thead>
        <tbody>
          {entries.map((e) => (
            <tr key={e.id}>
              <td className="mono">{formatDateTime(e.timestamp * 1000)}</td>
              <td><span className="neo-badge neo-badge--black">{e.action}</span></td>
              <td>{e.entity_type}</td>
              <td className="mono">{e.entity_id}</td>
              <td><SourceBadge source={e.source} /></td>
            </tr>
          ))}
          {entries.length === 0 && (
            <tr><td colSpan={5} className="empty-state">No audit log entries.</td></tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
