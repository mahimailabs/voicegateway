import { Link } from 'react-router-dom';

interface Props {
  tenantId: string | null;
  /** When true, the pill is a link to the tenant-scoped Sessions view. */
  asLink?: boolean;
  /** Optional click handler instead of the link. Ignored when ``asLink`` is true. */
  onClick?: () => void;
}

/**
 * REQ-VG-TENANT-002 AC-3: per-session tenant indicator.
 *
 * Renders the tenant id as a black neo-badge, or the muted
 * "unattributed" label when the session's tenant_id is NULL. The
 * empty-string tenant query convention scopes the dashboard to the
 * unattributed bucket.
 */
export default function TenantPill({ tenantId, asLink = false, onClick }: Props) {
  const isAttributed = tenantId !== null && tenantId !== '';
  const label = isAttributed ? tenantId : 'unattributed';
  const cls = isAttributed ? 'neo-badge neo-badge--black' : 'neo-badge';
  const style = isAttributed
    ? undefined
    : { background: 'transparent', color: 'var(--text-muted, #888)', opacity: 0.75 };

  if (asLink) {
    const search = new URLSearchParams({ tenant: tenantId ?? '' }).toString();
    return (
      <Link className={cls} style={style} to={`/sessions?${search}`}>
        {label}
      </Link>
    );
  }
  if (onClick) {
    return (
      <button className={cls} style={{ ...(style ?? {}), border: 'none', cursor: 'pointer' }} onClick={onClick}>
        {label}
      </button>
    );
  }
  return (
    <span className={cls} style={style}>
      {label}
    </span>
  );
}
