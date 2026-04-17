import { useCallback, useEffect, useState } from 'react';
import PageHeader from '../components/PageHeader';
import SourceBadge from '../components/SourceBadge';
import { fetchJson } from '../lib/api';

interface ProviderRow {
  provider_id: string;
  source: string;
  api_key_masked: string;
  base_url: string | null;
}

interface AuditEntry {
  id: number;
  timestamp: number;
  entity_type: string;
  entity_id: string;
  action: string;
  changes: Record<string, unknown> | null;
  source: string;
}

const TABS = ['Providers', 'Models', 'General', 'Audit Log'] as const;
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

      {activeTab === 'Providers' && <ProvidersTab />}
      {activeTab === 'Models' && <ModelsTab />}
      {activeTab === 'General' && <GeneralTab />}
      {activeTab === 'Audit Log' && <AuditLogTab />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Providers Tab
// ---------------------------------------------------------------------------

function ProvidersTab() {
  const [providers, setProviders] = useState<ProviderRow[]>([]);
  const [showAdd, setShowAdd] = useState(false);

  const refresh = useCallback(() => {
    fetchJson<{ providers: Record<string, { configured: boolean; type: string }> }>('/api/status').then((d) => {
      const mapped: ProviderRow[] = Object.entries(d.providers).map(([id, p]) => ({
        provider_id: id,
        source: p.type === 'local' ? 'auto' : 'yaml',
        api_key_masked: '',
        base_url: null,
      }));
      setProviders(mapped);
    });
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  return (
    <div className="mt-lg">
      <div className="flex-row" style={{ justifyContent: 'space-between', marginBottom: 16 }}>
        <h3>Configured Providers</h3>
        <button className="neo-btn neo-btn--primary" onClick={() => setShowAdd(true)}>+ Add Provider</button>
      </div>
      <table className="neo-table neo-table--pink">
        <thead>
          <tr><th>Provider</th><th>Status</th><th>Source</th><th>Actions</th></tr>
        </thead>
        <tbody>
          {providers.map((p) => (
            <tr key={p.provider_id}>
              <td className="mono">{p.provider_id}</td>
              <td><span className="neo-badge neo-badge--online">Configured</span></td>
              <td><SourceBadge source={p.source} /></td>
              <td>
                <button className="neo-btn neo-btn--sm" onClick={() => testProvider(p.provider_id)}>Test</button>
              </td>
            </tr>
          ))}
          {providers.length === 0 && (
            <tr><td colSpan={4} className="empty-state">No providers configured.</td></tr>
          )}
        </tbody>
      </table>
      {showAdd && <AddProviderModal onClose={() => { setShowAdd(false); refresh(); }} />}
    </div>
  );
}

async function testProvider(id: string) {
  try {
    const res = await fetch(`/v1/providers/${id}/test`, { method: 'POST' });
    const data = await res.json();
    alert(data.status === 'ok' ? `${id}: OK (${data.latency_ms}ms)` : `${id}: ${data.message}`);
  } catch {
    alert(`${id}: test failed`);
  }
}

function AddProviderModal({ onClose }: { onClose: () => void }) {
  const [providerId, setProviderId] = useState('');
  const [providerType, setProviderType] = useState('deepgram');
  const [apiKey, setApiKey] = useState('');
  const [baseUrl, setBaseUrl] = useState('');
  const [saving, setSaving] = useState(false);

  const save = async () => {
    setSaving(true);
    try {
      const res = await fetch('/v1/providers', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          provider_id: providerId || providerType,
          provider_type: providerType,
          api_key: apiKey,
          base_url: baseUrl || undefined,
        }),
      });
      if (res.ok) {
        onClose();
      } else {
        const err = await res.json();
        alert(err.detail || 'Failed to add provider');
      }
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="neo-modal-backdrop" onClick={onClose}>
      <div className="neo-modal" onClick={(e) => e.stopPropagation()}>
        <h3>Add Provider</h3>
        <label className="label">Provider Type</label>
        <select className="neo-select" value={providerType} onChange={(e) => { setProviderType(e.target.value); if (!providerId) setProviderId(''); }}>
          {['deepgram','openai','anthropic','groq','cartesia','elevenlabs','assemblyai','ollama','whisper','kokoro','piper'].map(t => (
            <option key={t} value={t}>{t}</option>
          ))}
        </select>
        <label className="label mt-md">Provider ID</label>
        <input className="neo-input" placeholder={providerType} value={providerId} onChange={(e) => setProviderId(e.target.value)} />
        <label className="label mt-md">API Key</label>
        <input className="neo-input" type="password" value={apiKey} onChange={(e) => setApiKey(e.target.value)} />
        <label className="label mt-md">Base URL (optional)</label>
        <input className="neo-input" placeholder="e.g. http://localhost:11434" value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} />
        <div className="flex-row mt-lg">
          <button className="neo-btn" onClick={onClose}>Cancel</button>
          <button className="neo-btn neo-btn--primary" onClick={save} disabled={saving}>
            {saving ? 'Saving...' : 'Save Provider'}
          </button>
        </div>
      </div>
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

  useEffect(() => {
    const url = filterType ? `/v1/audit-log?entity_type=${filterType}` : '/v1/audit-log';
    fetchJson<AuditEntry[]>(url).then(setEntries).catch(() => setEntries([]));
  }, [filterType]);

  return (
    <div className="mt-lg">
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
