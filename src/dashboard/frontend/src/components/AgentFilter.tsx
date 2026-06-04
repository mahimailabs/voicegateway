import { useEffect, useMemo, useState } from 'react';
import { fetchAgents } from '../lib/api';
import type { AgentsResponse } from '../lib/types';

interface Props {
  /** Current agent filter: `null` (no filter), `""` (unattributed), or an agent id. */
  value: string | null;
  /** Called with the new selection. `null` clears the filter; `""` scopes to unattributed. */
  onChange: (next: string | null) => void;
}

/**
 * Phase 2 fleet: typeahead agent filter (mirror of TenantFilter).
 *
 * Fetches `/api/agents` with a substring query that updates as the operator
 * types. The returned list (plus the implicit unattributed bucket) becomes the
 * dropdown options. Selection bubbles up via `onChange` so the parent
 * (FilterBar) can sync to the `agent` URL query and re-fetch the page's data.
 */
export default function AgentFilter({ value, onChange }: Props) {
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<AgentsResponse | null>(null);

  useEffect(() => {
    const handle = setTimeout(() => {
      fetchAgents({ limit: 50, q: query.trim() || undefined })
        .then(setData)
        .catch(() => setData(null));
    }, 200);
    return () => clearTimeout(handle);
  }, [query]);

  const display = useMemo(() => {
    if (value === null) return 'All agents';
    if (value === '') return 'Unattributed';
    return value;
  }, [value]);

  const selectAgent = (next: string | null) => {
    onChange(next);
    setOpen(false);
    setQuery('');
  };

  return (
    <div className="agent-filter" style={{ position: 'relative' }}>
      <button className="neo-btn" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        Agent: <strong>{display}</strong>
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
            placeholder="Search agents…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            autoFocus
          />

          <div className="mt-sm">
            <button
              className={`neo-btn ${value === null ? 'neo-btn--primary' : ''}`}
              style={{ width: '100%', justifyContent: 'flex-start' }}
              onClick={() => selectAgent(null)}
            >
              All agents
            </button>
            <button
              className={`neo-btn mt-sm ${value === '' ? 'neo-btn--primary' : ''}`}
              style={{ width: '100%', justifyContent: 'flex-start', opacity: 0.85 }}
              onClick={() => selectAgent('')}
            >
              Unattributed
              {data && (
                <span className="label" style={{ marginLeft: 'auto', fontSize: '0.75rem' }}>
                  {data.unattributed.request_count} req
                </span>
              )}
            </button>
          </div>

          {data && data.agents.length > 0 && (
            <div className="mt-sm">
              <div
                className="label"
                style={{ fontSize: '0.7rem', textTransform: 'uppercase', marginBottom: '0.25rem' }}
              >
                Agents
              </div>
              {data.agents.map((a) => (
                <button
                  key={a.agent_id}
                  className={`neo-btn ${value === a.agent_id ? 'neo-btn--primary' : ''}`}
                  style={{ width: '100%', justifyContent: 'flex-start', marginTop: '0.25rem' }}
                  onClick={() => selectAgent(a.agent_id)}
                >
                  <span>{a.agent_id}</span>
                  <span className="label" style={{ marginLeft: 'auto', fontSize: '0.75rem' }}>
                    {a.request_count} · ${a.total_cost_usd.toFixed(2)}
                  </span>
                </button>
              ))}
            </div>
          )}

          {data && data.agents.length === 0 && query.trim() && (
            <div className="empty-state mt-md">No agents match "{query}".</div>
          )}
        </div>
      )}
    </div>
  );
}
