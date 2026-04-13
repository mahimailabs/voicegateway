import type { TooltipProps } from 'recharts';

export default function NeoTooltip({ active, payload, label }: TooltipProps<number, string>) {
  if (!active || !payload || payload.length === 0) return null;
  return (
    <div className="neo-tooltip">
      {label != null && <div className="neo-tooltip__label">{label}</div>}
      {payload.map((entry, i) => (
        <div key={i} className="neo-tooltip__value" style={{ color: entry.color }}>
          {entry.name}: {typeof entry.value === 'number' ? entry.value.toFixed(4) : entry.value}
        </div>
      ))}
    </div>
  );
}
