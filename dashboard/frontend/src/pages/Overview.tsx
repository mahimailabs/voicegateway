import { useEffect, useState } from 'react';
import PageHeader from '../components/PageHeader';
import StatusCard from '../components/StatusCard';
import { fetchJson } from '../lib/api';
import { formatCost } from '../lib/ui';
import type { OverviewResponse } from '../lib/types';

export default function Overview() {
  const [data, setData] = useState<OverviewResponse | null>(null);

  useEffect(() => {
    fetchJson<OverviewResponse>('/api/overview').then(setData).catch(() => setData(null));
  }, []);

  if (!data) return <div className="empty-state">Loading overview...</div>;

  return (
    <div>
      <PageHeader
        title="Overview"
        subtitle="Live voice AI gateway stats"
        accent="yellow"
        actions={
          <>
            <button className="neo-btn neo-btn--primary">Refresh</button>
            <button className="neo-btn">Export</button>
          </>
        }
      />
      <div className="grid grid-cols-4">
        <StatusCard label="Total Requests" value={data.total_requests ?? 0} accent="yellow" icon="R" />
        <StatusCard label="Cost Today" value={formatCost(data.total_cost_today)} accent="green" icon="$" />
        <StatusCard label="Cost (All Time)" value={formatCost(data.total_cost_all)} accent="blue" icon="Σ" />
        <StatusCard label="Active Models" value={data.active_models ?? 0} accent="pink" icon="M" />
      </div>

      <div className="mt-lg grid grid-cols-2">
        <div className="neo-card neo-card--strip-yellow">
          <div className="label">Providers Configured</div>
          <div className="stat-value mt-md">{data.providers_configured ?? 0}</div>
          <div className="label mt-md">This dashboard is served by the gateway itself.</div>
        </div>
        <div className="neo-card neo-card--strip-blue">
          <div className="label">Quick Actions</div>
          <div className="flex-row flex-wrap mt-md">
            <button className="neo-btn neo-btn--primary">Refresh</button>
            <button className="neo-btn neo-btn--blue">View Models</button>
            <button className="neo-btn neo-btn--green">View Costs</button>
          </div>
        </div>
      </div>
    </div>
  );
}
