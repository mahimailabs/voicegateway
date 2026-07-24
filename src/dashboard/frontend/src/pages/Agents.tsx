import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import PageHeader from '../components/PageHeader';
import { fetchAgents } from '../lib/api';
import type { AgentRow } from '../lib/types';
import {
  agentStatus,
  agentStatusBadgeClass,
  formatCost,
  formatMs,
  formatRelativeTime,
  rosterStatusBadge,
} from '../lib/ui';

type SortKey =
  | 'agent_id'
  | 'last_seen'
  | 'total_cost_usd'
  | 'request_count'
  | 'p95_latency_ms'
  | 'error_rate'
  | 'memory_pct';

const COLUMNS: { key: SortKey; label: string }[] = [
  { key: 'agent_id', label: 'Agent' },
  { key: 'last_seen', label: 'Last seen' },
  { key: 'total_cost_usd', label: 'Cost (24h)' },
  { key: 'request_count', label: 'Requests (24h)' },
  { key: 'p95_latency_ms', label: 'p95 (24h)' },
  { key: 'error_rate', label: 'Error rate (24h)' },
  { key: 'memory_pct', label: 'Memory' },
];

/** Bar color for a memory-headroom reading: teal healthy, amber warm, red tight. */
function memoryBarColor(pct: number): string {
  if (pct >= 90) return '#dc2626';
  if (pct >= 75) return '#f59e0b';
  return 'var(--vg-teal, #1F96AA)';
}


export default function Agents() {
  const [agents, setAgents] = useState<AgentRow[]>([]);
  const [search, setSearch] = useState('');
  const [sortKey, setSortKey] = useState<SortKey>('last_seen');
  const [sortAsc, setSortAsc] = useState(false);

  useEffect(() => {
    const handle = setTimeout(() => {
      fetchAgents({ limit: 200, q: search.trim() || undefined })
        .then((d) => setAgents(d.agents))
        .catch(() => setAgents([]));
    }, 200);
    return () => clearTimeout(handle);
  }, [search]);

  const sorted = useMemo(() => {
    const rows = [...agents];
    rows.sort((a, b) => {
      const av = a[sortKey];
      const bv = b[sortKey];
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      const cmp =
        typeof av === 'string' || typeof bv === 'string'
          ? String(av).localeCompare(String(bv))
          : (av as number) - (bv as number);
      return sortAsc ? cmp : -cmp;
    });
    return rows;
  }, [agents, sortKey, sortAsc]);

  const onSort = (key: SortKey) => {
    if (key === sortKey) {
      setSortAsc((v) => !v);
    } else {
      setSortKey(key);
      setSortAsc(key === 'agent_id');
    }
  };

  return (
    <div>
      <PageHeader
        title="Agents"
        subtitle={`${agents.length} agent${agents.length === 1 ? '' : 's'} in the fleet (metrics over the last 24h)`}
        accent="blue"
      />

      <div className="vg-card mb-md" style={{ padding: '14px 20px' }}>
        <div className="flex-row gap-sm">
          <span className="vg-card__label" style={{ alignSelf: 'center' }}>Search</span>
          <input
            className="neo-input"
            placeholder="Filter by agent id..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            style={{ maxWidth: '20rem' }}
          />
        </div>
      </div>

      {sorted.length === 0 ? (
        <div className="empty-state mt-md">
          No agents yet. Agents appear here once they register (via{' '}
          <span className="mono">voicegateway.register_worker()</span>) or push
          telemetry (via <span className="mono">voicegateway.attach()</span>).
        </div>
      ) : (
        <div className="vg-card" style={{ padding: 0, overflow: 'hidden' }}>
          <table className="neo-table neo-table--blue">
            <thead>
              <tr>
                <th>Status</th>
                {COLUMNS.slice(0, 1).map((c) => (
                  <th
                    key={c.key}
                    onClick={() => onSort(c.key)}
                    style={{ cursor: 'pointer', userSelect: 'none' }}
                  >
                    {c.label}
                    {sortKey === c.key ? (sortAsc ? ' ▲' : ' ▼') : ''}
                  </th>
                ))}
                <th>Stack</th>
                {COLUMNS.slice(1).map((c) => (
                  <th
                    key={c.key}
                    onClick={() => onSort(c.key)}
                    style={{ cursor: 'pointer', userSelect: 'none' }}
                  >
                    {c.label}
                    {sortKey === c.key ? (sortAsc ? ' ▲' : ' ▼') : ''}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {sorted.map((a) => {
                // Registered workers show their live roster status (idle/busy/
                // offline), matching Server > Fleet; telemetry-only agents fall
                // back to the telemetry-recency status (active/idle/dormant).
                // `|| null` normalizes so the label and color never diverge.
                const fleet = a.fleet_status || null;
                const status = fleet ?? agentStatus(a.last_seen);
                const badgeClass = fleet
                  ? rosterStatusBadge(fleet)
                  : agentStatusBadgeClass(agentStatus(a.last_seen));
                return (
                  <tr key={a.agent_id}>
                    <td>
                      <span className={`neo-badge ${badgeClass}`}>{status}</span>
                    </td>
                    <td className="mono">
                      <Link to={`/agents/${encodeURIComponent(a.agent_id)}`}>
                        {a.agent_name || a.agent_id}
                      </Link>
                    </td>
                    <td>
                      <div className="flex-row gap-sm" style={{ flexWrap: 'wrap' }}>
                        {(['stt', 'llm', 'tts'] as const).map((m) => {
                          const model = a.models?.[m] ?? null;
                          return (
                            <span key={m} className="neo-badge neo-badge--black"
                                  title={model ? `${m.toUpperCase()}: ${model}` : `${m.toUpperCase()}: unknown`}
                                  style={{ opacity: model ? 1 : 0.4 }}>
                              {model ? model.split('/').pop() : '-'}
                            </span>
                          );
                        })}
                      </div>
                    </td>
                    <td>{formatRelativeTime(a.last_seen)}</td>
                    <td className="mono">{formatCost(a.total_cost_usd, 4)}</td>
                    <td>
                      <span className="neo-badge neo-badge--black">{a.request_count}</span>
                    </td>
                    <td className="mono">{formatMs(a.p95_latency_ms)}</td>
                    <td className="mono">{(a.error_rate * 100).toFixed(1)}%</td>
                    <td>
                      {a.memory_pct == null ? (
                        <span className="mono" style={{ color: 'var(--vg-muted)' }}>
                          -
                        </span>
                      ) : (
                        <div
                          className="flex-row gap-sm"
                          style={{ alignItems: 'center' }}
                          title={`RSS is ${a.memory_pct}% of this worker's memory ceiling`}
                        >
                          <span
                            style={{
                              display: 'inline-block',
                              width: 60,
                              height: 6,
                              borderRadius: 999,
                              background: 'rgba(31,150,170,0.15)',
                              overflow: 'hidden',
                              flexShrink: 0,
                            }}
                          >
                            <span
                              style={{
                                display: 'block',
                                width: `${Math.min(100, a.memory_pct)}%`,
                                height: '100%',
                                borderRadius: 999,
                                background: memoryBarColor(a.memory_pct),
                              }}
                            />
                          </span>
                          <span className="mono">{a.memory_pct}%</span>
                        </div>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
