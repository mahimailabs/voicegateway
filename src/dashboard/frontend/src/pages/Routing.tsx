import { useEffect, useMemo, useState } from 'react';
import FilterBar from '../components/FilterBar';
import PageHeader from '../components/PageHeader';
import { fetchJson } from '../lib/api';

interface LatencyObservation {
  project_id: string;
  provider: string;
  modality: string;
  p50_ms: number | null;
  p95_ms: number | null;
  sample_count: number;
  window_start: string;
  window_end: string;
  refreshed_at: string;
}

interface ObservationsResponse {
  observations: LatencyObservation[];
  filter: { project: string | null };
}

type SortColumn = 'provider' | 'modality' | 'p50_ms' | 'p95_ms' | 'sample_count';
type SortDir = 'asc' | 'desc';

const MODALITY_ORDER: Record<string, number> = { stt: 0, llm: 1, tts: 2 };
const MODALITY_BADGE: Record<string, string> = {
  stt: 'neo-badge--blue',
  llm: 'neo-badge--green',
  tts: 'neo-badge--pink',
};

const REFRESH_INTERVAL_MS = 60 * 60 * 1000; // OQ2 lock: at least hourly.

function formatRelative(iso: string | null): string {
  if (!iso) return '-';
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return '-';
  const secs = Math.floor((Date.now() - then) / 1000);
  if (secs < 60) return `${secs}s ago`;
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`;
  return `${Math.floor(secs / 86400)}d ago`;
}

function projectFromUrl(): string | undefined {
  const params = new URLSearchParams(window.location.search);
  return params.get('project') || undefined;
}

export default function Routing() {
  const [data, setData] = useState<ObservationsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [sortCol, setSortCol] = useState<SortColumn>('modality');
  const [sortDir, setSortDir] = useState<SortDir>('asc');

  const refresh = () => {
    setLoading(true);
    const project = projectFromUrl();
    const url = project
      ? `/api/routing/observations?project=${encodeURIComponent(project)}`
      : '/api/routing/observations';
    fetchJson<ObservationsResponse>(url)
      .then(setData)
      .catch(() => setData(null))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    refresh();
    const handle = window.setInterval(refresh, REFRESH_INTERVAL_MS);
    return () => window.clearInterval(handle);
  }, []);

  const rows = useMemo(() => {
    if (!data) return [];
    const sorted = [...data.observations];
    sorted.sort((a, b) => {
      let cmp = 0;
      if (sortCol === 'modality') {
        cmp = (MODALITY_ORDER[a.modality] ?? 99) - (MODALITY_ORDER[b.modality] ?? 99);
        if (cmp === 0) cmp = a.provider.localeCompare(b.provider);
      } else if (sortCol === 'provider') {
        cmp = a.provider.localeCompare(b.provider);
      } else {
        const av = a[sortCol];
        const bv = b[sortCol];
        if (av === null && bv === null) cmp = 0;
        else if (av === null) cmp = 1;
        else if (bv === null) cmp = -1;
        else cmp = av - bv;
      }
      return sortDir === 'asc' ? cmp : -cmp;
    });
    return sorted;
  }, [data, sortCol, sortDir]);

  const toggleSort = (col: SortColumn) => {
    if (sortCol === col) setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    else {
      setSortCol(col);
      setSortDir('asc');
    }
  };

  const sortIndicator = (col: SortColumn) =>
    sortCol === col ? (sortDir === 'asc' ? ' ▲' : ' ▼') : '';

  const projectScope = data?.filter.project;
  const refreshedAt = rows.length > 0 ? rows[0].refreshed_at : null;

  return (
    <div>
      <PageHeader
        title="Routing"
        subtitle={
          projectScope
            ? `Per-provider latency for ${projectScope}`
            : 'Per-provider latency across projects'
        }
        accent="green"
        actions={
          <button className="neo-btn" onClick={refresh} disabled={loading}>
            {loading ? '...' : 'Refresh'}
          </button>
        }
      />

      <FilterBar showTenant={false} />

      <div className="neo-card mt-md">
        {refreshedAt && (
          <div className="label" style={{ marginBottom: '0.5rem' }}>
            Last refreshed {formatRelative(refreshedAt)}. Auto-refresh every hour.
          </div>
        )}

        {loading && !data && <div className="empty-state">Loading observations...</div>}

        {!loading && rows.length === 0 && (
          <div className="empty-state">
            No latency observations yet. The roll-up worker refreshes every 15
            minutes; once a few sessions complete the table will populate.
          </div>
        )}

        {rows.length > 0 && (
          <table className="neo-table neo-table--green">
            <thead>
              <tr>
                <th onClick={() => toggleSort('modality')} style={{ cursor: 'pointer' }}>
                  Modality{sortIndicator('modality')}
                </th>
                <th onClick={() => toggleSort('provider')} style={{ cursor: 'pointer' }}>
                  Provider{sortIndicator('provider')}
                </th>
                <th
                  onClick={() => toggleSort('p50_ms')}
                  style={{ cursor: 'pointer', textAlign: 'right' }}
                >
                  p50 (ms){sortIndicator('p50_ms')}
                </th>
                <th
                  onClick={() => toggleSort('p95_ms')}
                  style={{ cursor: 'pointer', textAlign: 'right' }}
                >
                  p95 (ms){sortIndicator('p95_ms')}
                </th>
                <th
                  onClick={() => toggleSort('sample_count')}
                  style={{ cursor: 'pointer', textAlign: 'right' }}
                >
                  Samples{sortIndicator('sample_count')}
                </th>
                <th>Project</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={`${row.project_id}-${row.provider}-${row.modality}`}>
                  <td>
                    <span
                      className={`neo-badge ${
                        MODALITY_BADGE[row.modality] ?? 'neo-badge--black'
                      }`}
                    >
                      {row.modality}
                    </span>
                  </td>
                  <td className="mono">{row.provider}</td>
                  <td className="mono" style={{ textAlign: 'right' }}>
                    {row.p50_ms === null ? (
                      <span className="label">no observations yet</span>
                    ) : (
                      row.p50_ms
                    )}
                  </td>
                  <td className="mono" style={{ textAlign: 'right' }}>
                    {row.p95_ms === null ? '-' : row.p95_ms}
                  </td>
                  <td className="mono" style={{ textAlign: 'right' }}>
                    {row.sample_count}
                  </td>
                  <td>{row.project_id}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
