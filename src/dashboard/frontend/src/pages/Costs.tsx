import { useEffect, useState } from 'react';
import CostChart from '../components/CostChart';
import FilterBar, { useTenantFilter, useAgentFilter } from '../components/FilterBar';
import PageHeader from '../components/PageHeader';
import StatusCard from '../components/StatusCard';
import LatencyChart from '../components/LatencyChart';
import { fetchJson } from '../lib/api';
import { formatCost, latencyBadgeClass, formatMs } from '../lib/ui';
import type { CostsResponse, LatencyResponse, LatencyStats } from '../lib/types';

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

function badgeFor(v: number | null | undefined): string {
  return typeof v === 'number' ? latencyBadgeClass(v) : 'neo-badge--black';
}

function LatencyContent() {
  const [data, setData] = useState<LatencyResponse | null>(null);
  const agent = useAgentFilter();

  useEffect(() => {
    const params = new URLSearchParams();
    if (agent !== null) params.set('agent', agent);
    const qs = params.toString();
    fetchJson<LatencyResponse>(qs ? `/api/latency?${qs}` : '/api/latency')
      .then(setData)
      .catch(() => setData(null));
  }, [agent]);

  if (!data) return <div className="empty-state">Loading latency...</div>;

  const entries = Object.entries(data);
  const p50 = worstP(entries, 'p50');
  const p95 = worstP(entries, 'p95');
  const p99 = worstP(entries, 'p99');

  return (
    <div>
      <FilterBar showTenant={false} />

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

function CostsContent() {
  const [data, setData] = useState<CostsResponse | null>(null);
  const tenant = useTenantFilter();
  const agent = useAgentFilter();

  useEffect(() => {
    const params = new URLSearchParams();
    if (tenant !== null) params.set('tenant', tenant);
    if (agent !== null) params.set('agent', agent);
    const qs = params.toString();
    const url = qs ? `/api/costs?${qs}` : '/api/costs';
    fetchJson<CostsResponse>(url).then(setData).catch(() => setData(null));
  }, [tenant, agent]);

  if (!data) return <div className="empty-state">Loading costs...</div>;

  const models = Object.entries(data.by_model);

  return (
    <div>
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
                <th>Pricing source</th>
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
                  <td className="mono" style={{ fontSize: 11 }}>
                    {info.pricing_source ?? '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div
            className="label mt-md"
            style={{ fontStyle: 'italic', opacity: 0.8 }}
          >
            Costs are estimates from the pricing sources above. Run{' '}
            <span className="mono">
              voicegw reconcile --provider &lt;name&gt; --provider-usage-file &lt;file&gt;
            </span>{' '}
            to verify against your provider invoice.
          </div>
        </div>
      )}
    </div>
  );
}

export default function Costs() {
  const [tab, setTab] = useState<'Costs' | 'Latency'>('Costs');

  return (
    <div>
      <PageHeader title="Costs" subtitle="Spend and latency by provider and model" accent="green" />
      <FilterBar />

      <div className="neo-tabs">
        {(['Costs', 'Latency'] as const).map((t) => (
          <button
            key={t}
            className={`neo-tab${tab === t ? ' neo-tab--active' : ''}`}
            onClick={() => setTab(t)}
          >
            {t}
          </button>
        ))}
      </div>

      {tab === 'Costs' && <CostsContent />}
      {tab === 'Latency' && <LatencyContent />}
    </div>
  );
}
