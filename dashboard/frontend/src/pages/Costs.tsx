import { useEffect, useState } from 'react';
import PageHeader from '../components/PageHeader';
import CostChart from '../components/CostChart';
import { fetchJson } from '../lib/api';
import { formatCost } from '../lib/ui';
import type { CostsResponse } from '../lib/types';

export default function Costs() {
  const [data, setData] = useState<CostsResponse | null>(null);

  useEffect(() => {
    fetchJson<CostsResponse>('/api/costs').then(setData).catch(() => setData(null));
  }, []);

  if (!data) return <div className="empty-state">Loading costs...</div>;

  const models = Object.entries(data.by_model);

  return (
    <div>
      <PageHeader title="Costs" subtitle={`Period: ${data.period}`} accent="green" />

      <div className="neo-card neo-card--strip-green mb-lg">
        <div className="label">Total Spend</div>
        <div className="stat-value stat-value--xl mt-md">{formatCost(data.total)}</div>
      </div>

      <div className="grid grid-cols-2">
        <CostChart title="By Provider" data={data.by_provider} />
        <CostChart title="By Model" data={data.by_model} />
      </div>

      {models.length > 0 && (
        <div className="mt-lg">
          <table className="neo-table neo-table--green">
            <thead>
              <tr>
                <th>Model</th>
                <th>Requests</th>
                <th>Cost</th>
              </tr>
            </thead>
            <tbody>
              {models.map(([name, info]) => (
                <tr key={name}>
                  <td className="mono">{name}</td>
                  <td>
                    <span className="neo-badge neo-badge--black">{info.requests}</span>
                  </td>
                  <td className="mono">{formatCost(info.cost, 6)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
