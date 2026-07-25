import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import PageHeader from '../components/PageHeader';
import { fetchAgents, probeAgent } from '../lib/api';
import { attemptedProbes } from '../lib/autoProbe';
import { DEMO_MODE } from '../lib/demo';
import type { AgentRow } from '../lib/types';
import {
  agentStatus,
  agentStatusBadgeClass,
  formatCost,
  formatMs,
  formatRelativeTime,
  rosterStatusBadge,
} from '../lib/ui';

type SortKey =
  | 'agent_id'
  | 'last_seen'
  | 'total_cost_usd'
  | 'request_count'
  | 'p95_latency_ms'
  | 'error_rate'
  | 'cpu_pct'
  | 'memory_pct';

const COLUMNS: { key: SortKey; label: string }[] = [
  { key: 'agent_id', label: 'Agent' },
  { key: 'last_seen', label: 'Last seen' },
  { key: 'total_cost_usd', label: 'Cost (24h)' },
  { key: 'request_count', label: 'Requests (24h)' },
  { key: 'p95_latency_ms', label: 'p95 (24h)' },
  { key: 'error_rate', label: 'Error rate (24h)' },
  { key: 'cpu_pct', label: 'Compute' },
  { key: 'memory_pct', label: 'Memory' },
];

// Live compute/memory refresh cadence. These are heartbeat reads (not billed),
// so a short interval keeps the table current without hammering anything.
const RESOURCE_POLL_MS = 5000;

/** Bar color for a usage reading: teal healthy, amber warm, red tight. */
function usageBarColor(pct: number): string {
  if (pct >= 90) return '#dc2626';
  if (pct >= 75) return '#f59e0b';
  return 'var(--vg-teal, #1F96AA)';
}

/** A compact STT/LLM/TTS split bar for the cached probe, hover shows ms + model. */
function MiniSplit({ probe }: { probe: NonNullable<AgentRow['latency_probe']> }) {
  const c = probe.components;
  const segs = [
    { label: 'STT', color: 'var(--vg-teal)', ms: c?.stt != null ? c.stt * 1000 : 0, model: probe.models?.stt ?? null },
    { label: 'LLM', color: 'var(--vg-green)', ms: c?.llm_ttft != null ? c.llm_ttft * 1000 : 0, model: probe.models?.llm ?? null },
    { label: 'TTS', color: 'var(--vg-red)', ms: c?.tts != null ? c.tts * 1000 : 0, model: probe.models?.tts ?? null },
  ].filter((s) => s.ms > 0);
  const total = segs.reduce((sum, s) => sum + s.ms, 0);
  if (total === 0) {
    // The probe ran but measured no split (an errored turn, or a remote-sink
    // agent that writes no telemetry here). Say so instead of an empty bar.
    return (
      <span className="mono" style={{ fontSize: 11, color: 'var(--vg-muted-2)' }}
            title={probe.error ?? undefined}>
        {probe.error ? 'errored' : 'not measured'}
      </span>
    );
  }
  const title = segs
    .map((s) => `${s.label} ${Math.round(s.ms)}ms${s.model ? ` (${s.model})` : ''}`)
    .join(' · ');
  return (
    <div className="flex-row gap-sm" style={{ alignItems: 'center' }} title={title}>
      <div
        style={{
          display: 'flex',
          width: 120,
          height: 12,
          borderRadius: 3,
          overflow: 'hidden',
          border: '1px solid var(--vg-hairline)',
        }}
      >
        {segs.map((s) => (
          <div key={s.label} style={{ width: `${(s.ms / total) * 100}%`, background: s.color, minWidth: 0 }} />
        ))}
      </div>
      <span className="mono" style={{ fontSize: 11 }}>{Math.round(total)}ms</span>
    </div>
  );
}

/** A percent-usage bar + label, shared by the Compute and Memory columns. */
function UsageCell({ pct, title }: { pct: number | null | undefined; title?: string }) {
  if (pct == null) {
    return (
      <span className="mono" style={{ color: 'var(--vg-muted)' }}>
        -
      </span>
    );
  }
  return (
    <div className="flex-row gap-sm" style={{ alignItems: 'center' }} title={title}>
      <span
        style={{
          display: 'inline-block',
          width: 60,
          height: 6,
          borderRadius: 999,
          background: 'rgba(31,150,170,0.15)',
          overflow: 'hidden',
          flexShrink: 0,
        }}
      >
        <span
          style={{
            display: 'block',
            width: `${Math.min(100, pct)}%`,
            height: '100%',
            borderRadius: 999,
            background: usageBarColor(pct),
          }}
        />
      </span>
      <span className="mono">{pct}%</span>
    </div>
  );
}


export default function Agents() {
  const [agents, setAgents] = useState<AgentRow[]>([]);
  const [search, setSearch] = useState('');
  const [sortKey, setSortKey] = useState<SortKey>('last_seen');
  const [sortAsc, setSortAsc] = useState(false);
  // Agents whose probe is in flight (spinner). Those already auto-run live in the
  // module-level `attemptedProbes` (shared with the Overview cards) so neither the
  // 5s poll, a remount, nor the other surface re-bills the same agent.
  const [runningProbes, setRunningProbes] = useState<Set<string>>(new Set());

  useEffect(() => {
    let cancelled = false;
    const load = () =>
      fetchAgents({ limit: 200, q: search.trim() || undefined })
        .then((d) => {
          if (!cancelled) setAgents(d.agents);
        })
        .catch(() => {
          if (!cancelled) setAgents([]);
        });
    // Debounced load on search change, then poll so compute/memory stay live.
    // The demo has no live backend, so it loads once and skips the poll.
    const debounce = setTimeout(load, 200);
    const poll = DEMO_MODE ? null : setInterval(load, RESOURCE_POLL_MS);
    return () => {
      cancelled = true;
      clearTimeout(debounce);
      if (poll) clearInterval(poll);
    };
  }, [search]);

  const runProbe = useCallback(
    (agentId: string) => {
      setRunningProbes((s) => new Set(s).add(agentId));
      probeAgent(agentId)
        .catch(() => {
          // A refusal / timeout leaves the last cache in place; a retry is fine.
        })
        .finally(() => {
          setRunningProbes((s) => {
            const n = new Set(s);
            n.delete(agentId);
            return n;
          });
          // Pull the freshly-cached result in without waiting for the next poll.
          fetchAgents({ limit: 200, q: search.trim() || undefined })
            .then((d) => setAgents(d.agents))
            .catch(() => {});
        });
    },
    [search],
  );

  // Run one probe per eligible agent that has no cached latency yet, once per
  // page load: the "run once, then cache" default. A billed call per new agent on
  // first view, then the stored graph on every later view. The shared
  // `attemptedProbes` guard means the 5s poll, a remount, or the Overview cards
  // never re-bill the same agent.
  useEffect(() => {
    for (const a of agents) {
      if (a.probe?.eligible && !a.latency_probe && !attemptedProbes.has(a.agent_id)) {
        attemptedProbes.add(a.agent_id);
        runProbe(a.agent_id);
      }
    }
  }, [agents, runProbe]);

  const sorted = useMemo(() => {
    const rows = [...agents];
    rows.sort((a, b) => {
      const av = a[sortKey];
      const bv = b[sortKey];
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      const cmp =
        typeof av === 'string' || typeof bv === 'string'
          ? String(av).localeCompare(String(bv))
          : (av as number) - (bv as number);
      return sortAsc ? cmp : -cmp;
    });
    return rows;
  }, [agents, sortKey, sortAsc]);

  const onSort = (key: SortKey) => {
    if (key === sortKey) {
      setSortAsc((v) => !v);
    } else {
      setSortKey(key);
      setSortAsc(key === 'agent_id');
    }
  };

  return (
    <div>
      <PageHeader
        title="Agents"
        subtitle={`${agents.length} agent${agents.length === 1 ? '' : 's'} in the fleet (metrics over the last 24h)`}
        accent="blue"
      />

      <div className="vg-card mb-md" style={{ padding: '14px 20px' }}>
        <div className="flex-row gap-sm">
          <span className="vg-card__label" style={{ alignSelf: 'center' }}>Search</span>
          <input
            className="neo-input"
            placeholder="Filter by agent id..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            style={{ maxWidth: '20rem' }}
          />
        </div>
      </div>

      {sorted.length === 0 ? (
        <div className="empty-state mt-md">
          No agents yet. Agents appear here once they register (via{' '}
          <span className="mono">voicegateway.register_worker()</span>) or push
          telemetry (via <span className="mono">voicegateway.attach()</span>).
        </div>
      ) : (
        <div className="vg-card" style={{ padding: 0, overflow: 'hidden' }}>
          <table className="neo-table neo-table--blue">
            <thead>
              <tr>
                <th>Status</th>
                {COLUMNS.slice(0, 1).map((c) => (
                  <th
                    key={c.key}
                    onClick={() => onSort(c.key)}
                    style={{ cursor: 'pointer', userSelect: 'none' }}
                  >
                    {c.label}
                    {sortKey === c.key ? (sortAsc ? ' ▲' : ' ▼') : ''}
                  </th>
                ))}
                <th>Stack</th>
                {COLUMNS.slice(1).map((c) => (
                  <th
                    key={c.key}
                    onClick={() => onSort(c.key)}
                    style={{ cursor: 'pointer', userSelect: 'none' }}
                  >
                    {c.label}
                    {sortKey === c.key ? (sortAsc ? ' ▲' : ' ▼') : ''}
                  </th>
                ))}
                <th title="One real, billed call measuring this agent's STT/LLM/TTS split. Run once and cached; refresh to re-run.">
                  Latency (probe)
                </th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((a) => {
                // Registered workers show their live roster status (idle/busy/
                // offline), matching Server > Fleet; telemetry-only agents fall
                // back to the telemetry-recency status (active/idle/dormant).
                // `|| null` normalizes so the label and color never diverge.
                const fleet = a.fleet_status || null;
                const status = fleet ?? agentStatus(a.last_seen);
                const badgeClass = fleet
                  ? rosterStatusBadge(fleet)
                  : agentStatusBadgeClass(agentStatus(a.last_seen));
                return (
                  <tr key={a.agent_id}>
                    <td>
                      <span className={`neo-badge ${badgeClass}`}>{status}</span>
                    </td>
                    <td className="mono">
                      <Link to={`/agents/${encodeURIComponent(a.agent_id)}`}>
                        {a.agent_name || a.agent_id}
                      </Link>
                    </td>
                    <td>
                      <div className="flex-row gap-sm" style={{ flexWrap: 'wrap' }}>
                        {(['stt', 'llm', 'tts'] as const).map((m) => {
                          const model = a.models?.[m] ?? null;
                          return (
                            <span key={m} className="neo-badge neo-badge--black"
                                  title={model ? `${m.toUpperCase()}: ${model}` : `${m.toUpperCase()}: unknown`}
                                  style={{ opacity: model ? 1 : 0.4 }}>
                              {model ? model.split('/').pop() : '-'}
                            </span>
                          );
                        })}
                      </div>
                    </td>
                    <td>{formatRelativeTime(a.last_seen)}</td>
                    <td className="mono">{formatCost(a.total_cost_usd, 4)}</td>
                    <td>
                      <span className="neo-badge neo-badge--black">{a.request_count}</span>
                    </td>
                    <td className="mono">{formatMs(a.p95_latency_ms)}</td>
                    <td className="mono">{(a.error_rate * 100).toFixed(1)}%</td>
                    <td>
                      <UsageCell
                        pct={a.cpu_pct}
                        title={
                          a.cpu_pct == null
                            ? undefined
                            : `${a.cpu_pct}% of the machine's compute in use (${(100 - a.cpu_pct).toFixed(0)}% left)`
                        }
                      />
                    </td>
                    <td>
                      <UsageCell
                        pct={a.memory_pct}
                        title={
                          a.memory_pct == null
                            ? undefined
                            : `RSS is ${a.memory_pct}% of this worker's memory ceiling`
                        }
                      />
                    </td>
                    <td>
                      {runningProbes.has(a.agent_id) ? (
                        <span className="mono" style={{ fontSize: 11, color: 'var(--vg-muted)' }}>
                          running…
                        </span>
                      ) : a.latency_probe ? (
                        <div className="flex-row gap-sm" style={{ alignItems: 'center' }}>
                          <MiniSplit probe={a.latency_probe} />
                          <button
                            type="button"
                            className="neo-btn neo-btn--sm"
                            onClick={() => runProbe(a.agent_id)}
                            title="Re-run the probe (one billed call)"
                            aria-label={`Refresh probe latency for ${a.agent_id}`}
                          >
                            ↻
                          </button>
                        </div>
                      ) : a.probe?.eligible ? (
                        <button
                          type="button"
                          className="neo-btn neo-btn--sm"
                          onClick={() => runProbe(a.agent_id)}
                          title="Run one billed call to measure the STT/LLM/TTS split"
                        >
                          Run
                        </button>
                      ) : (
                        <span className="mono" style={{ color: 'var(--vg-muted)' }}>-</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
