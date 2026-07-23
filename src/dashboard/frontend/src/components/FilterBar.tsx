import { useSearchParams } from 'react-router-dom';
import AgentFilter from './AgentFilter';

interface Props {
  /** When true, render the tenant typeahead. Default false (single-tenant OSS). */
  showTenant?: boolean;
  /** When true, render the agent typeahead. Default true. */
  showAgent?: boolean;
  /** Optional slot for a page-specific project filter. */
  projectSlot?: React.ReactNode;
  /** Optional slot for a page-specific time-range filter. */
  timeRangeSlot?: React.ReactNode;
  /** Optional extra controls (sort, etc.). */
  extra?: React.ReactNode;
}

/**
 * REQ-VG-TENANT-002 AC-2: shared filter strip that syncs to the URL.
 *
 * The tenant value lives in the ``tenant`` URL search param:
 *   - missing            → no tenant filter (all)
 *   - empty string       → unattributed bucket
 *   - any other string   → that tenant
 *
 * Pages read the same param to scope their data fetches. This is how
 * the per-tenant view persists across navigation (Costs / Calls / Replay
 * all see the same scope as long as the URL carries the param).
 *
 * Project + time-range filters are intentionally page-owned slots:
 * the Costs page wants today/week/month/all, Sessions has a different
 * ordering control, Metrics carries a `days` selector. Pushing them
 * into the FilterBar would force every page to share one taxonomy,
 * which the existing v0.2.0 / v0.3.0 pages don't agree on.
 */
export default function FilterBar({
  showTenant = false,
  showAgent = true,
  projectSlot,
  timeRangeSlot,
  extra,
}: Props) {
  const [searchParams, setSearchParams] = useSearchParams();
  const tenantParam = searchParams.get('tenant');
  // ``null`` = no tenant filter; ``""`` = unattributed; otherwise a tenant id.
  const tenant: string | null = tenantParam === null ? null : tenantParam;
  const agentParam = searchParams.get('agent');
  const agent: string | null = agentParam === null ? null : agentParam;

  const setParam = (key: string, next: string | null) => {
    const params = new URLSearchParams(searchParams);
    if (next === null) {
      params.delete(key);
    } else {
      params.set(key, next);
    }
    setSearchParams(params, { replace: true });
  };
  const setTenant = (next: string | null) => setParam('tenant', next);
  const setAgent = (next: string | null) => setParam('agent', next);

  return (
    <div
      className="flex-row mt-md"
      style={{
        justifyContent: 'flex-end',
        gap: '0.5rem',
        flexWrap: 'wrap',
        marginBottom: '0.75rem',
      }}
    >
      {projectSlot}
      {timeRangeSlot}
      {extra}
      {showAgent && <AgentFilter value={agent} onChange={setAgent} />}
    </div>
  );
}

/**
 * Helper hook for pages to read the current agent filter without duplicating
 * the URL-parsing logic. Same convention as the tenant filter: `null` (no
 * filter), `""` (unattributed), or an agent id.
 */
export function useAgentFilter(): string | null {
  const [searchParams] = useSearchParams();
  return searchParams.get('agent');
}

/**
 * Helper hook for pages to read the current tenant filter without
 * duplicating the URL-parsing logic. Returns the same convention as
 * FilterBar: ``null`` (no filter), ``""`` (unattributed), or a tenant id.
 */
export function useTenantFilter(): string | null {
  const [searchParams] = useSearchParams();
  const v = searchParams.get('tenant');
  return v;
}
