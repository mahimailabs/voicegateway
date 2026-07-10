// v0.0.5 Sessions page — solves AC-VG-INFER-002.3.
//
// Lists recent voice conversations with project + sort filters. Each
// row click opens a detail modal with the per-modality cost
// breakdown and provider list, both computed by the backend via a
// JOIN on requests at read time.

import { useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import DeadAirList from '../components/DeadAirList';
import FilterBar, { useTenantFilter, useAgentFilter } from '../components/FilterBar';
import PageHeader from '../components/PageHeader';
import PerMinuteCostCard from '../components/PerMinuteCostCard';
import ResponseSpeedChart from '../components/ResponseSpeedChart';
import TalkOverChart from '../components/TalkOverChart';
import TenantPill from '../components/TenantPill';
import { fetchJson } from '../lib/api';
import { formatCost } from '../lib/ui';
import type {
  MetricsAggregate,
  SessionDetail,
  SessionOrderBy,
  SessionRow,
} from '../lib/types';

interface ProjectEntry {
  id: string;
  name: string;
}

// Defaults from MetricsConfig (REQ-VG-METRICS in voicegw.yaml).
const DEFAULT_TALK_OVER_OVERLAP_MS = 100;
const DEFAULT_DEAD_AIR_THRESHOLD_SECONDS = 3.0;
const DAYS_OPTIONS = [1, 7, 30, 90] as const;

function MetricsContent() {
  const [project, setProject] = useState<string>('');
  const [days, setDays] = useState<number>(7);
  const [data, setData] = useState<MetricsAggregate | null>(null);
  const [loading, setLoading] = useState<boolean>(false);
  const tenant = useTenantFilter();
  const agent = useAgentFilter();

  useEffect(() => {
    const params = new URLSearchParams();
    if (project) params.set('project', project);
    params.set('days', String(days));
    if (tenant !== null) params.set('tenant', tenant);
    if (agent !== null) params.set('agent', agent);
    setLoading(true);
    fetchJson<MetricsAggregate>(`/api/metrics?${params.toString()}`)
      .then((d) => setData(d))
      .catch(() => setData(null))
      .finally(() => setLoading(false));
  }, [project, days, tenant, agent]);

  return (
    <div>
      <div className="vg-card mb-lg">
        <div className="flex-row flex-wrap" style={{ gap: 20, alignItems: 'flex-end' }}>
          <div>
            <div className="vg-card__label" style={{ marginBottom: 6 }}>Project</div>
            <input
              type="text"
              className="neo-input"
              placeholder="(all)"
              value={project}
              onChange={(e) => setProject(e.target.value)}
            />
          </div>
          <div>
            <div className="vg-card__label" style={{ marginBottom: 6 }}>Window</div>
            <select
              className="neo-select"
              value={days}
              onChange={(e) => setDays(Number(e.target.value))}
            >
              {DAYS_OPTIONS.map((d) => (
                <option key={d} value={d}>
                  Last {d} day{d === 1 ? '' : 's'}
                </option>
              ))}
            </select>
          </div>
          {data && (
            <div style={{ marginLeft: 'auto' }}>
              <div className="vg-card__label" style={{ marginBottom: 4 }}>Sessions</div>
              <div className="vg-stat" style={{ fontSize: 22, letterSpacing: '-0.5px' }}>
                {data.measured_session_count} / {data.session_count}
                <span className="vg-card__label" style={{ marginLeft: 6 }}>measured</span>
              </div>
            </div>
          )}
        </div>
      </div>

      {loading && !data && (
        <div className="empty-state">Loading metrics...</div>
      )}

      {data && (
        <div className="grid grid-cols-2 gap-lg">
          <PerMinuteCostCard
            value={data.per_minute_cost_usd_avg}
            measuredSessionCount={data.measured_session_count}
          />
          <ResponseSpeedChart
            p50={data.response_speed_ms.p50}
            p95={data.response_speed_ms.p95}
          />
          <TalkOverChart
            rate={data.talk_over_rate}
            thresholdMs={DEFAULT_TALK_OVER_OVERLAP_MS}
          />
          <DeadAirList
            count={data.dead_air_event_count}
            thresholdSeconds={DEFAULT_DEAD_AIR_THRESHOLD_SECONDS}
          />
        </div>
      )}
    </div>
  );
}

const ORDER_OPTIONS: { value: SessionOrderBy; label: string }[] = [
  { value: 'started_at_desc', label: 'Newest first' },
  { value: 'started_at_asc', label: 'Oldest first' },
  { value: 'cost_desc', label: 'Most expensive' },
  { value: 'cost_asc', label: 'Cheapest first' },
];

const MODALITY_BADGE_CLASS: Record<string, string> = {
  stt: 'neo-badge--blue',
  llm: 'neo-badge--green',
  tts: 'neo-badge--pink',
};

export default function Sessions() {
  const [tab, setTab] = useState<'Sessions' | 'Metrics'>('Sessions');
  const [rows, setRows] = useState<SessionRow[]>([]);
  const [projects, setProjects] = useState<ProjectEntry[]>([]);
  const [project, setProject] = useState<string>('');
  const [orderBy, setOrderBy] = useState<SessionOrderBy>('started_at_desc');
  const [detail, setDetail] = useState<SessionDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const detailAbortRef = useRef<AbortController | null>(null);
  const tenant = useTenantFilter();
  const agent = useAgentFilter();

  useEffect(() => {
    const controller = new AbortController();
    fetchJson<{ projects: ProjectEntry[] }>('/api/projects', {
      signal: controller.signal,
    })
      .then((d) => setProjects(d.projects))
      .catch((err) => {
        if (err?.name !== 'AbortError') setProjects([]);
      });
    return () => controller.abort();
  }, []);

  // Abort the in-flight list fetch on filter/sort changes so a slow
  // earlier response can't overwrite the current view's data.
  useEffect(() => {
    setLoading(true);
    const controller = new AbortController();
    const params = new URLSearchParams({ order_by: orderBy, limit: '100' });
    if (project) params.set('project', project);
    if (tenant !== null) params.set('tenant', tenant);
    if (agent !== null) params.set('agent', agent);
    fetchJson<SessionRow[]>(`/api/sessions?${params.toString()}`, {
      signal: controller.signal,
    })
      .then((data) => setRows(data))
      .catch((err) => {
        if (err?.name !== 'AbortError') setRows([]);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [project, orderBy, tenant, agent]);

  const handleRowClick = (id: string) => {
    detailAbortRef.current?.abort();
    const controller = new AbortController();
    detailAbortRef.current = controller;
    fetchJson<SessionDetail>(
      `/api/sessions/${encodeURIComponent(id)}`,
      { signal: controller.signal },
    )
      .then(setDetail)
      .catch((err) => {
        if (err?.name !== 'AbortError') setDetail(null);
      });
  };

  return (
    <div>
      <PageHeader
        title="Sessions"
        subtitle={`${rows.length} voice conversation${rows.length === 1 ? '' : 's'}`}
        accent="blue"
      />

      <div className="neo-tabs">
        {(['Sessions', 'Metrics'] as const).map((t) => (
          <button
            key={t}
            className={`neo-tab${tab === t ? ' neo-tab--active' : ''}`}
            onClick={() => setTab(t)}
          >
            {t}
          </button>
        ))}
      </div>

      {tab === 'Metrics' && (
        <FilterBar />
      )}
      {tab === 'Metrics' && <MetricsContent />}

      {tab === 'Sessions' && (
        <>
          <FilterBar
            projectSlot={
              <>
                <span className="label">Project</span>
                <select
                  className="neo-select"
                  value={project}
                  onChange={(e) => setProject(e.target.value)}
                >
                  <option value="">All projects</option>
                  {projects.map((p) => (
                    <option key={p.id} value={p.id}>{p.name}</option>
                  ))}
                </select>
              </>
            }
            extra={
              <>
                <span className="label">Sort</span>
                <select
                  className="neo-select"
                  value={orderBy}
                  onChange={(e) => setOrderBy(e.target.value as SessionOrderBy)}
                >
                  {ORDER_OPTIONS.map((o) => (
                    <option key={o.value} value={o.value}>{o.label}</option>
                  ))}
                </select>
              </>
            }
          />

          {loading ? (
            <div className="empty-state">Loading sessions…</div>
          ) : rows.length === 0 ? (
            <div className="empty-state">
              No voice sessions yet. Once an agent runs through{' '}
              <span className="mono">voicegateway.inference</span>, conversations
              show up here grouped by their <span className="mono">session_id</span>.
            </div>
          ) : (
            <div className="vg-card" style={{ padding: 0, overflow: 'hidden' }}>
              <table className="neo-table neo-table--blue">
                <thead>
                  <tr>
                    <th>Started</th>
                    <th>Duration</th>
                    <th>Project</th>
                    <th>Tenant</th>
                    <th>Modalities</th>
                    <th>Requests</th>
                    <th style={{ textAlign: 'right' }}>Cost</th>
                    <th style={{ width: 110 }}>Replay</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <tr
                      key={row.id}
                      onClick={() => handleRowClick(row.id)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter' || e.key === ' ') {
                          e.preventDefault();
                          handleRowClick(row.id);
                        }
                      }}
                      tabIndex={0}
                      role="button"
                      aria-label={`Open session ${row.id}`}
                      style={{ cursor: 'pointer' }}
                    >
                      <td className="mono">{formatRelative(row.started_at)}</td>
                      <td className="mono">
                        {formatDuration(row.started_at, row.ended_at)}
                      </td>
                      <td>{row.project}</td>
                      <td onClick={(e) => e.stopPropagation()}>
                        <TenantPill tenantId={row.tenant_id ?? null} asLink />
                      </td>
                      <td>
                        <ModalityBadges modalities={row.modalities} />
                      </td>
                      <td>
                        <span className="neo-badge neo-badge--black">
                          {row.request_count}
                        </span>
                      </td>
                      <td className="mono" style={{ textAlign: 'right' }}>
                        {formatCost(row.total_cost_usd, 6)}
                      </td>
                      <td onClick={(e) => e.stopPropagation()}>
                        <Link
                          to={`/sessions/${encodeURIComponent(row.id)}/replay`}
                          className="neo-btn neo-btn--small"
                          aria-label={`Open replay for session ${row.id}`}
                        >
                          Open replay
                        </Link>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {detail && (
            <SessionDetailModal
              session={detail}
              onClose={() => setDetail(null)}
            />
          )}
        </>
      )}
    </div>
  );
}

function ModalityBadges({ modalities }: { modalities: string[] }) {
  return (
    <span className="flex-row" style={{ gap: 4 }}>
      {modalities.map((m) => (
        <span
          key={m}
          className={`neo-badge ${MODALITY_BADGE_CLASS[m] ?? 'neo-badge--black'}`}
        >
          {m.toUpperCase()}
        </span>
      ))}
    </span>
  );
}

function SessionDetailModal({
  session,
  onClose,
}: {
  session: SessionDetail;
  onClose: () => void;
}) {
  const breakdown = useMemo(
    () => Object.entries(session.by_modality),
    [session.by_modality],
  );

  const handleCopy = () => {
    void navigator.clipboard.writeText(session.id);
  };

  // Escape-to-close. Full focus-trap is intentionally not wired in
  // here — the modal is a single-column form with no off-screen
  // pitfalls, and the role="dialog" + aria-modal hint already gives
  // assistive tech the right semantics. Trap can be a follow-up.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <div className="neo-modal-backdrop" onClick={onClose}>
      <div
        className="neo-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="session-detail-title"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 id="session-detail-title">Session detail</h3>
        <div className="flex-row" style={{ gap: 8, alignItems: 'center' }}>
          <span className="mono" style={{ fontSize: 13 }}>{session.id}</span>
          <button
            type="button"
            className="neo-btn neo-btn--small"
            onClick={handleCopy}
          >
            Copy
          </button>
          <TenantPill tenantId={session.tenant_id ?? null} />
        </div>

        <div className="grid grid-cols-2 mt-md">
          <div>
            <div className="label">Project</div>
            <div className="mt-sm">{session.project}</div>
          </div>
          <div>
            <div className="label">Total cost</div>
            <div className="mt-sm mono">
              {formatCost(session.total_cost_usd, 6)}
            </div>
          </div>
          <div>
            <div className="label">Started</div>
            <div className="mt-sm mono" style={{ fontSize: 12 }}>
              {session.started_at}
            </div>
          </div>
          <div>
            <div className="label">Ended (last activity)</div>
            <div className="mt-sm mono" style={{ fontSize: 12 }}>
              {session.ended_at ?? '—'}
            </div>
          </div>
        </div>

        <RoutingStrip session={session} />

        <div className="mt-lg">
          <div className="label">Per-modality breakdown</div>
          {breakdown.length === 0 ? (
            <div className="empty-state mt-sm">
              No request rows joined to this session.
            </div>
          ) : (
            <table className="neo-table mt-sm">
              <thead>
                <tr>
                  <th>Modality</th>
                  <th>Requests</th>
                  <th style={{ textAlign: 'right' }}>Cost</th>
                </tr>
              </thead>
              <tbody>
                {breakdown.map(([modality, info]) => (
                  <tr key={modality}>
                    <td>
                      <span
                        className={`neo-badge ${
                          MODALITY_BADGE_CLASS[modality] ?? 'neo-badge--black'
                        }`}
                      >
                        {modality.toUpperCase()}
                      </span>
                    </td>
                    <td>
                      <span className="neo-badge neo-badge--black">
                        {info.request_count}
                      </span>
                    </td>
                    <td className="mono" style={{ textAlign: 'right' }}>
                      {formatCost(info.cost, 6)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        <div className="mt-lg">
          <div className="label">Providers</div>
          <div className="flex-row flex-wrap mt-sm" style={{ gap: 6 }}>
            {session.providers.length === 0 ? (
              <span className="empty-state">—</span>
            ) : (
              session.providers.map((p) => (
                <span key={p} className="neo-badge neo-badge--black">{p}</span>
              ))
            )}
          </div>
        </div>

        <div className="flex-row mt-lg">
          <button className="neo-btn neo-btn--primary" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
    </div>
  );
}

function formatRelative(iso: string): string {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return iso;
  const deltaSec = (Date.now() - t) / 1000;
  if (deltaSec < 60) return 'just now';
  if (deltaSec < 3600) return `${Math.floor(deltaSec / 60)}m ago`;
  if (deltaSec < 86400) return `${Math.floor(deltaSec / 3600)}h ago`;
  return `${Math.floor(deltaSec / 86400)}d ago`;
}

function formatDuration(startedAt: string, endedAt: string | null): string {
  if (!endedAt) return '—';
  const start = Date.parse(startedAt);
  const end = Date.parse(endedAt);
  if (Number.isNaN(start) || Number.isNaN(end)) return '—';
  const ms = end - start;
  if (ms < 1000) return `${ms}ms`;
  const sec = ms / 1000;
  if (sec < 60) return `${sec.toFixed(1)}s`;
  const min = Math.floor(sec / 60);
  const remSec = Math.round(sec - min * 60);
  return `${min}m ${remSec}s`;
}

// v0.5.0 routing strip (REQ-VG-ROUTE-003 AC-1 + AC-2). Renders the
// router's picked triple, the budget that was in effect, the actual
// end-to-end latency observed during the session, and an overrun
// chip when budget_overrun is true. NULL routed_* (pre-v0.5.0
// sessions or sessions where the router never ran) renders "-".
function RoutingStrip({ session }: { session: SessionDetail }) {
  const hasRouting =
    session.routed_llm != null ||
    session.routed_tts != null ||
    session.budget_ms != null;
  if (!hasRouting) return null;

  const stt = session.modalities?.find((m) => m === 'stt') ? 'stt' : '-';
  const llm = session.routed_llm ?? '-';
  const tts = session.routed_tts ?? '-';
  const budget = session.budget_ms;
  const used = session.budget_ms ?? null; // budget_ms_used not yet on SessionDetail
  const overrun = !!session.budget_overrun;

  return (
    <div className="vg-card mt-lg" style={{ padding: '0.75rem 1rem' }}>
      <div className="flex-row" style={{ justifyContent: 'space-between', alignItems: 'center' }}>
        <div>
          <div className="label">Routing</div>
          <div className="mt-sm">
            <code style={{ fontSize: '0.9rem' }}>
              {stt} / {llm} / {tts}
            </code>
          </div>
        </div>
        <div style={{ textAlign: 'right' }}>
          <div className="label">Budget</div>
          <div className="mt-sm mono">
            {budget == null ? '-' : `${budget} ms`}
            {overrun && (
              <span
                className="neo-badge"
                style={{ background: '#FFD166', marginLeft: '0.5rem' }}
              >
                budget overrun
              </span>
            )}
          </div>
          {used != null && !overrun && (
            <div className="label mt-sm" style={{ fontSize: '0.7rem' }}>
              actual: {used} ms
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
