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
      <div className="neo-card neo-card--strip-pink">
        <div className="label">Latency by Model</div>
        <div className="empty-state">No latency data yet</div>
      </div>
    );
  }

  return (
    <div className="neo-card neo-card--strip-pink">
      <div className="label">Latency by Model (ms)</div>
      <div style={{ width: '100%', height: 280, marginTop: 16 }}>
        <ResponsiveContainer>
          <BarChart data={chartData} margin={{ top: 8, right: 8, left: 0, bottom: 8 }}>
            <CartesianGrid strokeDasharray="4 4" stroke="#1A1A1A" strokeOpacity={0.3} />
            <XAxis dataKey="name" tick={{ fontSize: 11, fontWeight: 600 }} />
            <YAxis tick={{ fontSize: 11, fontWeight: 600 }} />
            <Tooltip content={<NeoTooltip />} cursor={{ fill: 'rgba(255, 0, 110, 0.1)' }} />
            <Bar dataKey="ttfb" name="TTFB" fill={ACCENT_COLORS.pink} stroke="#1A1A1A" strokeWidth={2} />
            <Bar dataKey="total" name="Total" fill={ACCENT_COLORS.yellow} stroke="#1A1A1A" strokeWidth={2} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
