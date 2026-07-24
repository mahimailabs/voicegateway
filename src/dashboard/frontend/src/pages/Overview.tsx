import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import PageHeader from '../components/PageHeader';
import StatusCard from '../components/StatusCard';
import TrendChart from '../components/TrendChart';
import { Skeleton, StatCardSkeleton } from '../components/Skeleton';
import AgentCard from '../components/AgentCard';
import { fetchJson, fetchAgents } from '../lib/api';
import { formatCost, agentStatus } from '../lib/ui';
import type { OverviewResponse, AgentRow } from '../lib/types';

export default function Overview() {
  const [data, setData] = useState<OverviewResponse | null>(null);
  const [agents, setAgents] = useState<AgentRow[]>([]);
  // Bumped on an explicit Refresh so the self-fetching TrendChart reloads too.
  const [reloadKey, setReloadKey] = useState(0);

  const load = useCallback(() => {
    fetchJson<OverviewResponse>('/api/overview').then(setData).catch(() => setData(null));
    fetchAgents({ limit: 200 })
      .then((d) => setAgents(d.agents))
      .catch(() => setAgents([]));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  if (!data) {
    return (
      <div>
        <PageHeader title="Overview" subtitle="Live voice AI gateway stats" accent="yellow" />
        <div className="grid grid-cols-4">
          <StatCardSkeleton />
          <StatCardSkeleton />
          <StatCardSkeleton />
          <StatCardSkeleton />
        </div>
        <div className="mt-lg grid grid-cols-2">
          <div className="vg-card">
            <Skeleton width={120} height={13} />
            <Skeleton height={88} style={{ marginTop: 16 }} />
          </div>
          <div className="vg-card">
            <Skeleton width={120} height={13} />
            <Skeleton height={88} style={{ marginTop: 16 }} />
          </div>
        </div>
      </div>
    );
  }

  const activeCount = agents.filter((a) => agentStatus(a.last_seen) === 'active').length;
  // Cards, cost-ranked. Cap the Overview grid; the full fleet lives on /agents.
  const cardAgents = [...agents]
    .sort((a, b) => b.total_cost_usd - a.total_cost_usd)
    .slice(0, 6);

  return (
    <div>
      <PageHeader
        title="Overview"
        subtitle="Live voice AI gateway stats"
        accent="yellow"
        actions={
          <button
            className="neo-btn neo-btn--primary"
            onClick={() => {
              load();
              setReloadKey((k) => k + 1);
            }}
          >
            Refresh
          </button>
        }
      />
      <div className="grid grid-cols-4">
        <StatusCard label="Total Requests" value={data.total_requests ?? 0} accent="yellow" icon="R" />
        <StatusCard label="Cost Today" value={formatCost(data.total_cost_today)} accent="green" icon="$" />
        <StatusCard label="Cost (All Time)" value={formatCost(data.total_cost_all)} accent="blue" icon="Σ" />
        <StatusCard label="Active Models" value={data.active_models ?? 0} accent="pink" icon="M" />
      </div>

      <div className="mt-lg">
        <TrendChart reloadKey={reloadKey} />
      </div>

      {agents.length > 0 && (
        <div className="mt-lg">
          <div
            className="flex-row"
            style={{ justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 12 }}
          >
            <div className="vg-card__label">
              Fleet · {agents.length} agent{agents.length === 1 ? '' : 's'}
              {activeCount > 0 ? ` · ${activeCount} active` : ''}
            </div>
            <Link className="neo-btn" to="/agents" style={{ fontSize: 12, padding: '5px 12px' }}>
              View all agents &rarr;
            </Link>
          </div>
          <div className="grid grid-cols-3">
            {cardAgents.map((a) => (
              <AgentCard key={a.agent_id} agent={a} />
            ))}
          </div>
        </div>
      )}

      <div className="mt-lg grid grid-cols-2">
        <div className="vg-card">
          <div className="vg-card__label">Providers Configured</div>
          <div className="vg-stat" style={{ marginTop: 6 }}>{data.providers_configured ?? 0}</div>
          <div style={{ marginTop: 10, fontSize: 13, color: 'var(--vg-muted)' }}>This dashboard is served by the gateway itself.</div>
        </div>
        <div className="vg-card">
          <div className="vg-card__label" style={{ marginBottom: 12 }}>Quick Actions</div>
          <div className="flex-row flex-wrap" style={{ gap: 8 }}>
            <Link className="neo-btn neo-btn--primary" to="/costs">View Costs</Link>
            <Link className="neo-btn" to="/agents">View Agents</Link>
          </div>
        </div>
      </div>
    </div>
  );
}
