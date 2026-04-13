import type { Accent } from '../lib/ui';

interface Props {
  label: string;
  value: string | number;
  accent: Accent;
  icon?: string;
}

export default function StatusCard({ label, value, accent, icon }: Props) {
  return (
    <div className={`neo-card neo-card--strip-${accent}`}>
      <div className="stat-label-row">
        <span className="label">{label}</span>
        {icon && <span className={`icon-square icon-square--${accent}`}>{icon}</span>}
      </div>
      <div className="stat-value">{value}</div>
    </div>
  );
}
