import type { ReactNode } from 'react';
import type { Accent } from '../lib/ui';

interface Props {
  title: string;
  subtitle?: string;
  accent: Accent;
  actions?: ReactNode;
}

export default function PageHeader({ title, subtitle, actions }: Props) {
  return (
    <div className="page-head">
      <div className="page-head__title">
        <h1>{title}</h1>
        {subtitle && <div style={{ fontSize: 14, color: 'var(--vg-muted)', marginTop: 2 }}>{subtitle}</div>}
      </div>
      {actions && <div className="flex-row flex-wrap" style={{ gap: 8 }}>{actions}</div>}
    </div>
  );
}
