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
      <div className="vg-card">
        <div className="vg-card__label">{title}</div>
        <div className="empty-state mt-md">No data yet</div>
      </div>
    );
  }

  return (
    <div className="vg-card">
      <div className="vg-card__label">{title}</div>
      <div style={{ width: '100%', height: 240, marginTop: 16 }}>
        <ResponsiveContainer>
          <BarChart data={chartData} margin={{ top: 8, right: 8, left: 0, bottom: 8 }}>
            <CartesianGrid strokeDasharray="0" stroke="var(--vg-hairline-2)" strokeOpacity={1} />
            <XAxis dataKey="name" tick={{ fontSize: 11, fontWeight: 600, fill: 'var(--vg-muted-2)' }} axisLine={false} tickLine={false} />
            <YAxis tick={{ fontSize: 11, fontWeight: 600, fill: 'var(--vg-muted-2)' }} axisLine={false} tickLine={false} width={48} />
            <Tooltip content={<NeoTooltip />} cursor={{ fill: 'var(--vg-teal-tint)' }} />
            <Bar dataKey="cost" fill="var(--vg-teal)" radius={[4, 4, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
