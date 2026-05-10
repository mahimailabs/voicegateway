// v0.0.5 Sessions page — solves AC-VG-INFER-002.3.
//
// Lists recent voice conversations with project + sort filters. Each
// row click opens a detail modal with the per-modality cost
// breakdown and provider list, both computed by the backend via a
// JOIN on requests at read time.

import { useEffect, useMemo, useRef, useState } from 'react';
import PageHeader from '../components/PageHeader';
import StalenessBanner from '../components/StalenessBanner';
import { fetchJson } from '../lib/api';
import { formatCost } from '../lib/ui';
import type {
  SessionDetail,
  SessionOrderBy,
  SessionRow,
} from '../lib/types';

interface ProjectEntry {
  id: string;
  name: string;
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
  const [rows, setRows] = useState<SessionRow[]>([]);
  const [projects, setProjects] = useState<ProjectEntry[]>([]);
  const [project, setProject] = useState<string>('');
  const [orderBy, setOrderBy] = useState<SessionOrderBy>('started_at_desc');
  const [detail, setDetail] = useState<SessionDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const detailAbortRef = useRef<AbortController | null>(null);

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
  }, [project, orderBy]);

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
      <StalenessBanner />
      <PageHeader
        title="Sessions"
        subtitle={`${rows.length} voice conversation${rows.length === 1 ? '' : 's'}`}
        accent="blue"
      />

      <div className="filter-bar">
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
      </div>

      {loading ? (
        <div className="empty-state">Loading sessions…</div>
      ) : rows.length === 0 ? (
        <div className="empty-state">
          No voice sessions yet. Once an agent runs through{' '}
          <span className="mono">voicegateway.inference</span>, conversations
          show up here grouped by their <span className="mono">session_id</span>.
        </div>
      ) : (
        <table className="neo-table neo-table--blue">
          <thead>
            <tr>
              <th>Started</th>
              <th>Duration</th>
              <th>Project</th>
              <th>Modalities</th>
              <th>Requests</th>
              <th style={{ textAlign: 'right' }}>Cost</th>
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
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {detail && (
        <SessionDetailModal
          session={detail}
          onClose={() => setDetail(null)}
        />
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
