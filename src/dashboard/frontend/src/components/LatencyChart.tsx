import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from 'recharts';
import NeoTooltip from './NeoTooltip';
import { ACCENT_COLORS } from '../lib/ui';
import type { LatencyResponse } from '../lib/types';

interface Props {
  data: LatencyResponse;
}

export default function LatencyChart({ data }: Props) {
  const chartData = Object.entries(data).map(([model, stats]) => ({
    name: model,
    ttfb: Math.round(stats.avg_ttfb_ms || 0),
    total: Math.round(stats.avg_latency_ms || 0),
  }));

  if (chartData.length === 0) {
    return (
      <div className="vg-card">
        <div className="vg-card__label">Latency by Model</div>
        <div className="empty-state mt-md">No latency data yet</div>
      </div>
    );
  }

  return (
    <div className="vg-card">
      <div className="vg-card__label">Latency by Model (ms)</div>
      <div style={{ width: '100%', height: 280, marginTop: 16 }}>
        <ResponsiveContainer>
          <BarChart data={chartData} margin={{ top: 8, right: 8, left: 0, bottom: 8 }}>
            <CartesianGrid strokeDasharray="0" stroke="var(--vg-hairline-2)" strokeOpacity={1} />
            <XAxis dataKey="name" tick={{ fontSize: 11, fontWeight: 600, fill: 'var(--vg-muted-2)' }} axisLine={false} tickLine={false} />
            <YAxis tick={{ fontSize: 11, fontWeight: 600, fill: 'var(--vg-muted-2)' }} axisLine={false} tickLine={false} width={48} />
            <Tooltip content={<NeoTooltip />} cursor={{ fill: 'var(--vg-teal-tint)' }} />
            <Bar dataKey="ttfb" name="First response" fill="var(--vg-teal)" radius={[4, 4, 0, 0]} />
            <Bar dataKey="total" name="Total" fill="var(--vg-teal-bright)" radius={[4, 4, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
