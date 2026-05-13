import type { ReactNode } from 'react';
import type { Accent } from '../lib/ui';

interface Props {
  title: string;
  subtitle?: string;
  accent: Accent;
  actions?: ReactNode;
}

export default function PageHeader({ title, subtitle, accent, actions }: Props) {
  return (
    <div className={`main__header main__header--${accent}`}>
      <div>
        <h1 className="page-title">{title}</h1>
        {subtitle && <div className="main__subtitle">{subtitle}</div>}
      </div>
      {actions && <div className="flex-row flex-wrap">{actions}</div>}
    </div>
  );
}
