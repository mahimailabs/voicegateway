// Shared conversation-quality metrics card grid (lifted from pages/Sessions.tsx).
// Renders the 4-card aggregate from GET /api/metrics: PerMinuteCostCard,
// ResponseSpeedChart, TalkOverChart, DeadAirList, plus a Project/Window filter.
// Used by pages/Costs.tsx (Conversation tab).

import { useEffect, useState } from 'react';
import DeadAirList from './DeadAirList';
import { useTenantFilter, useAgentFilter } from './FilterBar';
import PerMinuteCostCard from './PerMinuteCostCard';
import ResponseSpeedChart from './ResponseSpeedChart';
import TalkOverChart from './TalkOverChart';
import { fetchJson } from '../lib/api';
import type { MetricsAggregate } from '../lib/types';

// Defaults from MetricsConfig (REQ-VG-METRICS in voicegw.yaml).
const DEFAULT_TALK_OVER_OVERLAP_MS = 100;
const DEFAULT_DEAD_AIR_THRESHOLD_SECONDS = 3.0;

const DAYS_OPTIONS = [1, 7, 30, 90] as const;

export default function ConversationMetrics() {
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
      <div className="vg-card mb-lg">
        <div className="flex-row flex-wrap" style={{ gap: 20, alignItems: 'flex-end' }}>
          <div>
            <div className="vg-card__label" style={{ marginBottom: 6 }}>Project</div>
            <input
              type="text"
              className="neo-input"
              placeholder="(all)"
              value={project}
              onChange={(e) => setProject(e.target.value)}
            />
          </div>
          <div>
            <div className="vg-card__label" style={{ marginBottom: 6 }}>Window</div>
            <select
              className="neo-select"
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
            <div style={{ marginLeft: 'auto' }}>
              <div className="vg-card__label" style={{ marginBottom: 4 }}>Calls</div>
              <div className="vg-stat" style={{ fontSize: 22, letterSpacing: '-0.5px' }}>
                {data.measured_session_count} / {data.session_count}
                <span className="vg-card__label" style={{ marginLeft: 6 }}>measured</span>
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
