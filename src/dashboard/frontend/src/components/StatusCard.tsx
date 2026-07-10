import type { Accent } from '../lib/ui';

interface Props {
  label: string;
  value: string | number;
  accent: Accent;
  icon?: string;
}

export default function StatusCard({ label, value, icon }: Props) {
  return (
    <div className="vg-card">
      <div className="vg-card__head">
        <div className="vg-card__label">{label}</div>
        {icon && <span className="icon-square">{icon}</span>}
      </div>
      <div className="vg-stat">{value}</div>
    </div>
  );
}
