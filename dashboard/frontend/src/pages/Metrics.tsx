import { useEffect, useState } from 'react';
import PageHeader from '../components/PageHeader';
import StalenessBanner from '../components/StalenessBanner';
import { fetchJson } from '../lib/api';
import type { MetricsAggregate } from '../lib/types';

// v0.2.0 voice-conversation metrics page (REQ-VG-METRICS-005).
//
// Layout: PageHeader + StalenessBanner at top, inline filter (project, days),
// then a 2x2 grid of the four metric components (PerMinuteCostCard,
// ResponseSpeedChart, TalkOverChart, DeadAirList) on the screen without
// scrolling on a standard laptop display. T14 ships the four card
// components; this iteration scaffolds the page with the data fetch +
// filter bar + grid slots so T14 can drop the cards in without touching
// layout.
//
// REQ-VG-METRICS-006 graceful-handling-of-older-sessions surfaces here as
// the `measured_session_count` callout below the filter bar: when a window
// includes pre-v0.2.0 sessions, the count names how many sessions
// actually contribute to each aggregate so the developer knows the
// sample size.

const DAYS_OPTIONS = [1, 7, 30, 90] as const;

export default function Metrics() {
  const [project, setProject] = useState<string>('');
  const [days, setDays] = useState<number>(7);
  const [data, setData] = useState<MetricsAggregate | null>(null);
  const [loading, setLoading] = useState<boolean>(false);

  useEffect(() => {
    const params = new URLSearchParams();
    if (project) params.set('project', project);
    params.set('days', String(days));
    setLoading(true);
    fetchJson<MetricsAggregate>(`/api/metrics?${params.toString()}`)
      .then((d) => setData(d))
      .catch(() => setData(null))
      .finally(() => setLoading(false));
  }, [project, days]);

  return (
    <div>
      <StalenessBanner />
      <PageHeader
        title="Metrics"
        subtitle="Voice-conversation cost and quality"
        accent="orange"
      />

      <div className="neo-card mb-lg">
        <div className="flex flex-row gap-md items-end">
          <div>
            <div className="label">Project</div>
            <input
              type="text"
              className="neo-input"
              placeholder="(all)"
              value={project}
              onChange={(e) => setProject(e.target.value)}
            />
          </div>
          <div>
            <div className="label">Window</div>
            <select
              className="neo-input"
              value={days}
              onChange={(e) => setDays(Number(e.target.value))}
            >
              {DAYS_OPTIONS.map((d) => (
                <option key={d} value={d}>
                  Last {d} day{d === 1 ? '' : 's'}
                </option>
              ))}
            </select>
          </div>
          {data && (
            <div className="ml-auto">
              <div className="label">Sessions</div>
              <div className="stat-value">
                {data.measured_session_count} / {data.session_count}
                <span className="label ml-sm">measured</span>
              </div>
            </div>
          )}
        </div>
      </div>

      {loading && !data && (
        <div className="empty-state">Loading metrics...</div>
      )}

      {data && (
        <div className="grid grid-cols-2 gap-lg">
          {/* T14 ships the four metric components into these slots. */}
          <div className="neo-card neo-card--strip-green">
            <div className="label">Per-minute cost</div>
            <div className="stat-value stat-value--xl mt-md">
              {data.per_minute_cost_usd_avg !== null
                ? `$${data.per_minute_cost_usd_avg.toFixed(4)}`
                : <span className="empty-state-inline">not measured</span>}
            </div>
          </div>
          <div className="neo-card neo-card--strip-blue">
            <div className="label">Response speed (p50 / p95)</div>
            <div className="stat-value stat-value--xl mt-md">
              {data.response_speed_ms.p50 !== null
                ? `${Math.round(data.response_speed_ms.p50)}ms`
                : <span className="empty-state-inline">not measured</span>}
              <span className="mono ml-sm" style={{ fontSize: 14 }}>
                /{' '}
                {data.response_speed_ms.p95 !== null
                  ? `${Math.round(data.response_speed_ms.p95)}ms`
                  : '—'}
              </span>
            </div>
          </div>
          <div className="neo-card neo-card--strip-orange">
            <div className="label">Talk-over rate</div>
            <div className="stat-value stat-value--xl mt-md">
              {data.talk_over_rate !== null
                ? `${(data.talk_over_rate * 100).toFixed(1)}%`
                : <span className="empty-state-inline">not measured</span>}
            </div>
          </div>
          <div className="neo-card neo-card--strip-red">
            <div className="label">Dead-air events</div>
            <div className="stat-value stat-value--xl mt-md">
              {data.dead_air_event_count}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
