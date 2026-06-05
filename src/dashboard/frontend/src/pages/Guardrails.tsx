import { useEffect, useMemo, useState } from 'react';
import FilterBar, { useTenantFilter } from '../components/FilterBar';
import PageHeader from '../components/PageHeader';
import TenantPill from '../components/TenantPill';
import {
  fetchGuardrailAggregate,
  fetchGuardrailEvents,
  fetchJson,
} from '../lib/api';
import type {
  GuardrailAggregate,
  GuardrailCategory,
  GuardrailEvent,
  GuardrailTopSession,
} from '../lib/types';

interface ProjectEntry {
  id: string;
  name: string;
}

const CATEGORIES: GuardrailCategory[] = [
  'pii',
  'financial',
  'medical',
  'prompt_injection',
  'off_topic',
];

const ACTIONS = ['redact', 'block', 'alert'] as const;

const ACTION_CLASS: Record<string, string> = {
  redact: 'neo-badge--pink',
  block: 'neo-badge--black',
  alert: 'neo-badge--blue',
};

export default function Guardrails() {
  const [projects, setProjects] = useState<ProjectEntry[]>([]);
  const [project, setProject] = useState('');
  const [days, setDays] = useState(7);
  const [category, setCategory] = useState<GuardrailCategory | ''>('');
  const [counts, setCounts] = useState<GuardrailAggregate[]>([]);
  const [topSessions, setTopSessions] = useState<GuardrailTopSession[]>([]);
  const [events, setEvents] = useState<GuardrailEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const tenant = useTenantFilter();

  useEffect(() => {
    fetchJson<{ projects: ProjectEntry[] }>('/api/projects')
      .then((d) => setProjects(d.projects))
      .catch(() => setProjects([]));
  }, []);

  useEffect(() => {
    setLoading(true);
    Promise.all([
      fetchGuardrailAggregate({ project, days, tenant, category }),
      fetchGuardrailEvents({ project, days, tenant, category, limit: 50 }),
    ])
      .then(([aggregate, eventData]) => {
        setCounts(aggregate.counts);
        setTopSessions(aggregate.top_sessions);
        setEvents(eventData.events);
      })
      .catch(() => {
        setCounts([]);
        setTopSessions([]);
        setEvents([]);
      })
      .finally(() => setLoading(false));
  }, [project, days, tenant, category]);

  const matrix = useMemo(() => {
    const rows = new Map<string, Record<string, number>>();
    for (const c of CATEGORIES) rows.set(c, { redact: 0, block: 0, alert: 0 });
    for (const row of counts) {
      rows.set(row.category, {
        ...(rows.get(row.category) ?? { redact: 0, block: 0, alert: 0 }),
        [row.action]: row.count,
      });
    }
    return rows;
  }, [counts]);

  const total = counts.reduce((sum, row) => sum + row.count, 0);

  return (
    <div>
      <PageHeader
        title="Guardrails"
        subtitle={`${total} fired event${total === 1 ? '' : 's'} in window`}
        accent="pink"
      />

      <FilterBar
        showAgent={false}
        projectSlot={
          <>
            <span className="label">Project</span>
            <select
              className="neo-select"
              value={project}
              onChange={(e) => setProject(e.target.value)}
            >
              <option value="">All projects</option>
              {projects.map((p) => (
                <option key={p.id} value={p.id}>{p.name}</option>
              ))}
            </select>
          </>
        }
        extra={
          <>
            <span className="label">Window</span>
            <select
              className="neo-select"
              value={days}
              onChange={(e) => setDays(Number(e.target.value))}
            >
              <option value={1}>1 day</option>
              <option value={7}>7 days</option>
              <option value={30}>30 days</option>
              <option value={90}>90 days</option>
            </select>
            <span className="label">Category</span>
            <select
              className="neo-select"
              value={category}
              onChange={(e) => setCategory(e.target.value as GuardrailCategory | '')}
            >
              <option value="">All categories</option>
              {CATEGORIES.map((c) => (
                <option key={c} value={c}>{labelCategory(c)}</option>
              ))}
            </select>
          </>
        }
      />

      {loading ? (
        <div className="empty-state">Loading guardrail activity...</div>
      ) : (
        <>
          <table className="neo-table neo-table--pink">
            <thead>
              <tr>
                <th>Category</th>
                {ACTIONS.map((action) => (
                  <th key={action}>{action.toUpperCase()}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {CATEGORIES.map((c) => {
                const row = matrix.get(c) ?? { redact: 0, block: 0, alert: 0 };
                return (
                  <tr
                    key={c}
                    onClick={() => setCategory(c)}
                    style={{ cursor: 'pointer' }}
                  >
                    <td>{labelCategory(c)}</td>
                    {ACTIONS.map((action) => (
                      <td key={action}>
                        <span className={`neo-badge ${ACTION_CLASS[action]}`}>
                          {row[action]}
                        </span>
                      </td>
                    ))}
                  </tr>
                );
              })}
            </tbody>
          </table>

          {category && (
            <div className="neo-card neo-card--strip-pink mt-lg">
              <div className="label">Top sessions for {labelCategory(category)}</div>
              {topSessions.length === 0 ? (
                <div className="empty-state mt-sm">No sessions in this window.</div>
              ) : (
                <table className="neo-table mt-sm">
                  <thead>
                    <tr>
                      <th>Session</th>
                      <th>Project</th>
                      <th>Tenant</th>
                      <th>Events</th>
                      <th>Last event</th>
                    </tr>
                  </thead>
                  <tbody>
                    {topSessions.map((row) => (
                      <tr key={row.session_id}>
                        <td className="mono">{row.session_id}</td>
                        <td>{row.project ?? '-'}</td>
                        <td><TenantPill tenantId={row.tenant_id} /></td>
                        <td><span className="neo-badge neo-badge--black">{row.count}</span></td>
                        <td className="mono">{row.last_event_at ?? '-'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          )}

          <div className="neo-card mt-lg">
            <div className="label">Recent audit events</div>
            {events.length === 0 ? (
              <div className="empty-state mt-sm">Zero guardrail events found.</div>
            ) : (
              <table className="neo-table mt-sm">
                <thead>
                  <tr>
                    <th>Time</th>
                    <th>Session</th>
                    <th>Category</th>
                    <th>Action</th>
                    <th>Context</th>
                  </tr>
                </thead>
                <tbody>
                  {events.map((event) => (
                    <tr key={event.id}>
                      <td className="mono">{event.created_at}</td>
                      <td className="mono">{event.session_id}</td>
                      <td>{event.category ? labelCategory(event.category) : event.event_type}</td>
                      <td>
                        {event.action ? (
                          <span className={`neo-badge ${ACTION_CLASS[event.action]}`}>
                            {event.action}
                          </span>
                        ) : '-'}
                      </td>
                      <td>{event.context_excerpt ?? '-'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </>
      )}
    </div>
  );
}

function labelCategory(category: GuardrailCategory): string {
  return category.replace(/_/g, ' ').replace(/\b\w/g, (m) => m.toUpperCase());
}
