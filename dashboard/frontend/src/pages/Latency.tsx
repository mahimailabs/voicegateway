import { useEffect, useState } from 'react';
import PageHeader from '../components/PageHeader';
import StatusCard from '../components/StatusCard';
import LatencyChart from '../components/LatencyChart';
import { fetchJson } from '../lib/api';
import { latencyBadgeClass, formatMs } from '../lib/ui';
import type { LatencyResponse, LatencyStats } from '../lib/types';

/** Return the highest model percentile, or null when no model has that key. */
function worstP(
  entries: [string, LatencyStats][],
  key: 'p50' | 'p95' | 'p99',
): number | null {
  let max: number | null = null;
  for (const [, s] of entries) {
    const v = s.ttfb_percentiles?.[key];
    if (typeof v === 'number' && (max === null || v > max)) max = v;
  }
  return max;
}

function fmtP(v: number | null | undefined): string {
  return typeof v === 'number' ? formatMs(v) : '—';
}

/** Badge class given a (possibly missing) latency value. */
function badgeFor(v: number | null | undefined): string {
  return typeof v === 'number' ? latencyBadgeClass(v) : 'neo-badge--black';
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
        <StatusCard label="P50 TTFB" value={fmtP(p50)} accent="pink" icon="50" />
        <StatusCard label="P95 TTFB" value={fmtP(p95)} accent="pink" icon="95" />
        <StatusCard label="P99 TTFB" value={fmtP(p99)} accent="pink" icon="99" />
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
            {entries.map(([model, stats]) => {
              const ttfbP95 = stats.ttfb_percentiles?.p95;
              const latP95 = stats.latency_percentiles?.p95;
              return (
                <tr key={model}>
                  <td className="mono">{model}</td>
                  <td>
                    <span className={`neo-badge ${latencyBadgeClass(stats.avg_ttfb_ms)}`}>
                      {formatMs(stats.avg_ttfb_ms)}
                    </span>
                  </td>
                  <td>
                    <span className={`neo-badge ${badgeFor(ttfbP95)}`}>
                      {fmtP(ttfbP95)}
                    </span>
                  </td>
                  <td>
                    <span className={`neo-badge ${badgeFor(latP95)}`}>
                      {fmtP(latP95)}
                    </span>
                  </td>
                  <td>
                    <span className="neo-badge neo-badge--black">{stats.request_count}</span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}
