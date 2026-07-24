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

function egressStatusBadge(status: string): string {
  if (status === 'EGRESS_ACTIVE') return 'neo-badge--green';
  if (status === 'EGRESS_STARTING' || status === 'EGRESS_ENDING') return 'neo-badge--yellow';
  if (status === 'EGRESS_FAILED' || status === 'EGRESS_ABORTED') return 'neo-badge--red';
  return 'neo-badge--black';
}

function ingressStatusBadge(status: string): string {
  if (status === 'ENDPOINT_PUBLISHING') return 'neo-badge--green';
  if (status === 'ENDPOINT_BUFFERING') return 'neo-badge--yellow';
  if (status === 'ENDPOINT_ERROR') return 'neo-badge--red';
  return 'neo-badge--black';
}

/**
 * A LiveKit enum name rendered as its distinctive last token, lowercased:
 * "EGRESS_ACTIVE" -> "active", "SIP_TRANSPORT_TCP" -> "tcp",
 * "ENDPOINT_PUBLISHING" -> "publishing", "RTMP_INPUT" -> "rtmp". An unknown
 * numeric value (the backend fallback) passes through unchanged (e.g. "9").
 */
function enumLabel(s: string): string {
  const last = s.replace(/_INPUT$/, '').split('_').pop() ?? s;
  return last.toLowerCase();
}

/** Egress started_at is unix nanoseconds; render a local time or a dash. */
function fmtNs(ns: number): string {
  if (!ns) return '-';
  return new Date(ns / 1e6).toLocaleString();
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

  const { connection, rooms, egress, ingress, sip, fleet } = data;
  const badge = connectionBadge(connection);
  const activeAgents = rooms.rooms.reduce(
    (n, r) => n + r.agents.filter((a) => a.state === 'active').length,
    0,
  );
  const dispatchedAgents = rooms.rooms.reduce(
    (n, r) => n + r.agents.filter((a) => a.state === 'dispatched').length,
    0,
  );
  const sipTrunks = sip.inbound.length + sip.outbound.length;
  const sipEmpty = sipTrunks + sip.dispatch_rules.length === 0;
  // Count only when the section actually answered, so an unconfigured/unreachable
  // deployment shows "-" rather than a misleading 0.
  const tileCount = (ok: boolean, n: number) => (ok ? n : '-');

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
        <div className="vg-card">
          <div className="vg-card__label">Egress</div>
          <div className="vg-stat" style={{ marginTop: 6 }}>{tileCount(egress.ok, egress.items.length)}</div>
          <div style={{ marginTop: 4, fontSize: 12, color: 'var(--vg-muted)' }}>jobs</div>
        </div>
        <div className="vg-card">
          <div className="vg-card__label">Ingress</div>
          <div className="vg-stat" style={{ marginTop: 6 }}>{tileCount(ingress.ok, ingress.items.length)}</div>
          <div style={{ marginTop: 4, fontSize: 12, color: 'var(--vg-muted)' }}>endpoints</div>
        </div>
        <div className="vg-card">
          <div className="vg-card__label">SIP</div>
          <div className="vg-stat" style={{ marginTop: 6 }}>{tileCount(sip.ok, sipTrunks)}</div>
          <div style={{ marginTop: 4, fontSize: 12, color: 'var(--vg-muted)' }}>
            trunks · {sip.ok ? sip.dispatch_rules.length : '-'} rules
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

      {/* SIP / Egress / Ingress: only when LiveKit is configured (the connection
          card already explains an unconfigured deployment). */}
      {connection.configured && (
        <>
          {/* SIP */}
          <div className="vg-card" style={{ marginBottom: 16 }}>
            <div className="vg-card__label" style={{ marginBottom: 12 }}>
              SIP · telephony
              {sip.ok
                ? ` (${sip.inbound.length} inbound · ${sip.outbound.length} outbound · ${sip.dispatch_rules.length} rules)`
                : ''}
            </div>
            {!sip.ok ? (
              <div className="empty-state">{sip.error ?? 'SIP is not available on this deployment.'}</div>
            ) : sipEmpty ? (
              <div className="empty-state">No SIP trunks or dispatch rules configured.</div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
                {sip.inbound.length > 0 && (
                  <div>
                    <div className="label" style={{ marginBottom: 6 }}>Inbound trunks</div>
                    <table className="neo-table neo-table--orange">
                      <thead><tr><th>Trunk</th><th>Name</th><th>Numbers</th></tr></thead>
                      <tbody>
                        {sip.inbound.map((t) => (
                          <tr key={t.trunk_id}>
                            <td className="mono">{t.trunk_id}</td>
                            <td>{t.name || '-'}</td>
                            <td className="mono">{t.numbers.join(', ') || '-'}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
                {sip.outbound.length > 0 && (
                  <div>
                    <div className="label" style={{ marginBottom: 6 }}>Outbound trunks</div>
                    <table className="neo-table neo-table--orange">
                      <thead><tr><th>Trunk</th><th>Name</th><th>Address</th><th>Transport</th><th>Numbers</th></tr></thead>
                      <tbody>
                        {sip.outbound.map((t) => (
                          <tr key={t.trunk_id}>
                            <td className="mono">{t.trunk_id}</td>
                            <td>{t.name || '-'}</td>
                            <td className="mono">{t.address || '-'}</td>
                            <td>{enumLabel(t.transport)}</td>
                            <td className="mono">{t.numbers.join(', ') || '-'}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
                {sip.dispatch_rules.length > 0 && (
                  <div>
                    <div className="label" style={{ marginBottom: 6 }}>Dispatch rules</div>
                    <table className="neo-table neo-table--orange">
                      <thead><tr><th>Rule</th><th>Name</th><th>Trunks</th></tr></thead>
                      <tbody>
                        {sip.dispatch_rules.map((r) => (
                          <tr key={r.rule_id}>
                            <td className="mono">{r.rule_id}</td>
                            <td>{r.name || '-'}</td>
                            <td className="mono">{r.trunk_ids.join(', ') || 'any'}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Egress */}
          <div className="vg-card" style={{ marginBottom: 16 }}>
            <div className="vg-card__label" style={{ marginBottom: 12 }}>
              Egress{egress.ok ? ` (${egress.items.length})` : ''}
            </div>
            {!egress.ok ? (
              <div className="empty-state">{egress.error ?? 'Egress is not available.'}</div>
            ) : egress.items.length === 0 ? (
              <div className="empty-state">No egress jobs.</div>
            ) : (
              <table className="neo-table neo-table--pink">
                <thead><tr><th>Egress</th><th>Status</th><th>Room</th><th>Source</th><th>Started</th></tr></thead>
                <tbody>
                  {egress.items.map((e) => (
                    <tr key={e.egress_id}>
                      <td className="mono">{e.egress_id}</td>
                      <td><span className={`neo-badge ${egressStatusBadge(e.status)}`}>{enumLabel(e.status)}</span></td>
                      <td className="mono">{e.room_name || '-'}</td>
                      <td>{enumLabel(e.source_type)}</td>
                      <td style={{ color: 'var(--vg-muted)', fontSize: 13 }}>{fmtNs(e.started_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>

          {/* Ingress */}
          <div className="vg-card" style={{ marginBottom: 16 }}>
            <div className="vg-card__label" style={{ marginBottom: 12 }}>
              Ingress{ingress.ok ? ` (${ingress.items.length})` : ''}
            </div>
            {!ingress.ok ? (
              <div className="empty-state">{ingress.error ?? 'Ingress is not available.'}</div>
            ) : ingress.items.length === 0 ? (
              <div className="empty-state">No ingress endpoints.</div>
            ) : (
              <table className="neo-table neo-table--yellow">
                <thead><tr><th>Ingress</th><th>Name</th><th>Input</th><th>Room</th><th>Status</th></tr></thead>
                <tbody>
                  {ingress.items.map((i) => (
                    <tr key={i.ingress_id}>
                      <td className="mono">{i.ingress_id}</td>
                      <td>{i.name || '-'}</td>
                      <td>{enumLabel(i.input_type)}</td>
                      <td className="mono">{i.room_name || '-'}</td>
                      <td>
                        <span className={`neo-badge ${ingressStatusBadge(i.status)}`}>
                          {i.status ? enumLabel(i.status) : '-'}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </>
      )}

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
        VG shows only what it can measure: no SFU node health or load bars, since
        those need metrics VG does not collect. SIP auth and ingress stream keys are
        never read into the dashboard.
      </p>
    </div>
  );
}
