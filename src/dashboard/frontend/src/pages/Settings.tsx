import { useEffect, useState } from 'react';
import PageHeader from '../components/PageHeader';
import SourceBadge from '../components/SourceBadge';
import { fetchJson } from '../lib/api';
import Projects from './Projects';
import Providers from './Providers';
import Routing from './Routing';
import Guardrails from './Guardrails';
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

const TABS = ['Providers', 'API Keys', 'Projects', 'Routing', 'Guardrails', 'Models', 'General', 'Audit Log'] as const;
type Tab = (typeof TABS)[number];

export default function Settings({ tab: initialTab }: { tab?: string }) {
  const [activeTab, setActiveTab] = useState<Tab>(
    initialTab === 'audit' ? 'Audit Log' : 'Providers'
  );

  return (
    <div>
      <PageHeader title="Settings" subtitle="Manage providers, models, and configuration" accent="pink" />

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

      {activeTab === 'Providers' && <Providers />}
      {activeTab === 'API Keys' && <ApiKeys />}
      {activeTab === 'Projects' && <Projects />}
      {activeTab === 'Routing' && <Routing />}
      {activeTab === 'Guardrails' && <Guardrails />}
      {activeTab === 'Models' && <ModelsTab />}
      {activeTab === 'General' && <GeneralTab />}
      {activeTab === 'Audit Log' && <AuditLogTab />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Models Tab
// ---------------------------------------------------------------------------

interface ModelRow {
  model_id: string;
  modality: string;
  provider: string;
}

function ModelsTab() {
  const [models, setModels] = useState<Record<string, ModelRow>>({});

  useEffect(() => {
    fetchJson<{ providers: Record<string, unknown>; models: Record<string, ModelRow> }>('/api/status')
      .then(d => setModels(d.models));
  }, []);

  const byModality: Record<string, [string, ModelRow][]> = {};
  for (const [id, m] of Object.entries(models)) {
    (byModality[m.modality] ??= []).push([id, m]);
  }

  return (
    <div className="mt-lg">
      {['stt', 'llm', 'tts'].map((mod) => (
        <div key={mod} className="mb-lg">
          <h3>{mod.toUpperCase()} Models</h3>
          <table className="neo-table neo-table--pink">
            <thead>
              <tr><th>Model ID</th><th>Provider</th><th>Status</th></tr>
            </thead>
            <tbody>
              {(byModality[mod] || []).map(([id, m]) => (
                <tr key={id}>
                  <td className="mono">{id}</td>
                  <td><span className="neo-badge neo-badge--blue">{m.provider}</span></td>
                  <td><span className="neo-badge neo-badge--online">Active</span></td>
                </tr>
              ))}
              {(!byModality[mod] || byModality[mod].length === 0) && (
                <tr><td colSpan={3} className="empty-state">No {mod.toUpperCase()} models</td></tr>
              )}
            </tbody>
          </table>
        </div>
      ))}
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
      <div className="neo-card neo-card--strip-pink">
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
              <td className="mono">{new Date(e.timestamp * 1000).toLocaleString()}</td>
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
