import { useCallback, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import PageHeader from '../components/PageHeader';
import { fetchServerOverview } from '../lib/api';
import { formatCost, formatMs, formatRelativeTime } from '../lib/ui';
import type { ServerOverview } from '../lib/types';

/**
 * Server: a read-only, cost-annotated view of the live LiveKit deployment.
 *
 * Rooms + agents come from the LiveKit Server API; the fleet roster comes from
 * VG's own heartbeats. Each is annotated with VG's metered cost, the thing
 * LiveKit's own console cannot show. SFU health is NOT drawn here: VG can only
 * measure it with a billed probe, which lives on the Diagnostics page. SIP /
 * Egress / Ingress arrive in Phase 2.
 */

function connectionBadge(
  c: ServerOverview['connection'],
): { cls: string; text: string } {
  if (!c.configured) return { cls: 'neo-badge--warning', text: 'Not configured' };
  if (c.reachable === false) return { cls: 'neo-badge--red', text: 'Unreachable' };
  if (c.reachable === true) return { cls: 'neo-badge--green', text: 'Connected' };
  return { cls: 'neo-badge--black', text: 'Configured' };
}

function workerStatusBadge(status: string): string {
  if (status === 'busy') return 'neo-badge--green';
  if (status === 'idle') return 'neo-badge--blue';
  return 'neo-badge--offline';
}

export default function Server() {
  const mountedRef = useRef(true);
  const [data, setData] = useState<ServerOverview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(() => {
    setLoading(true);
    fetchServerOverview()
      .then((d) => {
        if (mountedRef.current) {
          setData(d);
          setError(null);
        }
      })
      .catch((e) => {
        if (mountedRef.current) setError(e instanceof Error ? e.message : String(e));
      })
      .finally(() => {
        if (mountedRef.current) setLoading(false);
      });
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    load();
    return () => {
      mountedRef.current = false;
    };
  }, [load]);

  const refresh = (
    <button className="neo-btn neo-btn--primary" onClick={load} disabled={loading}>
      {loading ? 'Refreshing...' : 'Refresh'}
    </button>
  );

  if (!data) {
    return (
      <div>
        <PageHeader
          title="Server"
          subtitle="Live LiveKit deployment, annotated with your metered cost"
          accent="blue"
          actions={refresh}
        />
        {error ? (
          <div className="neo-card neo-card--strip-orange">{error}</div>
        ) : (
          <div className="empty-state">Loading deployment...</div>
        )}
      </div>
    );
  }

  const { connection, rooms, fleet } = data;
  const badge = connectionBadge(connection);
  const activeAgents = rooms.rooms.reduce(
    (n, r) => n + r.agents.filter((a) => a.state === 'active').length,
    0,
  );
  const dispatchedAgents = rooms.rooms.reduce(
    (n, r) => n + r.agents.filter((a) => a.state === 'dispatched').length,
    0,
  );

  return (
    <div>
      <PageHeader
        title="Server"
        subtitle={`Live LiveKit deployment, annotated with your metered cost. Snapshot ${formatRelativeTime(
          data.generated_at,
        )}`}
        accent="blue"
        actions={refresh}
      />

      {/* Connection */}
      <div className="vg-card" style={{ marginBottom: 16 }}>
        <div className="vg-card__label" style={{ marginBottom: 10 }}>
          LiveKit connection
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
          <span className={`neo-badge ${badge.cls}`}>{badge.text}</span>
          {connection.url && (
            <span className="mono" style={{ fontSize: 13, color: 'var(--vg-muted)' }}>
              {connection.url}
            </span>
          )}
        </div>
        {!connection.configured && (
          <p style={{ marginTop: 10, fontSize: 13, color: 'var(--vg-muted)', lineHeight: 1.5 }}>
            Set <span className="mono">LIVEKIT_URL</span>,{' '}
            <span className="mono">LIVEKIT_API_KEY</span> and{' '}
            <span className="mono">LIVEKIT_API_SECRET</span> (or a{' '}
            <span className="mono">livekit:</span> block in{' '}
            <span className="mono">voicegw.yaml</span>), then refresh.
          </p>
        )}
        {rooms.error && connection.configured && (
          <p style={{ marginTop: 10, fontSize: 13, color: 'var(--vg-red)', lineHeight: 1.5 }}>
            {rooms.error}
          </p>
        )}
      </div>

      {/* Component grid */}
      <div className="grid grid-cols-4" style={{ marginBottom: 16 }}>
        <div className="vg-card">
          <div className="vg-card__label">Rooms</div>
          <div className="vg-stat" style={{ marginTop: 6 }}>{rooms.rooms.length}</div>
          <div style={{ marginTop: 4, fontSize: 12, color: 'var(--vg-muted)' }}>with agents</div>
        </div>
        <div className="vg-card">
          <div className="vg-card__label">Agents</div>
          <div className="vg-stat" style={{ marginTop: 6 }}>{activeAgents}</div>
          <div style={{ marginTop: 4, fontSize: 12, color: 'var(--vg-muted)' }}>
            active{dispatchedAgents ? ` + ${dispatchedAgents} dispatched` : ''}
          </div>
        </div>
        <div className="vg-card">
          <div className="vg-card__label">Workers</div>
          <div className="vg-stat" style={{ marginTop: 6 }}>{fleet.counts.total}</div>
          <div style={{ marginTop: 4, fontSize: 12, color: 'var(--vg-muted)' }}>
            {fleet.counts.idle} idle · {fleet.counts.busy} busy · {fleet.counts.offline} offline
          </div>
        </div>
        <div className="vg-card">
          <div className="vg-card__label">SFU</div>
          <div style={{ marginTop: 10 }}>
            <Link className="neo-btn neo-btn--sm" to="/diagnostics">
              Measure on Diagnostics
            </Link>
          </div>
          <div style={{ marginTop: 8, fontSize: 12, color: 'var(--vg-muted)', lineHeight: 1.4 }}>
            Health is a billed probe, not a live gauge.
          </div>
        </div>
      </div>

      {/* Rooms */}
      <div className="vg-card" style={{ marginBottom: 16 }}>
        <div className="vg-card__label" style={{ marginBottom: 12 }}>
          Rooms (cost over last 24h)
        </div>
        {rooms.rooms.length === 0 ? (
          <div className="empty-state">
            {rooms.ok
              ? 'No rooms with agents are live right now.'
              : 'LiveKit is not reachable, so no rooms can be listed.'}
          </div>
        ) : (
          <table className="neo-table neo-table--blue">
            <thead>
              <tr>
                <th>Room</th>
                <th>In call</th>
                <th>Agents</th>
                <th>Cost (24h)</th>
                <th>Requests</th>
                <th>p95</th>
              </tr>
            </thead>
            <tbody>
              {rooms.rooms.map((r) => (
                <tr key={r.name}>
                  <td className="mono">{r.name}</td>
                  <td>{r.humans}</td>
                  <td>
                    <div className="flex-row flex-wrap" style={{ gap: 4 }}>
                      {r.agents.map((a, i) => (
                        <span
                          key={`${a.agent_name}-${a.identity ?? i}`}
                          className={`neo-badge ${a.state === 'active' ? 'neo-badge--green' : 'neo-badge--yellow'}`}
                          title={a.state}
                        >
                          {a.agent_name}
                        </span>
                      ))}
                    </div>
                  </td>
                  <td className="mono">{formatCost(r.cost_usd, 4)}</td>
                  <td>{r.request_count}</td>
                  <td>{formatMs(r.p95_latency_ms)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Fleet */}
      <div className="vg-card" style={{ marginBottom: 16 }}>
        <div className="vg-card__label" style={{ marginBottom: 12 }}>
          Fleet (registered workers)
        </div>
        {fleet.workers.length === 0 ? (
          <div className="empty-state" style={{ lineHeight: 1.5 }}>
            No workers are heartbeating. LiveKit's server API does not report idle
            workers, so this roster is populated by your agents calling{' '}
            <span className="mono">register_worker</span> (set{' '}
            <span className="mono">VOICEGW_COLLECTOR_URL</span> and{' '}
            <span className="mono">VOICEGW_API_KEY</span>).
          </div>
        ) : (
          <table className="neo-table neo-table--green">
            <thead>
              <tr>
                <th>Agent</th>
                <th>Status</th>
                <th>Region</th>
                <th>Host</th>
                <th>Version</th>
                <th>Sessions</th>
                <th>Mem</th>
                <th>Last seen</th>
              </tr>
            </thead>
            <tbody>
              {fleet.workers.map((w) => (
                <tr key={w.agent_id}>
                  <td className="mono">{w.agent_name || w.agent_id}</td>
                  <td>
                    <span className={`neo-badge ${workerStatusBadge(w.status)}`}>{w.status}</span>
                  </td>
                  <td>{w.region ?? '-'}</td>
                  <td className="mono">{w.host ?? '-'}</td>
                  <td className="mono">{w.version ?? '-'}</td>
                  <td>{w.active_sessions}</td>
                  <td>{w.memory_pct != null ? `${w.memory_pct}%` : '-'}</td>
                  <td style={{ color: 'var(--vg-muted)', fontSize: 13 }}>
                    {formatRelativeTime(w.last_seen)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <p style={{ fontSize: 12, color: 'var(--vg-muted)', lineHeight: 1.5 }}>
        SIP trunks, Egress, and Ingress land in a follow-up. VG shows only what it
        can measure: no SFU node health or load bars, since those need metrics VG
        does not collect.
      </p>
    </div>
  );
}
