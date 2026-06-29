import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import PageHeader from '../components/PageHeader';
import StatusCard from '../components/StatusCard';
import TrendChart from '../components/TrendChart';
import { Skeleton, StatCardSkeleton } from '../components/Skeleton';
import { fetchJson, fetchAgents } from '../lib/api';
import { formatCost, agentStatus } from '../lib/ui';
import type { OverviewResponse, AgentRow } from '../lib/types';

export default function Overview() {
  const [data, setData] = useState<OverviewResponse | null>(null);
  const [agents, setAgents] = useState<AgentRow[]>([]);

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
          <div className="neo-card">
            <Skeleton width={120} height={13} />
            <Skeleton height={88} style={{ marginTop: 16 }} />
          </div>
          <div className="neo-card">
            <Skeleton width={120} height={13} />
            <Skeleton height={88} style={{ marginTop: 16 }} />
          </div>
        </div>
      </div>
    );
  }

  const activeCount = agents.filter((a) => agentStatus(a.last_seen) === 'active').length;
  const topAgents = [...agents]
    .sort((a, b) => b.total_cost_usd - a.total_cost_usd)
    .slice(0, 3);

  return (
    <div>
      <PageHeader
        title="Overview"
        subtitle="Live voice AI gateway stats"
        accent="yellow"
        actions={
          <button className="neo-btn neo-btn--primary" onClick={load}>Refresh</button>
        }
      />
      <div className="grid grid-cols-4">
        <StatusCard label="Total Requests" value={data.total_requests ?? 0} accent="yellow" icon="R" />
        <StatusCard label="Cost Today" value={formatCost(data.total_cost_today)} accent="green" icon="$" />
        <StatusCard label="Cost (All Time)" value={formatCost(data.total_cost_all)} accent="blue" icon="Σ" />
        <StatusCard label="Active Models" value={data.active_models ?? 0} accent="pink" icon="M" />
      </div>

      <div className="mt-lg">
        <TrendChart />
      </div>

      {agents.length > 0 && (
        <div className="mt-lg neo-card neo-card--strip-blue">
          <div
            className="flex-row"
            style={{ justifyContent: 'space-between', alignItems: 'baseline' }}
          >
            <div className="label">Fleet</div>
            <Link className="neo-btn" to="/agents">
              View all agents →
            </Link>
          </div>
          <div className="flex-row flex-wrap mt-md" style={{ gap: '2.5rem' }}>
            <div>
              <div className="stat-value">{agents.length}</div>
              <div className="label">Agents</div>
            </div>
            <div>
              <div className="stat-value">{activeCount}</div>
              <div className="label">Active now</div>
            </div>
          </div>
          {topAgents.length > 0 && (
            <div className="mt-md">
              <div
                className="label"
                style={{ fontSize: '0.7rem', textTransform: 'uppercase', marginBottom: '0.25rem' }}
              >
                Top by cost (24h)
              </div>
              {topAgents.map((a) => (
                <div
                  key={a.agent_id}
                  className="flex-row mt-sm"
                  style={{ justifyContent: 'space-between' }}
                >
                  <Link className="mono" to={`/costs?agent=${encodeURIComponent(a.agent_id)}`}>
                    {a.agent_id}
                  </Link>
                  <span className="mono">{formatCost(a.total_cost_usd, 4)}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      <div className="mt-lg grid grid-cols-2">
        <div className="neo-card neo-card--strip-yellow">
          <div className="label">Providers Configured</div>
          <div className="stat-value mt-md">{data.providers_configured ?? 0}</div>
          <div className="label mt-md">This dashboard is served by the gateway itself.</div>
        </div>
        <div className="neo-card neo-card--strip-blue">
          <div className="label">Quick Actions</div>
          <div className="flex-row flex-wrap mt-md">
            <Link className="neo-btn neo-btn--primary" to="/costs">View Costs</Link>
            <Link className="neo-btn" to="/models">View Models</Link>
            <Link className="neo-btn" to="/agents">View Agents</Link>
          </div>
        </div>
      </div>
    </div>
  );
}
