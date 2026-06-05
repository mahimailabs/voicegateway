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
} from '../lib/ui';

type SortKey =
  | 'agent_id'
  | 'last_seen'
  | 'total_cost_usd'
  | 'request_count'
  | 'p95_latency_ms'
  | 'error_rate';

const COLUMNS: { key: SortKey; label: string }[] = [
  { key: 'agent_id', label: 'Agent' },
  { key: 'last_seen', label: 'Last seen' },
  { key: 'total_cost_usd', label: 'Cost (24h)' },
  { key: 'request_count', label: 'Requests (24h)' },
  { key: 'p95_latency_ms', label: 'p95 (24h)' },
  { key: 'error_rate', label: 'Error rate (24h)' },
];

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

      <div className="filter-bar">
        <span className="label">Search</span>
        <input
          className="neo-input"
          placeholder="Filter by agent id…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          style={{ maxWidth: '20rem' }}
        />
      </div>

      {sorted.length === 0 ? (
        <div className="empty-state mt-lg">
          No agents yet. Agents appear here once they push telemetry to the
          collector (via <span className="mono">voicegateway.attach()</span> or a
          remote sink).
        </div>
      ) : (
        <table className="neo-table neo-table--blue mt-lg">
          <thead>
            <tr>
              <th>Status</th>
              {COLUMNS.map((c) => (
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
              const status = agentStatus(a.last_seen);
              return (
                <tr key={a.agent_id}>
                  <td>
                    <span className={`neo-badge ${agentStatusBadgeClass(status)}`}>
                      {status}
                    </span>
                  </td>
                  <td className="mono">
                    <Link to={`/costs?agent=${encodeURIComponent(a.agent_id)}`}>
                      {a.agent_id}
                    </Link>
                  </td>
                  <td>{formatRelativeTime(a.last_seen)}</td>
                  <td className="mono">{formatCost(a.total_cost_usd, 4)}</td>
                  <td>
                    <span className="neo-badge neo-badge--black">{a.request_count}</span>
                  </td>
                  <td className="mono">{formatMs(a.p95_latency_ms)}</td>
                  <td className="mono">{(a.error_rate * 100).toFixed(1)}%</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}
