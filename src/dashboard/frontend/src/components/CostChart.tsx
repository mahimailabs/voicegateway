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

interface Props {
  title: string;
  data: Record<string, { cost: number; requests: number }>;
}

export default function CostChart({ title, data }: Props) {
  const chartData = Object.entries(data).map(([name, info]) => ({
    name,
    cost: Number(info.cost.toFixed(6)),
    requests: info.requests,
  }));

  if (chartData.length === 0) {
    return (
      <div className="neo-card neo-card--strip-green">
        <div className="label">{title}</div>
        <div className="empty-state">No data yet</div>
      </div>
    );
  }

  return (
    <div className="neo-card neo-card--strip-green">
      <div className="label">{title}</div>
      <div style={{ width: '100%', height: 240, marginTop: 16 }}>
        <ResponsiveContainer>
          <BarChart data={chartData} margin={{ top: 8, right: 8, left: 0, bottom: 8 }}>
            <CartesianGrid strokeDasharray="4 4" stroke="#1A1A1A" strokeOpacity={0.3} />
            <XAxis dataKey="name" tick={{ fontSize: 11, fontWeight: 600 }} />
            <YAxis tick={{ fontSize: 11, fontWeight: 600 }} />
            <Tooltip content={<NeoTooltip />} cursor={{ fill: 'rgba(138, 201, 38, 0.15)' }} />
            <Bar dataKey="cost" fill={ACCENT_COLORS.green} stroke="#1A1A1A" strokeWidth={2} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
