import { Link } from 'react-router-dom';

interface Props {
  agentId: string | null;
  /** When true, the pill links to the agent-scoped Sessions view. */
  asLink?: boolean;
  /** Optional click handler instead of the link. Ignored when `asLink` is true. */
  onClick?: () => void;
}

/**
 * Phase 2 fleet: per-row agent indicator (mirror of TenantPill).
 *
 * Renders the agent id as a neo-badge, or a muted "unattributed" label when
 * the row's agent_id is NULL.
 */
export default function AgentPill({ agentId, asLink = false, onClick }: Props) {
  const isAttributed = agentId !== null && agentId !== '';
  const label = isAttributed ? agentId : 'unattributed';
  const cls = isAttributed ? 'neo-badge neo-badge--black' : 'neo-badge';
  const style = isAttributed
    ? undefined
    : { background: 'transparent', color: 'var(--text-muted, #888)', opacity: 0.75 };

  if (asLink) {
    const search = new URLSearchParams({ agent: agentId ?? '' }).toString();
    return (
      <Link className={cls} style={style} to={`/calls?${search}`}>
        {label}
      </Link>
    );
  }
  if (onClick) {
    return (
      <button
        className={cls}
        style={{ ...(style ?? {}), border: 'none', cursor: 'pointer' }}
        onClick={onClick}
      >
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
