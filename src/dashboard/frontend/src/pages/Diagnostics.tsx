import { useEffect, useMemo, useRef, useState } from 'react';
import PageHeader from '../components/PageHeader';
import {
  createDiagnosticsRun,
  fetchDiagnosticsCreds,
  fetchDiagnosticsRun,
  fetchDiagnosticsRuns,
} from '../lib/api';
import { DEMO_MODE } from '../lib/demo';
import { formatDateTime } from '../lib/time';
import type { DiagCheckName, DiagnosticRun, DiagnosticsCreds } from '../lib/types';
import AgentsTab from './diagnostics/AgentsTab';
import CallsPanel from './diagnostics/CallsPanel';
import CorrelationPanel from './diagnostics/CorrelationPanel';
import ErrorsTab from './diagnostics/ErrorsTab';
import LatencyTab from './diagnostics/LatencyTab';
import LoadTab from './diagnostics/LoadTab';
import NodesPanel from './diagnostics/NodesPanel';
import ReportTab from './diagnostics/ReportTab';
import { Note } from './diagnostics/shared';
import SfuTab from './diagnostics/SfuTab';

const CHECK_OPTS = [
  { key: 'agents', label: 'Agents in rooms' },
  { key: 'sfu', label: 'SFU baseline' },
  { key: 'latency', label: 'Latency (real calls)' },
  { key: 'sfu_load', label: 'SFU load' },
] as const;

const MAX_POLLS = 180;

// Result tabs. Each renders exactly one check's payload, and says so when that
// check was not part of the run rather than rendering an empty card. "Report" is
// the exception and sits last: it renders no check, it exports the whole run as
// a file an operator can hand to somebody who cannot open this dashboard.
const TABS = ['Agents', 'SFU', 'Load', 'Latency', 'Errors', 'Report'] as const;
type Tab = (typeof TABS)[number];

const CHECK_LABEL: Record<DiagCheckName, string> = {
  agents: 'agents',
  sfu: 'sfu baseline',
  sfu_load: 'sfu load',
  latency: 'latency',
};

const CHECK_ORDER: DiagCheckName[] = ['agents', 'sfu', 'sfu_load', 'latency'];

function verdictBadgeClass(v: string | null): string {
  if (v === 'PASS') return 'neo-badge--green';
  if (v === 'WARN') return 'neo-badge--warning';
  if (v === 'FAIL') return 'neo-badge--red';
  return 'neo-badge--black';
}

export default function Diagnostics() {
  const mountedRef = useRef(true);
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const [creds, setCreds] = useState<DiagnosticsCreds | null>(null);
  const [run, setRun] = useState<DiagnosticRun | null>(null);
  const [runs, setRuns] = useState<DiagnosticRun[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set(['agents', 'sfu']));
  const [inFlight, setInFlight] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<Tab>('Agents');

  // Load creds status + run history on mount.
  useEffect(() => {
    fetchDiagnosticsCreds()
      .then((c) => { if (mountedRef.current) setCreds(c); })
      .catch(() => { if (mountedRef.current) setCreds({ configured: false, url: null }); });
    fetchDiagnosticsRuns()
      .then((r) => { if (mountedRef.current) setRuns(r); })
      .catch(() => { if (mountedRef.current) setRuns([]); });
  }, []);

  const configured = creds?.configured ?? false;
  const costy = selected.has('latency') || selected.has('sfu_load');
  // Demo build is read-only: never allow starting a (billed, long-polling) run.
  const canRun = configured && selected.size > 0 && !inFlight && !DEMO_MODE;

  // The run being inspected, and the history the error classes are counted over.
  // An in-flight run is not in `runs` until it finishes, so it is prepended here
  // rather than waiting: its partial results are still the newest thing measured.
  const allRuns = useMemo(() => {
    if (run && !runs.some((r) => r.run_id === run.run_id)) return [run, ...runs];
    return runs;
  }, [run, runs]);
  const current = run ?? allRuns[0] ?? null;
  const checks = current?.results?.checks;

  const toggleCheck = (key: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const startRun = async () => {
    setInFlight(true);
    setRunError(null);
    try {
      const checkList = Array.from(selected);
      const { run_id } = await createDiagnosticsRun({ checks: checkList, config: {} });

      let rec = await fetchDiagnosticsRun(run_id);
      if (mountedRef.current) setRun(rec);

      let polls = 0;
      while (rec.status === 'queued' || rec.status === 'running') {
        if (polls >= MAX_POLLS) {
          if (mountedRef.current) {
            setRunError('Run is still in progress. Check history later.');
          }
          break;
        }
        await new Promise<void>((resolve) => setTimeout(resolve, 2000));
        if (!mountedRef.current) return;
        polls++;
        rec = await fetchDiagnosticsRun(run_id);
        if (mountedRef.current) setRun(rec);
      }

      if (polls < MAX_POLLS && mountedRef.current) {
        setRuns((prev) => [rec, ...prev]);
      }
    } catch (err) {
      if (mountedRef.current) {
        setRunError(err instanceof Error ? err.message : String(err));
      }
    } finally {
      if (mountedRef.current) {
        setInFlight(false);
      }
    }
  };

  return (
    <div>
      <PageHeader
        title="Diagnostics"
        subtitle="Probe your LiveKit deployment from this machine"
        accent="blue"
      />

      {/* Recorded calls, layer 1-6. Above the run controls and outside the run
          tabs on purpose: it needs no billed run, it is the page's headline
          claim, and it is the only thing here that reads what the deployment
          already recorded rather than what a probe just went and measured. */}
      {/* How often a session joins the call it ran in. Above the calls it
          describes and outside the run tabs for the same reasons: it needs no
          billed run, and it is the one card that says whether the join behind
          every other number on this page is still resolving. That join fails
          silently, so nothing else here would show it stopped. */}
      <CorrelationPanel />

      <CallsPanel />

      {/* Layer 7: what the boxes looked like while each of those calls ran.
          Under the calls it is keyed to, outside the run tabs for the same
          reasons: it needs no billed run and reads what the scrape worker
          already recorded. It is a correlation by TIME WINDOW and never an
          attribution, which is why every card prints the pad it was widened by
          and why a window nobody scraped says so instead of showing zeros. */}
      <NodesPanel />

      {/* Connection status card */}
      <div className="vg-card" style={{ marginBottom: 16 }}>
        <div className="vg-card__label" style={{ marginBottom: 10 }}>LiveKit connection</div>
        {creds === null ? (
          <span style={{ color: 'var(--vg-muted)', fontSize: 13 }}>Checking...</span>
        ) : configured ? (
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <span className="neo-badge neo-badge--green">Connected</span>
            {creds.url && (
              <span className="mono" style={{ fontSize: 13, color: 'var(--vg-muted)' }}>
                {creds.url}
              </span>
            )}
          </div>
        ) : (
          <div>
            <span className="neo-badge neo-badge--warning">Not configured</span>
            <p style={{ marginTop: 10, fontSize: 13, color: 'var(--vg-muted)', lineHeight: 1.5 }}>
              Set <span className="mono">LIVEKIT_URL</span>,{' '}
              <span className="mono">LIVEKIT_API_KEY</span> and{' '}
              <span className="mono">LIVEKIT_API_SECRET</span> (or a{' '}
              <span className="mono">livekit:</span> block in{' '}
              <span className="mono">voicegw.yaml</span>), then reload.
            </p>
          </div>
        )}
      </div>

      {/* Run checks card */}
      <div className="vg-card" style={{ marginBottom: 16 }}>
        <div className="vg-card__label" style={{ marginBottom: 10 }}>
          Run checks{inFlight && <span className="neo-badge neo-badge--black" style={{ marginLeft: 10 }}>Running...</span>}
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {CHECK_OPTS.map(({ key, label }) => (
            <label
              key={key}
              style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', fontSize: 14 }}
            >
              <input
                type="checkbox"
                checked={selected.has(key)}
                onChange={() => toggleCheck(key)}
              />
              {label}
            </label>
          ))}
        </div>
        {costy && (
          <div
            style={{
              marginTop: 12,
              padding: '10px 14px',
              borderRadius: 6,
              border: '1.5px solid var(--vg-amber, #e6a817)',
              background: 'rgba(230, 168, 23, 0.08)',
              color: 'var(--vg-amber, #b07d00)',
              fontSize: 13,
              lineHeight: 1.5,
            }}
          >
            Latency and SFU load place real calls and open many connections, billed by your providers.
          </div>
        )}
        {runError && (
          <div
            style={{
              marginTop: 10,
              padding: '10px 14px',
              borderRadius: 6,
              border: '1.5px solid var(--vg-red, #e53e3e)',
              background: 'rgba(229, 62, 62, 0.07)',
              color: 'var(--vg-red, #c53030)',
              fontSize: 13,
            }}
          >
            {runError}
          </div>
        )}
        <div style={{ marginTop: 14, display: 'flex', alignItems: 'center', gap: 12 }}>
          <button
            type="button"
            className="neo-btn neo-btn--primary"
            onClick={startRun}
            disabled={!canRun}
            // Demo build is read-only: keep the button visible but inert.
            title={DEMO_MODE ? 'Read-only in the demo' : undefined}
          >
            {inFlight ? 'Running...' : 'Run checks'}
          </button>
          {DEMO_MODE ? (
            <span style={{ fontSize: 12, color: 'var(--vg-muted)' }}>
              Read-only in the demo.
            </span>
          ) : (
            !configured && (
              <span style={{ fontSize: 12, color: 'var(--vg-muted)' }}>
                Configure LiveKit credentials first.
              </span>
            )
          )}
        </div>
      </div>

      {/* Results: the run summary, then one tab per check payload. */}
      {current === null ? (
        <div className="vg-card" style={{ marginBottom: 16 }}>
          <div className="vg-card__label" style={{ marginBottom: 10 }}>Results</div>
          <Note>
            No diagnostics run has been recorded yet. Pick the checks above and run them: the
            results land here, one tab per check, and nothing is shown that was not measured.
          </Note>
        </div>
      ) : (
        <>
          <RunSummary run={current} />

          {current.results === null ? (
            <div className="vg-card" style={{ marginBottom: 16 }}>
              <div className="vg-card__label" style={{ marginBottom: 10 }}>Results</div>
              {current.status === 'queued' || current.status === 'running' ? (
                <Note>
                  The checks are still running. Results appear per check as the run completes; a run
                  is capped at six minutes.
                </Note>
              ) : (
                <Note tone="bad">
                  This run recorded no results: {current.error ?? 'the reason was not reported'}
                </Note>
              )}
            </div>
          ) : (
            <>
              <div className="neo-tabs">
                {TABS.map((t) => (
                  <button
                    key={t}
                    type="button"
                    className={`neo-tab${activeTab === t ? ' neo-tab--active' : ''}`}
                    onClick={() => setActiveTab(t)}
                  >
                    {t}
                  </button>
                ))}
              </div>

              {activeTab === 'Agents' && <AgentsTab check={checks?.agents} />}
              {/* The plain sfu check and the load check both measure a baseline;
                  prefer the dedicated one, fall back to the load run's. */}
              {activeTab === 'SFU' && <SfuTab check={checks?.sfu ?? checks?.sfu_load} />}
              {activeTab === 'Load' && <LoadTab check={checks?.sfu_load} />}
              {activeTab === 'Latency' && <LatencyTab check={checks?.latency} />}
              {activeTab === 'Errors' && <ErrorsTab runs={allRuns} />}
              {activeTab === 'Report' && <ReportTab run={current} />}
            </>
          )}
        </>
      )}

      {/* Run history card */}
      {runs.length > 0 && (
        <div className="vg-card">
          <div className="vg-card__label" style={{ marginBottom: 12 }}>Run history</div>
          <table className="neo-table">
            <thead>
              <tr>
                <th>Started</th>
                <th>Checks</th>
                <th>Verdict</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((r) => (
                <tr key={r.run_id}>
                  <td style={{ color: 'var(--vg-muted)', fontSize: 13 }}>
                    {r.created_at ?? '-'}
                  </td>
                  <td>{r.checks.join(', ')}</td>
                  <td>
                    {r.verdict ? (
                      <span className={`neo-badge ${verdictBadgeClass(r.verdict)}`}>
                        {r.verdict}
                      </span>
                    ) : (
                      <span style={{ color: 'var(--vg-muted)', fontSize: 13 }}>{r.status}</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

/** Header card for the run the tabs below are showing: when, what, and how it went. */
function RunSummary({ run }: { run: DiagnosticRun }) {
  const checks = run.results?.checks;
  const ran = CHECK_ORDER.filter((name) => checks?.[name] !== undefined);

  return (
    <div className="vg-card" style={{ marginBottom: 16 }}>
      <div
        className="flex-row"
        style={{ justifyContent: 'space-between', alignItems: 'center', gap: 10, marginBottom: 10 }}
      >
        <div className="vg-card__label">
          Latest run
          {(run.status === 'queued' || run.status === 'running') && ' (in progress)'}
        </div>
        <span className={`neo-badge ${verdictBadgeClass(run.verdict)}`}>
          {run.verdict ?? run.status}
        </span>
      </div>

      <div className="flex-row flex-wrap" style={{ gap: 18, fontSize: 12, color: 'var(--vg-muted)' }}>
        <span>Started {run.created_at ? formatDateTime(run.created_at) : 'at an unrecorded time'}</span>
        {run.ended_at && <span>Ended {formatDateTime(run.ended_at)}</span>}
        {/* Run ids are a uuid4 hex; 12 chars is enough to tell two runs apart
            without wrapping the line. */}
        <span className="mono">run {run.run_id.slice(0, 12)}</span>
      </div>

      <div className="flex-row flex-wrap" style={{ gap: 8, marginTop: 12 }}>
        {ran.length === 0 ? (
          <span style={{ fontSize: 13, color: 'var(--vg-muted)' }}>
            No check has reported a result for this run yet.
          </span>
        ) : (
          ran.map((name) => {
            const check = checks?.[name];
            const ok = check?.ok === true;
            return (
              <span
                key={name}
                className={`neo-badge ${ok ? 'neo-badge--green' : 'neo-badge--red'}`}
                title={ok ? 'the check completed' : (check?.error ?? 'the check failed')}
              >
                {CHECK_LABEL[name]} {ok ? 'ok' : `failed: ${check?.error ?? 'no reason reported'}`}
              </span>
            );
          })
        )}
      </div>
    </div>
  );
}
