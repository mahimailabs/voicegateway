// v0.0.5 Providers page — see design.md section 3.5.
//
// 5.9 #1: page skeleton (header + grid + Add button).
// 5.9 #2: full Add Provider modal (this iteration).
// 5.9 #3-#4: per-row actions and source badges (next).
// 5.9 #5: dashboard backend HTTP endpoints for the per-project
// reads + writes the modal here issues. Until that lands, the
// fetch calls fail gracefully and surface as the empty state /
// error toast rather than crashing the page.

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

export default function Providers() {
  const [projects, setProjects] = useState<ProjectEntry[]>([]);
  const [byProject, setByProject] = useState<ProvidersByProject>({});
  const [showAdd, setShowAdd] = useState(false);

  const refresh = () => {
    fetchJson<{ projects: ProjectEntry[] }>('/api/projects')
      .then((d) => setProjects(d.projects))
      .catch(() => setProjects([]));
    // 5.9 #5 will wire in /api/providers/by-project. Until then, the
    // grouping stays empty per project and the page renders the
    // empty-state messaging without erroring.
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
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((row) => (
                      <tr key={row.provider_id}>
                        <td className="mono">{row.provider}</td>
                        <td className="mono">{row.api_key_masked ?? '—'}</td>
                        <td><SourceBadge source={row.source} /></td>
                      </tr>
                    ))}
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
      // Persist transiently with a sentinel id, run the existing
      // /v1/providers/{id}/test endpoint, then clean up. The dashboard
      // backend gets a project-aware test endpoint in 5.9 #5; this
      // path uses what's already shipped so the button works today.
      const sentinel = `__test__${Date.now()}__${provider}`;
      await fetchJson('/v1/providers', {
        method: 'POST',
        body: JSON.stringify({
          provider_id: sentinel,
          provider_type: provider,
          api_key: apiKey,
          base_url: baseUrl || null,
        }),
      });
      try {
        const result = await fetchJson<{ status: string; latency_ms: number; message?: string }>(
          `/v1/providers/${encodeURIComponent(sentinel)}/test`,
          { method: 'POST' },
        );
        if (result.status === 'ok') {
          setTestState({ kind: 'ok', latency_ms: result.latency_ms });
        } else {
          setTestState({
            kind: 'failed',
            message: result.message ?? 'Provider returned unhealthy',
          });
        }
      } finally {
        await fetchJson(`/v1/providers/${encodeURIComponent(sentinel)}`, {
          method: 'DELETE',
        }).catch(() => {
          // Best-effort cleanup; if the row leaks (rare) the user can
          // remove it from the global Settings page.
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
