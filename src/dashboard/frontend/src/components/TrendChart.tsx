import { useEffect, useMemo, useState } from 'react';
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import NeoTooltip from './NeoTooltip';
import { Skeleton } from './Skeleton';
import { fetchJson } from '../lib/api';
import { formatCost } from '../lib/ui';
import type { CostByDayPoint } from '../lib/types';

type Metric = 'cost' | 'requests';

const TEAL = '#1F96AA';
const TEAL_BRIGHT = '#14B8C4';

const DAY_FMT = new Intl.DateTimeFormat('en-US', {
  month: 'short',
  day: 'numeric',
  timeZone: 'UTC',
});

/** Gradient area chart of daily spend / requests, in the Helicone landing style.
 *
 * Self-fetching from GET /api/costs/by-day (last 7 days) so the Overview stays a
 * thin shell. Renders a teal gradient fill under a smooth line, with a Cost /
 * Requests toggle that mirrors Helicone's metric switcher.
 */
export default function TrendChart() {
  const [series, setSeries] = useState<CostByDayPoint[] | null>(null);
  const [metric, setMetric] = useState<Metric>('cost');

  useEffect(() => {
    fetchJson<CostByDayPoint[]>('/api/costs/by-day?period=week')
      .then(setSeries)
      .catch(() => setSeries([]));
  }, []);

  const data = useMemo(
    () =>
      (series ?? []).map((p) => ({
        label: DAY_FMT.format(new Date(p.day * 1000)),
        cost: Number(p.cost.toFixed(6)),
        requests: p.requests,
      })),
    [series],
  );

  return (
    <div className="neo-card neo-card--strip-blue">
      <div className="flex-row" style={{ justifyContent: 'space-between', alignItems: 'center' }}>
        <div className="label">{metric === 'cost' ? 'Spend' : 'Requests'} (last 7 days)</div>
        <div className="seg" role="tablist" aria-label="Trend metric">
          {(['cost', 'requests'] as const).map((m) => (
            <button
              key={m}
              type="button"
              role="tab"
              aria-selected={metric === m}
              className={`seg__btn${metric === m ? ' seg__btn--on' : ''}`}
              onClick={() => setMetric(m)}
            >
              {m === 'cost' ? 'Cost' : 'Requests'}
            </button>
          ))}
        </div>
      </div>

      <div style={{ width: '100%', height: 240, marginTop: 16 }}>
        {series === null ? (
          <Skeleton height={224} />
        ) : data.length === 0 ? (
          <div className="empty-state">No activity in the last 7 days</div>
        ) : (
          <ResponsiveContainer>
            <AreaChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 8 }}>
              <defs>
                <linearGradient id="trend-fill" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={TEAL_BRIGHT} stopOpacity={0.35} />
                  <stop offset="100%" stopColor={TEAL} stopOpacity={0.02} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="4 4" stroke="var(--border)" strokeOpacity={0.3} />
              <XAxis dataKey="label" tick={{ fontSize: 11, fontWeight: 600 }} />
              <YAxis
                tick={{ fontSize: 11, fontWeight: 600 }}
                width={metric === 'cost' ? 56 : 40}
                tickFormatter={(v) =>
                  metric === 'cost' ? formatCost(v as number, 2) : `${v}`
                }
              />
              <Tooltip content={<NeoTooltip />} cursor={{ stroke: TEAL, strokeWidth: 1 }} />
              <Area
                type="monotone"
                dataKey={metric}
                name={metric === 'cost' ? 'Cost' : 'Requests'}
                stroke={TEAL}
                strokeWidth={2}
                fill="url(#trend-fill)"
                dot={false}
                activeDot={{ r: 4, fill: TEAL }}
              />
            </AreaChart>
          </ResponsiveContainer>
        )}
      </div>
    </div>
  );
}
