import { useEffect, useState } from 'react';
import PageHeader from '../components/PageHeader';
import StatusCard from '../components/StatusCard';
import LatencyChart from '../components/LatencyChart';
import { fetchJson } from '../lib/api';
import { latencyBadgeClass, formatMs } from '../lib/ui';
import type { LatencyResponse } from '../lib/types';

function percentile(values: number[], pct: number): number {
  if (values.length === 0) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const idx = Math.floor((sorted.length * pct) / 100);
  return sorted[Math.min(idx, sorted.length - 1)];
}

export default function Latency() {
  const [data, setData] = useState<LatencyResponse | null>(null);

  useEffect(() => {
    fetchJson<LatencyResponse>('/api/latency').then(setData).catch(() => setData(null));
  }, []);

  if (!data) return <div className="empty-state">Loading latency...</div>;

  const entries = Object.entries(data);
  const ttfb = entries.map(([, s]) => s.avg_ttfb_ms || 0);
  const p50 = percentile(ttfb, 50);
  const p95 = percentile(ttfb, 95);
  const p99 = percentile(ttfb, 99);

  return (
    <div>
      <PageHeader title="Latency" subtitle="Time to first byte (TTFB) and total" accent="pink" />

      <div className="grid grid-cols-3 mb-lg">
        <StatusCard label="P50 TTFB" value={`${p50.toFixed(0)}ms`} accent="pink" icon="50" />
        <StatusCard label="P95 TTFB" value={`${p95.toFixed(0)}ms`} accent="pink" icon="95" />
        <StatusCard label="P99 TTFB" value={`${p99.toFixed(0)}ms`} accent="pink" icon="99" />
      </div>

      <div className="mb-lg">
        <LatencyChart data={data} />
      </div>

      {entries.length > 0 && (
        <table className="neo-table neo-table--pink">
          <thead>
            <tr>
              <th>Model</th>
              <th>Avg TTFB</th>
              <th>Avg Total</th>
              <th>Requests</th>
            </tr>
          </thead>
          <tbody>
            {entries.map(([model, stats]) => (
              <tr key={model}>
                <td className="mono">{model}</td>
                <td>
                  <span className={`neo-badge ${latencyBadgeClass(stats.avg_ttfb_ms)}`}>
                    {formatMs(stats.avg_ttfb_ms)}
                  </span>
                </td>
                <td>
                  <span className={`neo-badge ${latencyBadgeClass(stats.avg_latency_ms)}`}>
                    {formatMs(stats.avg_latency_ms)}
                  </span>
                </td>
                <td>
                  <span className="neo-badge neo-badge--black">{stats.request_count}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
