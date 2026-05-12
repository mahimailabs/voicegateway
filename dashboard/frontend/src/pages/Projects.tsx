import { useEffect, useState } from 'react';
import PageHeader from '../components/PageHeader';
import SourceBadge from '../components/SourceBadge';
import { fetchJson } from '../lib/api';

interface ProjectBranding {
  logo_url?: string | null;
  accent_color?: string | null;
  product_name?: string | null;
}

interface ProjectEntry {
  id: string;
  name: string;
  description: string;
  daily_budget: number;
  budget_action: string;
  tags: string[];
  default_stack: string;
  accent: string;
  // v0.0.5: "yaml" (defined in voicegw.yaml), "db" (created via the
  // dashboard or MCP), or "auto" (the first-run default the gateway
  // populates when no projects: block exists yet).
  source?: string;
}

interface ProjectStats {
  requests_today: number;
  cost_today: number;
}

export default function Projects() {
  const [projects, setProjects] = useState<ProjectEntry[]>([]);
  const [stats, setStats] = useState<Record<string, ProjectStats>>({});
  const [showCreate, setShowCreate] = useState(false);
  const [brandingFor, setBrandingFor] = useState<string | null>(null);

  const refresh = () => {
    fetchJson<{ projects: ProjectEntry[]; stats: Record<string, ProjectStats> }>('/api/projects')
      .then((d) => { setProjects(d.projects); setStats(d.stats); })
      .catch(() => { setProjects([]); setStats({}); });
  };

  useEffect(() => { refresh(); }, []);

  return (
    <div>
      <PageHeader
        title="Projects"
        subtitle={`${projects.length} projects configured`}
        accent="orange"
        actions={
          <button className="neo-btn neo-btn--primary" onClick={() => setShowCreate(true)}>
            + Create Project
          </button>
        }
      />

      <div className="grid grid-cols-3">
        {projects.map((p) => {
          const s = stats[p.id];
          const spent = s?.cost_today ?? 0;
          const dailyBudget = p.daily_budget ?? 0;
          const pct = dailyBudget > 0 ? Math.min((spent / dailyBudget) * 100, 100) : 0;
          const barColor = pct >= 100 ? 'var(--accent-pink)' : pct >= 80 ? '#FFD166' : 'var(--accent-green)';
          const tags = p.tags ?? [];

          return (
            <div key={p.id} className="neo-card neo-card--strip-orange">
              <div className="flex-row" style={{ justifyContent: 'space-between' }}>
                <strong>{p.name}</strong>
                <SourceBadge source={p.source ?? 'yaml'} />
              </div>
              <div className="label mt-sm">{p.description || p.id}</div>

              {tags.length > 0 && (
                <div className="flex-row flex-wrap mt-sm">
                  {tags.map((t) => (
                    <span key={t} className="neo-badge neo-badge--black">{t}</span>
                  ))}
                </div>
              )}

              <div className="mt-md">
                <div className="label">Budget: ${spent.toFixed(2)} / ${dailyBudget.toFixed(2)}</div>
                <div className="budget-bar mt-sm">
                  <div className="budget-bar__fill" style={{ width: `${pct}%`, background: barColor }} />
                </div>
              </div>

              <div className="flex-row mt-md" style={{ justifyContent: 'space-between' }}>
                <span className="label">{s?.requests_today ?? 0} requests today</span>
                <span className="label">{p.budget_action}</span>
              </div>
              <div className="mt-sm">
                <button
                  type="button"
                  className="neo-btn neo-btn--small"
                  onClick={() => setBrandingFor(p.id)}
                >
                  Brand
                </button>
              </div>
            </div>
          );
        })}
        {projects.length === 0 && (
          <div className="neo-card"><div className="empty-state">No projects configured.</div></div>
        )}
      </div>

      {showCreate && <CreateProjectModal onClose={() => { setShowCreate(false); refresh(); }} />}
      {brandingFor && (
        <BrandingModal
          projectId={brandingFor}
          onClose={() => setBrandingFor(null)}
        />
      )}
    </div>
  );
}

function CreateProjectModal({ onClose }: { onClose: () => void }) {
  const [projectId, setProjectId] = useState('');
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [dailyBudget, setDailyBudget] = useState('0');
  const [budgetAction, setBudgetAction] = useState('warn');
  const [saving, setSaving] = useState(false);

  const slug = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');

  const save = async () => {
    setSaving(true);
    try {
      await fetchJson('/v1/projects', {
        method: 'POST',
        body: JSON.stringify({
          project_id: projectId || slug(name),
          name,
          description,
          daily_budget: parseFloat(dailyBudget) || 0,
          budget_action: budgetAction,
        }),
      });
      onClose();
    } catch (e) {
      alert((e as Error).message || 'Failed to create project');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="neo-modal-backdrop" onClick={onClose}>
      <div className="neo-modal" onClick={(e) => e.stopPropagation()}>
        <h3>Create Project</h3>
        <label className="label">Name</label>
        <input className="neo-input" value={name} onChange={(e) => { setName(e.target.value); if (!projectId) setProjectId(slug(e.target.value)); }} />
        <label className="label mt-md">Project ID</label>
        <input className="neo-input" value={projectId} onChange={(e) => setProjectId(e.target.value)} />
        <label className="label mt-md">Description</label>
        <textarea className="neo-input" rows={2} value={description} onChange={(e) => setDescription(e.target.value)} />
        <label className="label mt-md">Daily Budget (USD)</label>
        <input className="neo-input" type="number" min="0" step="0.5" value={dailyBudget} onChange={(e) => setDailyBudget(e.target.value)} />
        <label className="label mt-md">Budget Action</label>
        <select className="neo-select" value={budgetAction} onChange={(e) => setBudgetAction(e.target.value)}>
          <option value="warn">Warn</option>
          <option value="throttle">Throttle</option>
          <option value="block">Block</option>
        </select>
        <div className="flex-row mt-lg">
          <button className="neo-btn" onClick={onClose}>Cancel</button>
          <button className="neo-btn neo-btn--primary" onClick={save} disabled={saving || !name}>
            {saving ? 'Creating...' : 'Create Project'}
          </button>
        </div>
      </div>
    </div>
  );
}

function BrandingModal({
  projectId,
  onClose,
}: {
  projectId: string;
  onClose: () => void;
}) {
  const [logoUrl, setLogoUrl] = useState('');
  const [accentColor, setAccentColor] = useState('#333333');
  const [productName, setProductName] = useState('');
  const [logoFile, setLogoFile] = useState<File | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchJson<{ project_id: string; branding: ProjectBranding | null }>(
      `/api/projects/${encodeURIComponent(projectId)}/branding`,
    )
      .then((d) => {
        if (d.branding) {
          setLogoUrl(d.branding.logo_url ?? '');
          setAccentColor(d.branding.accent_color ?? '#333333');
          setProductName(d.branding.product_name ?? '');
        }
      })
      .catch(() => {
        // Project may have no branding yet; keep the input defaults.
      });
  }, [projectId]);

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      if (logoFile) {
        const fd = new FormData();
        fd.append('file', logoFile);
        const resp = await fetch(
          `/api/projects/${encodeURIComponent(projectId)}/branding/logo`,
          { method: 'POST', body: fd },
        );
        if (!resp.ok) {
          const body = await resp.json().catch(() => ({}));
          throw new Error(body.detail || `HTTP ${resp.status}`);
        }
        const data = (await resp.json()) as { logo_url: string };
        setLogoUrl(data.logo_url);
      }
      await fetchJson(`/api/projects/${encodeURIComponent(projectId)}/branding`, {
        method: 'POST',
        body: JSON.stringify({
          logo_url: logoFile ? null : logoUrl || null,
          accent_color: accentColor || null,
          product_name: productName.trim() || null,
        }),
      });
      onClose();
    } catch (e) {
      setError((e as Error).message || 'Failed to save branding');
    } finally {
      setSaving(false);
    }
  };

  const reset = async () => {
    if (!confirm('Reset branding to defaults for this project?')) return;
    setSaving(true);
    setError(null);
    try {
      await fetchJson(`/api/projects/${encodeURIComponent(projectId)}/branding`, {
        method: 'POST',
        body: JSON.stringify({}),
      });
      onClose();
    } catch (e) {
      setError((e as Error).message || 'Failed to reset branding');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="neo-modal-backdrop" onClick={onClose}>
      <div className="neo-modal" onClick={(e) => e.stopPropagation()} style={{ maxWidth: '32rem' }}>
        <h3>Branding · {projectId}</h3>
        <p className="label mt-sm">
          Per-project white-label. Logo, accent color, and product name
          override the default VoiceGateway brand for users scoped to
          this project (REQ-VG-ROUTE-004).
        </p>

        <label className="label mt-md">Product name</label>
        <input
          className="neo-input"
          value={productName}
          onChange={(e) => setProductName(e.target.value)}
          placeholder="(default: VoiceGateway)"
          maxLength={64}
        />

        <label className="label mt-md">Accent color</label>
        <div className="flex-row" style={{ gap: '0.5rem', alignItems: 'center' }}>
          <input
            type="color"
            value={accentColor}
            onChange={(e) => setAccentColor(e.target.value)}
            style={{ width: 48, height: 36, border: '2px solid black', cursor: 'pointer' }}
          />
          <input
            className="neo-input"
            value={accentColor}
            onChange={(e) => setAccentColor(e.target.value)}
            style={{ width: '8rem', fontFamily: 'monospace' }}
          />
        </div>

        <label className="label mt-md">Logo</label>
        {(logoFile || logoUrl) && (
          <div className="mt-sm" style={{ background: '#FFF9C2', padding: '0.5rem', border: '2px solid black' }}>
            <img
              src={logoFile ? URL.createObjectURL(logoFile) : logoUrl}
              alt="Logo preview"
              style={{ maxHeight: '48px', maxWidth: '200px', display: 'block' }}
            />
            <code style={{ fontSize: '0.75rem', wordBreak: 'break-all' }}>
              {logoFile ? logoFile.name : logoUrl}
            </code>
          </div>
        )}
        <input
          type="file"
          accept="image/png,image/svg+xml"
          className="neo-input mt-sm"
          onChange={(e) => setLogoFile(e.target.files?.[0] ?? null)}
        />
        <div className="label" style={{ fontSize: '0.7rem', opacity: 0.7 }}>
          PNG or SVG, max 256 KB, max 512x512 px.
        </div>

        {error && (
          <div className="mt-md" style={{ color: 'var(--accent-pink, #FF3D71)' }}>
            {error}
          </div>
        )}

        <div className="flex-row mt-lg" style={{ justifyContent: 'space-between' }}>
          <button className="neo-btn" onClick={reset} disabled={saving}>
            Reset
          </button>
          <div className="flex-row">
            <button className="neo-btn" onClick={onClose} disabled={saving}>
              Cancel
            </button>
            <button className="neo-btn neo-btn--primary" onClick={save} disabled={saving}>
              {saving ? 'Saving...' : 'Save'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
