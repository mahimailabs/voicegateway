import { useEffect, useRef, useState } from 'react';
import PageHeader from '../components/PageHeader';
import {
  createDiagnosticsRun,
  fetchDiagnosticsCreds,
  fetchDiagnosticsRun,
  fetchDiagnosticsRuns,
} from '../lib/api';
import type { DiagnosticRun, DiagnosticsCreds } from '../lib/types';

const CHECK_OPTS = [
  { key: 'agents', label: 'Agents in rooms' },
  { key: 'sfu', label: 'SFU baseline' },
  { key: 'latency', label: 'Latency (real calls)' },
  { key: 'sfu_load', label: 'SFU load' },
] as const;

const MAX_POLLS = 180;

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
  const canRun = configured && selected.size > 0 && !inFlight;

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
      const checks = Array.from(selected);
      const { run_id } = await createDiagnosticsRun({ checks, config: {} });

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
          >
            {inFlight ? 'Running...' : 'Run checks'}
          </button>
          {!configured && (
            <span style={{ fontSize: 12, color: 'var(--vg-muted)' }}>
              Configure LiveKit credentials first.
            </span>
          )}
        </div>
      </div>

      {/* Latest run card */}
      {run && (
        <div className="vg-card" style={{ marginBottom: 16 }}>
          <div
            style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}
          >
            <span className="vg-card__label">
              Latest run
              {(run.status === 'queued' || run.status === 'running') && ' (in progress)'}
            </span>
            {run.verdict && (
              <span className={`neo-badge ${verdictBadgeClass(run.verdict)}`}>
                {run.verdict}
              </span>
            )}
          </div>
          {run.results ? (
            <table className="neo-table neo-table--blue">
              <thead>
                <tr>
                  <th>Check</th>
                  <th>Result</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(run.results.checks).map(([name, result]) => (
                  <tr key={name}>
                    <td style={{ fontWeight: 500 }}>{name}</td>
                    <td>
                      {result.ok ? (
                        <span className="neo-badge neo-badge--green">ok</span>
                      ) : (
                        <span className="neo-badge neo-badge--red">
                          {result.error ? `error: ${result.error}` : 'error'}
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : run.error ? (
            <div
              style={{
                padding: '10px 14px',
                borderRadius: 6,
                border: '1.5px solid var(--vg-red, #e53e3e)',
                background: 'rgba(229, 62, 62, 0.07)',
                color: 'var(--vg-red, #c53030)',
                fontSize: 13,
              }}
            >
              {run.error}
            </div>
          ) : (
            <div style={{ color: 'var(--vg-muted)', fontSize: 13 }}>
              Waiting for results...
            </div>
          )}
        </div>
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
