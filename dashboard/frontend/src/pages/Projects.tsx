import { useEffect, useState } from 'react';
import PageHeader from '../components/PageHeader';
import SourceBadge from '../components/SourceBadge';
import { fetchJson } from '../lib/api';

interface ProjectEntry {
  id: string;
  name: string;
  description: string;
  daily_budget: number;
  budget_action: string;
  tags: string[];
  default_stack: string;
  accent: string;
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
      .then((d) => { setProjects(d.projects); setStats(d.stats); });
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
          const pct = p.daily_budget > 0 ? Math.min((spent / p.daily_budget) * 100, 100) : 0;
          const barColor = pct >= 100 ? 'var(--accent-pink)' : pct >= 80 ? '#FFD166' : 'var(--accent-green)';

          return (
            <div key={p.id} className="neo-card neo-card--strip-orange">
              <div className="flex-row" style={{ justifyContent: 'space-between' }}>
                <strong>{p.name}</strong>
                <SourceBadge source={p.accent === 'blue' ? 'yaml' : 'db'} />
              </div>
              <div className="label mt-sm">{p.description || p.id}</div>

              {p.tags.length > 0 && (
                <div className="flex-row flex-wrap mt-sm">
                  {p.tags.map((t) => (
                    <span key={t} className="neo-badge neo-badge--black">{t}</span>
                  ))}
                </div>
              )}

              <div className="mt-md">
                <div className="label">Budget: ${spent.toFixed(2)} / ${p.daily_budget.toFixed(2)}</div>
                <div className="budget-bar mt-sm">
                  <div className="budget-bar__fill" style={{ width: `${pct}%`, background: barColor }} />
                </div>
              </div>

              <div className="flex-row mt-md" style={{ justifyContent: 'space-between' }}>
                <span className="label">{s?.requests_today ?? 0} requests today</span>
                <span className="label">{p.budget_action}</span>
              </div>
            </div>
          );
        })}
        {projects.length === 0 && (
          <div className="neo-card"><div className="empty-state">No projects configured.</div></div>
        )}
      </div>

      {showCreate && <CreateProjectModal onClose={() => { setShowCreate(false); refresh(); }} />}
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
      const res = await fetch('/v1/projects', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          project_id: projectId || slug(name),
          name,
          description,
          daily_budget: parseFloat(dailyBudget) || 0,
          budget_action: budgetAction,
        }),
      });
      if (res.ok) {
        onClose();
      } else {
        const err = await res.json();
        alert(err.detail || 'Failed to create project');
      }
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
