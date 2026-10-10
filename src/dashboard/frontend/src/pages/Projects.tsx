import { useEffect, useState } from 'react';
import PageHeader from '../components/PageHeader';
import SourceBadge from '../components/SourceBadge';
import { fetchJson } from '../lib/api';
import { DEMO_MODE } from '../lib/demo';

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
          // Demo build is read-only: hide the create-project control.
          !DEMO_MODE && (
            <button className="neo-btn neo-btn--primary" onClick={() => setShowCreate(true)}>
              + Create Project
            </button>
          )
        }
      />

      <div className="grid grid-cols-3">
        {projects.map((p) => {
          const s = stats[p.id];
          const spent = s?.cost_today ?? 0;
          const dailyBudget = p.daily_budget ?? 0;
          const pct = dailyBudget > 0 ? Math.min((spent / dailyBudget) * 100, 100) : 0;
          const barColor =
            pct >= 100
              ? 'var(--vg-red)'
              : pct >= 80
              ? 'var(--vg-amber)'
              : 'var(--vg-green)';
          const tags = p.tags ?? [];

          return (
            <div key={p.id} className="vg-card">
              <div
                style={{
                  position: 'absolute',
                  top: 0, left: 0, right: 0, height: 3,
                  borderRadius: 'var(--vg-radius-sm) var(--vg-radius-sm) 0 0',
                  background: 'linear-gradient(90deg, var(--vg-teal), var(--vg-teal-bright))',
                }}
              />
              <div className="flex-row" style={{ justifyContent: 'space-between', paddingTop: 4 }}>
                <strong style={{ color: 'var(--vg-ink)', fontWeight: 700 }}>{p.name}</strong>
                <SourceBadge source={p.source ?? 'yaml'} />
              </div>
              <div className="vg-card__label mt-sm" style={{ textTransform: 'none', letterSpacing: 0, fontWeight: 500 }}>
                {p.description || p.id}
              </div>

              {tags.length > 0 && (
                <div className="flex-row flex-wrap mt-sm">
                  {tags.map((t) => (
                    <span key={t} className="neo-badge">{t}</span>
                  ))}
                </div>
              )}

              <div className="mt-md">
                <div className="vg-card__label">
                  Budget: ${spent.toFixed(2)} / ${dailyBudget.toFixed(2)}
                </div>
                <div className="budget-bar mt-sm">
                  <div
                    className="budget-bar__fill"
                    style={{ width: `${pct}%`, background: barColor }}
                  />
                </div>
              </div>

              <div className="flex-row mt-md" style={{ justifyContent: 'space-between' }}>
                <span className="vg-card__label">{s?.requests_today ?? 0} requests today</span>
                <span className="neo-badge neo-badge--info">{p.budget_action}</span>
              </div>
            </div>
          );
        })}
        {projects.length === 0 && (
          <div className="vg-card">
            <div className="empty-state">No projects configured.</div>
          </div>
        )}
      </div>

      {showCreate && <CreateProjectModal onClose={() => { setShowCreate(false); refresh(); }} />}
    </div>
  );
}

function CreateProjectModal({ onClose }: { onClose: () => void }) {
  const [projectId, setProjectId] = useState('');
  const [idEdited, setIdEdited] = useState(false);
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
        <h3 style={{ marginBottom: 20, color: 'var(--vg-ink)' }}>Create Project</h3>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <div>
            <label className="vg-card__label" style={{ display: 'block', marginBottom: 6 }}>Name</label>
            <input
              className="neo-input"
              value={name}
              onChange={(e) => { setName(e.target.value); if (!idEdited) setProjectId(slug(e.target.value)); }}
              style={{ width: '100%' }}
            />
          </div>
          <div>
            <label className="vg-card__label" style={{ display: 'block', marginBottom: 6 }}>Project ID</label>
            <input
              className="neo-input"
              value={projectId}
              onChange={(e) => { setIdEdited(true); setProjectId(e.target.value); }}
              style={{ width: '100%' }}
            />
          </div>
          <div>
            <label className="vg-card__label" style={{ display: 'block', marginBottom: 6 }}>Description</label>
            <textarea
              className="neo-input"
              rows={2}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              style={{ width: '100%', resize: 'vertical' }}
            />
          </div>
          <div>
            <label className="vg-card__label" style={{ display: 'block', marginBottom: 6 }}>Daily Budget (USD)</label>
            <input
              className="neo-input"
              type="number"
              min="0"
              step="0.5"
              value={dailyBudget}
              onChange={(e) => setDailyBudget(e.target.value)}
              style={{ width: '100%' }}
            />
          </div>
          <div>
            <label
              className="vg-card__label"
              style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 }}
            >
              Budget Action
              <span
                role="img"
                aria-label="What each budget action does"
                title={
                  'What happens once a project passes its daily budget ' +
                  '(applies only when Daily Budget is greater than 0):\n\n' +
                  'Warn (default) — keep serving requests, just log a warning. ' +
                  'Spend is not capped.\n\n' +
                  'Throttle — keep serving, but raise a throttle signal so your ' +
                  'agent (via guard()) can fall back to a cheaper or local model ' +
                  'instead of the paid provider, so spend stops growing.\n\n' +
                  'Block — reject further requests for the rest of the day with a ' +
                  'budget-exceeded error.'
                }
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  width: 15,
                  height: 15,
                  borderRadius: '50%',
                  border: '1.5px solid var(--vg-muted)',
                  color: 'var(--vg-muted)',
                  fontSize: 10,
                  fontWeight: 700,
                  lineHeight: 1,
                  cursor: 'help',
                }}
              >
                ?
              </span>
            </label>
            <select
              className="neo-select"
              value={budgetAction}
              onChange={(e) => setBudgetAction(e.target.value)}
              style={{ width: '100%' }}
            >
              <option value="warn">Warn</option>
              <option value="throttle">Throttle</option>
              <option value="block">Block</option>
            </select>
          </div>
        </div>

        <div className="flex-row mt-lg" style={{ justifyContent: 'flex-end' }}>
          <button className="neo-btn" onClick={onClose}>Cancel</button>
          <button className="neo-btn neo-btn--primary" onClick={save} disabled={saving || !name}>
            {saving ? 'Creating...' : 'Create Project'}
          </button>
        </div>
      </div>
    </div>
  );
}
