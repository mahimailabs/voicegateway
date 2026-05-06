// v0.0.5 Providers page — see design.md section 3.5.
//
// 5.9 #1 ships the page skeleton: header + "Add Provider" button +
// grid of project cards listing per-project provider keys. 5.9 #2-#4
// will fill in the modal, per-row actions (rotate / delete / test),
// and the source badge wiring. 5.9 #5 wires this to the new
// /api/providers/by-project endpoint (currently a placeholder until
// the dashboard backend gets the per-project list).

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

// 5.9 #2 will replace this stub with a full modal: project selector,
// provider dropdown over the 11 supported providers, masked api_key
// input, "Test connection" button, save/cancel. For 5.9 #1 the modal
// is a placeholder that explains where the work lands.
function AddProviderModal({
  projects,
  onClose,
}: {
  projects: ProjectEntry[];
  onClose: () => void;
}) {
  return (
    <div className="neo-modal-backdrop" onClick={onClose}>
      <div className="neo-modal" onClick={(e) => e.stopPropagation()}>
        <h3>Add Provider Key</h3>
        <p className="label">
          Scoping a key to one of {projects.length} project{projects.length === 1 ? '' : 's'}.
          The full add form (project + provider + api_key + test) lands in 5.9 #2.
        </p>
        <div className="flex-row mt-lg">
          <button className="neo-btn" onClick={onClose}>Close</button>
        </div>
      </div>
    </div>
  );
}
