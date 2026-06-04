import { useEffect, useState } from 'react';
import DeadAirList from '../components/DeadAirList';
import FilterBar, { useTenantFilter, useAgentFilter } from '../components/FilterBar';
import PageHeader from '../components/PageHeader';
import PerMinuteCostCard from '../components/PerMinuteCostCard';
import ResponseSpeedChart from '../components/ResponseSpeedChart';
import TalkOverChart from '../components/TalkOverChart';
import { fetchJson } from '../lib/api';
import type { MetricsAggregate } from '../lib/types';

// Defaults sourced from MetricsConfig (REQ-VG-METRICS in voicegw.yaml).
// The Metrics page does not yet fetch per-project config so the tooltips
// on TalkOverChart and DeadAirList read against the Foundry-locked
// defaults. T17 may wire a /api/config/metrics endpoint if per-project
// values need to surface; for v0.2.0 the locked defaults match what the
// runtime captures.
const DEFAULT_TALK_OVER_OVERLAP_MS = 100;
const DEFAULT_DEAD_AIR_THRESHOLD_SECONDS = 3.0;

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
  const tenant = useTenantFilter();
  const agent = useAgentFilter();

  useEffect(() => {
    const params = new URLSearchParams();
    if (project) params.set('project', project);
    params.set('days', String(days));
    if (tenant !== null) params.set('tenant', tenant);
    if (agent !== null) params.set('agent', agent);
    setLoading(true);
    fetchJson<MetricsAggregate>(`/api/metrics?${params.toString()}`)
      .then((d) => setData(d))
      .catch(() => setData(null))
      .finally(() => setLoading(false));
  }, [project, days, tenant, agent]);

  return (
    <div>
      <PageHeader
        title="Metrics"
        subtitle="Voice-conversation cost and quality"
        accent="orange"
      />

      <FilterBar />

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
          <PerMinuteCostCard
            value={data.per_minute_cost_usd_avg}
            measuredSessionCount={data.measured_session_count}
          />
          <ResponseSpeedChart
            p50={data.response_speed_ms.p50}
            p95={data.response_speed_ms.p95}
          />
          <TalkOverChart
            rate={data.talk_over_rate}
            thresholdMs={DEFAULT_TALK_OVER_OVERLAP_MS}
          />
          <DeadAirList
            count={data.dead_air_event_count}
            thresholdSeconds={DEFAULT_DEAD_AIR_THRESHOLD_SECONDS}
          />
        </div>
      )}
    </div>
  );
}
