import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import PageHeader from '../components/PageHeader';
import StatusCard from '../components/StatusCard';
import { fetchAgents, fetchJson } from '../lib/api';
import type { AgentRow, SessionRow } from '../lib/types';
import {
  agentStatus,
  agentStatusBadgeClass,
  formatCost,
  formatMs,
  formatRelativeTime,
  rosterStatusBadge,
} from '../lib/ui';
import { formatDateTime } from '../lib/time';

/** Detail view for a single agent: 24h metrics, model stack, recent calls. */
export default function AgentDetail() {
  const { agentId = '' } = useParams<{ agentId: string }>();
  const [agent, setAgent] = useState<AgentRow | null>(null);
  const [calls, setCalls] = useState<SessionRow[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    // The /api/agents/{id} endpoint omits the model stack; the list carries it,
    // so resolve the full row from a scoped list fetch.
    fetchAgents({ q: agentId, limit: 200 })
      .then((d) => {
        if (alive) setAgent(d.agents.find((a) => a.agent_id === agentId) ?? null);
      })
      .catch(() => alive && setAgent(null))
      .finally(() => alive && setLoading(false));
    fetchJson<SessionRow[]>(
      `/api/sessions?agent=${encodeURIComponent(agentId)}&order_by=started_at_desc&limit=25`,
    )
      .then((rows) => alive && setCalls(rows))
      .catch(() => alive && setCalls([]));
    return () => {
      alive = false;
    };
  }, [agentId]);

  if (loading) return <div className="empty-state">Loading agent...</div>;

  if (!agent) {
    return (
      <div>
        <PageHeader
          title={agentId}
          subtitle="Agent detail"
          accent="blue"
          actions={<Link className="neo-btn" to="/agents">← All agents</Link>}
        />
        <div className="empty-state">No telemetry for this agent yet.</div>
      </div>
    );
  }

  // Match the Agents list: live roster status (idle/busy/offline) when present,
  // else the telemetry-recency status. `|| null` keeps label and color in sync.
  const fleet = agent.fleet_status || null;
  const status = fleet ?? agentStatus(agent.last_seen);
  const badgeClass = fleet
    ? rosterStatusBadge(fleet)
    : agentStatusBadgeClass(agentStatus(agent.last_seen));

  return (
    <div>
      <PageHeader
        title={agent.agent_name || agent.agent_id}
        subtitle={`Last seen ${formatRelativeTime(agent.last_seen)} · metrics over the last 24h`}
        accent="blue"
        actions={<Link className="neo-btn" to="/agents">← All agents</Link>}
      />

      <div className="flex-row gap-sm mb-md" style={{ alignItems: 'center' }}>
        <span className={`neo-badge ${badgeClass}`}>{status}</span>
        {(['stt', 'llm', 'tts'] as const).map((m) => {
          const model = agent.models?.[m] ?? null;
          return (
            <span
              key={m}
              className="neo-badge neo-badge--black"
              title={model ? `${m.toUpperCase()}: ${model}` : `${m.toUpperCase()}: unknown`}
              style={{ opacity: model ? 1 : 0.4 }}
            >
              {model ? model.split('/').pop() : '-'}
            </span>
          );
        })}
      </div>

      <div className="grid grid-cols-4 mb-lg">
        <StatusCard label="Cost (24h)" value={formatCost(agent.total_cost_usd, 4)} accent="blue" icon="$" />
        <StatusCard label="Requests (24h)" value={agent.request_count} accent="blue" icon="#" />
        <StatusCard label="p95 latency" value={formatMs(agent.p95_latency_ms)} accent="blue" icon="95" />
        <StatusCard label="Error rate" value={`${(agent.error_rate * 100).toFixed(1)}%`} accent="blue" icon="!" />
      </div>

      <div className="vg-card">
        <div className="vg-card__label" style={{ marginBottom: 12 }}>Recent calls</div>
        {calls.length === 0 ? (
          <div className="empty-state">No calls recorded for this agent.</div>
        ) : (
          <table className="neo-table neo-table--blue">
            <thead>
              <tr>
                <th>Call</th>
                <th>Started</th>
                <th>Modalities</th>
                <th>Requests</th>
                <th>Cost</th>
              </tr>
            </thead>
            <tbody>
              {calls.map((c) => (
                <tr key={c.id}>
                  <td className="mono">{c.id}</td>
                  <td>{formatDateTime(c.started_at)}</td>
                  <td>{c.modalities.join(' · ')}</td>
                  <td>{c.request_count}</td>
                  <td className="mono">{formatCost(c.total_cost_usd, 4)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
