import { useEffect, useState } from 'react';
import PageHeader from '../components/PageHeader';
import StatusCard from '../components/StatusCard';
import LatencyChart from '../components/LatencyChart';
import { fetchJson } from '../lib/api';
import { latencyBadgeClass, formatMs } from '../lib/ui';
import type { LatencyResponse, LatencyStats } from '../lib/types';

function worstP(entries: [string, LatencyStats][], key: 'p50' | 'p95' | 'p99'): number {
  let max = 0;
  for (const [, s] of entries) {
    const v = s.ttfb_percentiles?.[key];
    if (typeof v === 'number' && v > max) max = v;
  }
  return max;
}

function fmtP(v: number | null | undefined): string {
  return typeof v === 'number' ? formatMs(v) : '—';
}

export default function Latency() {
  const [data, setData] = useState<LatencyResponse | null>(null);

  useEffect(() => {
    fetchJson<LatencyResponse>('/api/latency').then(setData).catch(() => setData(null));
  }, []);

  if (!data) return <div className="empty-state">Loading latency...</div>;

  const entries = Object.entries(data);
  const p50 = worstP(entries, 'p50');
  const p95 = worstP(entries, 'p95');
  const p99 = worstP(entries, 'p99');

  return (
    <div>
      <PageHeader
        title="Latency"
        subtitle="Worst-model TTFB percentiles across today's requests"
        accent="pink"
      />

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
              <th>P95 TTFB</th>
              <th>P95 Total</th>
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
                  <span className={`neo-badge ${latencyBadgeClass(stats.ttfb_percentiles?.p95 ?? stats.avg_ttfb_ms)}`}>
                    {fmtP(stats.ttfb_percentiles?.p95)}
                  </span>
                </td>
                <td>
                  <span className={`neo-badge ${latencyBadgeClass(stats.latency_percentiles?.p95 ?? stats.avg_latency_ms)}`}>
                    {fmtP(stats.latency_percentiles?.p95)}
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
