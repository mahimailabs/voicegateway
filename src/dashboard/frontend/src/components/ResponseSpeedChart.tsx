// ResponseSpeedChart: p50 / p95 caller-to-agent response speed (REQ-VG-METRICS-002).
//
// v0.2.0 ships averaged p50 + p95 across the filter window as a small
// horizontal bar pair. The Foundry called for "histogram with p50, p95,
// p99 markers" but the /api/metrics endpoint returns averaged percentiles,
// not the underlying turn distribution. A full histogram needs the turns
// table joined across the window, which is a separate query that lands as
// follow-up scope; the current shape conveys the same intent (faster is
// better, p95 is the tail) without overpromising precision.

import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import NeoTooltip from './NeoTooltip';
import { ACCENT_COLORS } from '../lib/ui';

interface Props {
  p50: number | null;
  p95: number | null;
}

export default function ResponseSpeedChart({ p50, p95 }: Props) {
  const allNull = p50 === null && p95 === null;

  if (allNull) {
    return (
      <div className="neo-card neo-card--strip-blue">
        <div className="label">Response speed</div>
        <div className="empty-state mt-md">not measured</div>
      </div>
    );
  }

  const chartData = [
    { name: 'p50', ms: p50 ?? 0 },
    { name: 'p95', ms: p95 ?? 0 },
  ];

  return (
    <div className="neo-card neo-card--strip-blue">
      <div className="label">Response speed</div>
      <div className="stat-value mt-sm" style={{ fontSize: 14, opacity: 0.7 }}>
        Caller stops &rarr; agent first audible byte (ms)
      </div>
      <div style={{ width: '100%', height: 200, marginTop: 12 }}>
        <ResponsiveContainer>
          <BarChart
            data={chartData}
            layout="vertical"
            margin={{ top: 8, right: 24, left: 8, bottom: 8 }}
          >
            <CartesianGrid
              strokeDasharray="4 4"
              stroke="var(--border)"
              strokeOpacity={0.3}
            />
            <XAxis type="number" tick={{ fontSize: 11, fontWeight: 600 }} />
            <YAxis
              dataKey="name"
              type="category"
              tick={{ fontSize: 11, fontWeight: 700 }}
            />
            <Tooltip
              content={<NeoTooltip />}
              cursor={{ fill: 'rgba(53, 80, 112, 0.15)' }}
            />
            <Bar dataKey="ms" fill={ACCENT_COLORS.blue} radius={[0, 4, 4, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
