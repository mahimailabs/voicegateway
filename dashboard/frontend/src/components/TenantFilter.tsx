import { useEffect, useMemo, useState } from 'react';
import { fetchTenants } from '../lib/api';
import type { TenantsResponse } from '../lib/types';

interface Props {
  /** Current tenant filter: ``null`` (no filter), ``""`` (unattributed bucket), or a tenant id. */
  value: string | null;
  /** Called with the new selection. ``null`` clears the filter; ``""`` scopes to unattributed. */
  onChange: (next: string | null) => void;
}

/**
 * REQ-VG-TENANT-002 AC-1: typeahead tenant filter.
 *
 * Fetches `/api/tenants` with a substring query that updates as the
 * operator types. The returned list (plus the implicit unattributed
 * bucket) becomes the dropdown options. Selection bubbles up via
 * ``onChange`` so the parent (typically ``FilterBar``) can sync to the
 * URL query and re-fetch the page's data.
 *
 * The fetch is debounced 200ms so a busy operator doesn't fire a
 * request per keystroke; the limit caps server cost.
 */
export default function TenantFilter({ value, onChange }: Props) {
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<TenantsResponse | null>(null);

  useEffect(() => {
    const handle = setTimeout(() => {
      fetchTenants({ limit: 50, q: query.trim() || undefined })
        .then(setData)
        .catch(() => setData(null));
    }, 200);
    return () => clearTimeout(handle);
  }, [query]);

  const display = useMemo(() => {
    if (value === null) return 'All tenants';
    if (value === '') return 'Unattributed';
    return value;
  }, [value]);

  const selectTenant = (next: string | null) => {
    onChange(next);
    setOpen(false);
    setQuery('');
  };

  return (
    <div className="tenant-filter" style={{ position: 'relative' }}>
      <button
        className="neo-btn"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
      >
        Tenant: <strong>{display}</strong>
        <span style={{ marginLeft: '0.4rem' }}>▾</span>
      </button>

      {open && (
        <div
          className="neo-card"
          style={{
            position: 'absolute',
            top: 'calc(100% + 0.5rem)',
            right: 0,
            zIndex: 10,
            minWidth: '20rem',
            padding: '0.75rem',
            maxHeight: '24rem',
            overflowY: 'auto',
          }}
        >
          <input
            className="neo-input"
            placeholder="Search tenants…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            autoFocus
          />

          <div className="mt-sm">
            <button
              className={`neo-btn ${value === null ? 'neo-btn--primary' : ''}`}
              style={{ width: '100%', justifyContent: 'flex-start' }}
              onClick={() => selectTenant(null)}
            >
              All tenants
            </button>
            <button
              className={`neo-btn mt-sm ${value === '' ? 'neo-btn--primary' : ''}`}
              style={{ width: '100%', justifyContent: 'flex-start', opacity: 0.85 }}
              onClick={() => selectTenant('')}
            >
              Unattributed
              {data && (
                <span className="label" style={{ marginLeft: 'auto', fontSize: '0.75rem' }}>
                  {data.unattributed.session_count} session(s)
                </span>
              )}
            </button>
          </div>

          {data && data.tenants.length > 0 && (
            <div className="mt-sm">
              <div
                className="label"
                style={{ fontSize: '0.7rem', textTransform: 'uppercase', marginBottom: '0.25rem' }}
              >
                Tenants
              </div>
              {data.tenants.map((t) => (
                <button
                  key={t.tenant_id}
                  className={`neo-btn ${value === t.tenant_id ? 'neo-btn--primary' : ''}`}
                  style={{ width: '100%', justifyContent: 'flex-start', marginTop: '0.25rem' }}
                  onClick={() => selectTenant(t.tenant_id)}
                >
                  <span>{t.tenant_id}</span>
                  <span className="label" style={{ marginLeft: 'auto', fontSize: '0.75rem' }}>
                    {t.session_count} · ${t.total_cost_usd.toFixed(2)}
                  </span>
                </button>
              ))}
            </div>
          )}

          {data && data.tenants.length === 0 && query.trim() && (
            <div className="empty-state mt-md">No tenants match "{query}".</div>
          )}
        </div>
      )}
    </div>
  );
}
