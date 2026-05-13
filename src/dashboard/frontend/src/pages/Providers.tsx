// v0.0.5 Providers page — see design.md section 3.5.
//
// 5.9 #1: page skeleton (header + grid + Add button).
// 5.9 #2: full Add Provider modal.
// 5.9 #3: per-row actions (test/rotate/delete) and last-test
//         green/red indicator (this iteration).
// 5.9 #4: SourceBadge wiring (already present from #1).
// 5.9 #5: dashboard backend HTTP endpoints for the per-project
//         reads/writes. Until that lands the modal save and the
//         page fetch surface as the empty state rather than
//         crashing.

import { useEffect, useState } from 'react';
import PageHeader from '../components/PageHeader';
import SourceBadge from '../components/SourceBadge';
import { fetchJson } from '../lib/api';

interface ProjectEntry {
  id: string;
  name: string;
  description: string;
  tags: string[];
}

interface ProjectProviderRow {
  project: string;
  provider: string;
  provider_id: string;
  source: string;
  api_key_masked: string | null;
  base_url: string | null;
  type: string;
}

type ProvidersByProject = Record<string, ProjectProviderRow[]>;

type RowTestStatus =
  | { kind: 'unknown' }
  | { kind: 'testing' }
  | { kind: 'ok'; latency_ms: number }
  | { kind: 'failed'; message: string };

export default function Providers() {
  const [projects, setProjects] = useState<ProjectEntry[]>([]);
  const [byProject, setByProject] = useState<ProvidersByProject>({});
  const [showAdd, setShowAdd] = useState(false);
  const [rotateTarget, setRotateTarget] = useState<ProjectProviderRow | null>(null);
  const [testResults, setTestResults] = useState<Record<string, RowTestStatus>>({});

  const refresh = () => {
    fetchJson<{ projects: ProjectEntry[] }>('/api/projects')
      .then((d) => setProjects(d.projects))
      .catch(() => setProjects([]));
    fetchJson<{ providers: ProjectProviderRow[] }>('/api/providers/by-project')
      .then((d) => {
        const grouped: ProvidersByProject = {};
        for (const row of d.providers) {
          if (!grouped[row.project]) grouped[row.project] = [];
          grouped[row.project].push(row);
        }
        setByProject(grouped);
      })
      .catch(() => setByProject({}));
  };

  useEffect(() => { refresh(); }, []);

  const setRowTestStatus = (id: string, status: RowTestStatus) =>
    setTestResults((prev) => ({ ...prev, [id]: status }));

  const handleTestRow = async (row: ProjectProviderRow) => {
    setRowTestStatus(row.provider_id, { kind: 'testing' });
    try {
      const result = await fetchJson<{ status: string; latency_ms: number; message?: string }>(
        `/v1/providers/${encodeURIComponent(row.provider_id)}/test`,
        { method: 'POST' },
      );
      if (result.status === 'ok') {
        setRowTestStatus(row.provider_id, { kind: 'ok', latency_ms: result.latency_ms });
      } else {
        setRowTestStatus(row.provider_id, {
          kind: 'failed',
          message: result.message ?? 'Provider returned unhealthy',
        });
      }
    } catch (e) {
      setRowTestStatus(row.provider_id, {
        kind: 'failed',
        message: (e as Error).message || 'Test failed',
      });
    }
  };

  const handleDeleteRow = async (row: ProjectProviderRow) => {
    if (row.source !== 'db') {
      alert('YAML-defined providers cannot be deleted via the dashboard. Edit voicegw.yaml.');
      return;
    }
    const ok = window.confirm(
      `Remove ${row.provider} key from project "${row.project}"? This cannot be undone.`,
    );
    if (!ok) return;
    try {
      // ?confirm=true is required by /v1/providers/{id} DELETE — without
      // it the backend returns {would_delete: ...} as a dry-run without
      // touching the row. The window.confirm() above is the user-facing
      // gate; the query string just unlocks the backend operation.
      await fetchJson(
        `/v1/providers/${encodeURIComponent(row.provider_id)}?confirm=true`,
        { method: 'DELETE' },
      );
      setTestResults((prev) => {
        const { [row.provider_id]: _gone, ...rest } = prev;
        return rest;
      });
      refresh();
    } catch (e) {
      alert((e as Error).message || 'Delete failed');
    }
  };

  const projectCount = projects.length;
  const totalKeys = Object.values(byProject).reduce(
    (acc, rows) => acc + rows.length,
    0,
  );

  return (
    <div>
      <PageHeader
        title="Providers"
        subtitle={`${totalKeys} per-project key${totalKeys === 1 ? '' : 's'} across ${projectCount} project${projectCount === 1 ? '' : 's'}`}
        accent="pink"
        actions={
          <button
            className="neo-btn neo-btn--primary"
            onClick={() => setShowAdd(true)}
          >
            + Add Provider
          </button>
        }
      />

      <div className="grid grid-cols-2">
        {projects.map((p) => {
          const rows = byProject[p.id] ?? [];
          return (
            <div key={p.id} className="neo-card neo-card--strip-pink">
              <div className="flex-row" style={{ justifyContent: 'space-between' }}>
                <strong>{p.name}</strong>
                <span className="label">{rows.length} provider{rows.length === 1 ? '' : 's'}</span>
              </div>
              <div className="label mt-sm">{p.description || p.id}</div>

              {rows.length === 0 ? (
                <div className="empty-state mt-md">
                  No per-project keys yet. Click <strong>+ Add Provider</strong> to scope a key to this project.
                </div>
              ) : (
                <table className="neo-table mt-md">
                  <thead>
                    <tr>
                      <th>Provider</th>
                      <th>Key</th>
                      <th>Source</th>
                      <th>Status</th>
                      <th style={{ textAlign: 'right' }}>Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((row) => {
                      const test = testResults[row.provider_id] ?? { kind: 'unknown' };
                      const isDb = row.source === 'db';
                      return (
                        <tr key={row.provider_id}>
                          <td className="mono">{row.provider}</td>
                          <td className="mono">{row.api_key_masked ?? '—'}</td>
                          <td><SourceBadge source={row.source} /></td>
                          <td><TestDot status={test} /></td>
                          <td style={{ textAlign: 'right' }}>
                            <button
                              className="neo-btn neo-btn--small"
                              type="button"
                              onClick={() => handleTestRow(row)}
                              disabled={test.kind === 'testing'}
                            >
                              {test.kind === 'testing' ? 'Testing…' : 'Test'}
                            </button>
                            {' '}
                            <button
                              className="neo-btn neo-btn--small"
                              type="button"
                              onClick={() => setRotateTarget(row)}
                              disabled={!isDb}
                              title={isDb ? 'Rotate the api key' : 'YAML-defined keys: edit voicegw.yaml'}
                            >
                              Rotate
                            </button>
                            {' '}
                            <button
                              className="neo-btn neo-btn--small neo-btn--danger"
                              type="button"
                              onClick={() => handleDeleteRow(row)}
                              disabled={!isDb}
                              title={isDb ? 'Delete this key' : 'YAML-defined keys: edit voicegw.yaml'}
                            >
                              Delete
                            </button>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              )}
            </div>
          );
        })}
        {projects.length === 0 && (
          <div className="neo-card">
            <div className="empty-state">
              No projects configured. Add a project under the Projects page first; per-project provider keys live within projects.
            </div>
          </div>
        )}
      </div>

      {showAdd && (
        <AddProviderModal
          projects={projects}
          onClose={() => { setShowAdd(false); refresh(); }}
        />
      )}
      {rotateTarget && (
        <RotateProviderModal
          row={rotateTarget}
          onClose={() => { setRotateTarget(null); refresh(); }}
        />
      )}
    </div>
  );
}

function TestDot({ status }: { status: RowTestStatus }) {
  let color = '#888';
  let label = 'untested';
  let title = 'Click Test to verify the key';
  if (status.kind === 'testing') {
    color = '#FFD166';
    label = 'testing';
    title = 'Health check in flight';
  } else if (status.kind === 'ok') {
    color = '#8AC926';
    label = `ok ${status.latency_ms}ms`;
    title = `Last test ok in ${status.latency_ms}ms`;
  } else if (status.kind === 'failed') {
    color = '#FF006E';
    label = 'failed';
    title = status.message;
  }
  return (
    <span title={title} style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
      <span
        aria-hidden="true"
        style={{
          display: 'inline-block',
          width: 10,
          height: 10,
          borderRadius: '50%',
          background: color,
          border: '1px solid var(--ink, #000)',
        }}
      />
      <span className="label" style={{ fontSize: 11 }}>{label}</span>
    </span>
  );
}

function RotateProviderModal({
  row,
  onClose,
}: {
  row: ProjectProviderRow;
  onClose: () => void;
}) {
  const [apiKey, setApiKey] = useState('');
  const [baseUrl, setBaseUrl] = useState(row.base_url ?? '');
  const [revealKey, setRevealKey] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSave = async () => {
    if (!apiKey) return;
    setSaving(true);
    setError(null);
    try {
      await fetchJson(`/v1/providers/${encodeURIComponent(row.provider_id)}`, {
        method: 'PATCH',
        body: JSON.stringify({
          api_key: apiKey,
          base_url: baseUrl || null,
        }),
      });
      onClose();
    } catch (e) {
      setError((e as Error).message || 'Rotate failed');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="neo-modal-backdrop" onClick={onClose}>
      <div className="neo-modal" onClick={(e) => e.stopPropagation()}>
        <h3>Rotate Provider Key</h3>
        <p className="label">
          {row.project} / <span className="mono">{row.provider}</span>
        </p>

        <label className="label mt-md">New API Key</label>
        <div className="flex-row" style={{ gap: '8px' }}>
          <input
            className="neo-input"
            type={revealKey ? 'text' : 'password'}
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder="sk-..."
            autoComplete="off"
            spellCheck={false}
            disabled={saving}
            style={{ flex: 1 }}
          />
          <button
            className="neo-btn"
            type="button"
            onClick={() => setRevealKey((r) => !r)}
            disabled={saving}
          >
            {revealKey ? 'Hide' : 'Show'}
          </button>
        </div>

        <label className="label mt-md">Base URL (optional)</label>
        <input
          className="neo-input"
          value={baseUrl}
          onChange={(e) => setBaseUrl(e.target.value)}
          placeholder="https://api.openai.com/v1"
          disabled={saving}
        />

        {error && (
          <div className="empty-state mt-md" style={{ color: 'var(--accent-pink)' }}>
            {error}
          </div>
        )}

        <div className="flex-row mt-lg">
          <button className="neo-btn" onClick={onClose} disabled={saving}>Cancel</button>
          <button
            className="neo-btn neo-btn--primary"
            onClick={handleSave}
            disabled={!apiKey || saving}
          >
            {saving ? 'Rotating...' : 'Rotate Key'}
          </button>
        </div>
      </div>
    </div>
  );
}

// The eleven providers wired up in voicegateway.core.registry. Order
// matches the registry's sort so the dropdown is alphabetical.
const SUPPORTED_PROVIDERS = [
  'anthropic',
  'assemblyai',
  'cartesia',
  'deepgram',
  'elevenlabs',
  'groq',
  'kokoro',
  'ollama',
  'openai',
  'piper',
  'whisper',
] as const;

type TestState =
  | { kind: 'idle' }
  | { kind: 'testing' }
  | { kind: 'ok'; latency_ms: number }
  | { kind: 'failed'; message: string };

function AddProviderModal({
  projects,
  onClose,
}: {
  projects: ProjectEntry[];
  onClose: () => void;
}) {
  const firstProject = projects[0]?.id ?? '';
  const [project, setProject] = useState(firstProject);
  const [provider, setProvider] = useState<string>('openai');
  const [apiKey, setApiKey] = useState('');
  const [baseUrl, setBaseUrl] = useState('');
  const [revealKey, setRevealKey] = useState(false);
  const [saving, setSaving] = useState(false);
  const [testState, setTestState] = useState<TestState>({ kind: 'idle' });
  const [error, setError] = useState<string | null>(null);

  const canSave = project && provider && apiKey && !saving;
  const canTest = canSave && testState.kind !== 'testing';

  const handleTest = async () => {
    setTestState({ kind: 'testing' });
    try {
      // Stateless health check: send provider_type + api_key directly,
      // backend runs health_check without persisting. No sentinel-row
      // round-trip means the test cannot leak managed_providers
      // entries on transient network failures.
      const result = await fetchJson<{ status: string; latency_ms: number; message?: string }>(
        '/v1/providers/test',
        {
          method: 'POST',
          body: JSON.stringify({
            provider_type: provider,
            api_key: apiKey,
            base_url: baseUrl || null,
          }),
        },
      );
      if (result.status === 'ok') {
        setTestState({ kind: 'ok', latency_ms: result.latency_ms });
      } else {
        setTestState({
          kind: 'failed',
          message: result.message ?? 'Provider returned unhealthy',
        });
      }
    } catch (e) {
      setTestState({ kind: 'failed', message: (e as Error).message || 'Test failed' });
    }
  };

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    try {
      // 5.9 #5 will wire /api/providers/by-project. Until then we POST
      // to /v1/providers with a composite "<project>:<provider>" id so
      // the row at least lands in managed_providers; it stays
      // project-NULL until the backend wiring honours the project
      // field. Documented in the modal's caption above the Save button.
      await fetchJson('/v1/providers', {
        method: 'POST',
        body: JSON.stringify({
          provider_id: `${project}:${provider}`,
          provider_type: provider,
          api_key: apiKey,
          base_url: baseUrl || null,
          project,
        }),
      });
      onClose();
    } catch (e) {
      setError((e as Error).message || 'Save failed');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="neo-modal-backdrop" onClick={onClose}>
      <div className="neo-modal" onClick={(e) => e.stopPropagation()}>
        <h3>Add Provider Key</h3>

        <label className="label">Project</label>
        <select
          className="neo-select"
          value={project}
          onChange={(e) => setProject(e.target.value)}
          disabled={saving}
        >
          {projects.length === 0 ? (
            <option value="">No projects configured</option>
          ) : null}
          {projects.map((p) => (
            <option key={p.id} value={p.id}>{p.name} ({p.id})</option>
          ))}
        </select>

        <label className="label mt-md">Provider</label>
        <select
          className="neo-select"
          value={provider}
          onChange={(e) => setProvider(e.target.value)}
          disabled={saving}
        >
          {SUPPORTED_PROVIDERS.map((p) => (
            <option key={p} value={p}>{p}</option>
          ))}
        </select>

        <label className="label mt-md">API Key</label>
        <div className="flex-row" style={{ gap: '8px' }}>
          <input
            className="neo-input"
            type={revealKey ? 'text' : 'password'}
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder="sk-..."
            autoComplete="off"
            spellCheck={false}
            disabled={saving}
            style={{ flex: 1 }}
          />
          <button
            className="neo-btn"
            type="button"
            onClick={() => setRevealKey((r) => !r)}
            disabled={saving}
          >
            {revealKey ? 'Hide' : 'Show'}
          </button>
        </div>

        <label className="label mt-md">Base URL (optional)</label>
        <input
          className="neo-input"
          value={baseUrl}
          onChange={(e) => setBaseUrl(e.target.value)}
          placeholder="https://api.openai.com/v1"
          disabled={saving}
        />

        <div className="mt-md">
          <button
            className="neo-btn"
            type="button"
            onClick={handleTest}
            disabled={!canTest}
          >
            {testState.kind === 'testing' ? 'Testing...' : 'Test connection'}
          </button>
          {testState.kind === 'ok' && (
            <span className="neo-badge neo-badge--online" style={{ marginLeft: 12 }}>
              OK ({testState.latency_ms}ms)
            </span>
          )}
          {testState.kind === 'failed' && (
            <span className="neo-badge neo-badge--offline" style={{ marginLeft: 12 }}>
              {testState.message}
            </span>
          )}
        </div>

        {error && (
          <div className="empty-state mt-md" style={{ color: 'var(--accent-pink)' }}>
            {error}
          </div>
        )}

        <div className="flex-row mt-lg">
          <button className="neo-btn" onClick={onClose} disabled={saving}>Cancel</button>
          <button
            className="neo-btn neo-btn--primary"
            onClick={handleSave}
            disabled={!canSave}
          >
            {saving ? 'Saving...' : 'Save'}
          </button>
        </div>
      </div>
    </div>
  );
}
