// Diagnostics > Report: turn one run into something you can hand to a client.
//
// Two files, both produced by the SERVER from the stored run, so the artifact an
// operator sends out and the numbers this dashboard shows cannot disagree:
//
//   - report.html — ONE self-contained file. No CDN, no stylesheet, no font, no
//     image, no script. It renders the same from `file://` on a laptop with no
//     internet, months after the run, which is the only way an attachment on a
//     ticket is still readable when somebody finally opens it.
//   - report (JSON) — the same payload, carrying `schema_version` so a parser
//     written against today's shape survives tomorrow's additions.
//
// Nothing is rendered here that the report itself will not carry, and nothing is
// judged here at all: the verdict below is the one `livekit_diag.gates` recorded
// when the run executed. UNKNOWN is shown as UNKNOWN, never as a green tick — a
// gate that could not evaluate is not a gate that passed.

import { useState } from 'react';
import { getToken } from '../../lib/api';
import { DEMO_MODE } from '../../lib/demo';
import type { DiagnosticRun } from '../../lib/types';
import { Card, Note } from './shared';

type Kind = 'html' | 'json';

/** Badge class for a stored verdict. UNKNOWN is deliberately not green. */
function verdictBadgeClass(verdict: string | null): string {
  if (verdict === 'PASS') return 'neo-badge--green';
  if (verdict === 'WARN') return 'neo-badge--warning';
  if (verdict === 'FAIL') return 'neo-badge--red';
  // UNKNOWN, and anything this build does not recognise: neutral, never green.
  return 'neo-badge--black';
}

/** Same filename the server puts in Content-Disposition, same sanitising. */
function fileName(runId: string, kind: Kind): string {
  const safe = runId.replace(/[^A-Za-z0-9_-]/g, '').slice(0, 32) || 'run';
  return `voicegateway-diagnostics-${safe}.${kind}`;
}

function reportPath(runId: string, kind: Kind): string {
  const base = `/api/diagnostics/runs/${encodeURIComponent(runId)}/report`;
  return kind === 'html' ? `${base}.html` : base;
}

/**
 * Fetch one report and save it.
 *
 * Deliberately not routed through `fetchJson`: the HTML export is not JSON, and
 * both exports are files rather than state this page renders. The bearer token
 * is attached by hand for the same reason — an `<a href>` would drop it and 401
 * the moment auth is switched on.
 */
async function downloadReport(runId: string, kind: Kind): Promise<void> {
  const token = getToken();
  const res = await fetch(reportPath(runId, kind), {
    headers: token ? { Authorization: `Bearer ${token}` } : undefined,
  });
  if (!res.ok) {
    throw new Error(`the server refused the export (HTTP ${res.status})`);
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  try {
    const link = document.createElement('a');
    link.href = url;
    link.download = fileName(runId, kind);
    document.body.appendChild(link);
    link.click();
    link.remove();
  } finally {
    URL.revokeObjectURL(url);
  }
}

export default function ReportTab({ run }: { run: DiagnosticRun }) {
  const [busy, setBusy] = useState<Kind | null>(null);
  const [error, setError] = useState<string | null>(null);

  // A run still in flight has no results yet, so its report would say "no
  // result" for every check: a truthful document, and a useless deliverable.
  const finished = run.status === 'done' || run.status === 'failed';
  const canExport = finished && !DEMO_MODE && busy === null;

  const start = (kind: Kind) => {
    setBusy(kind);
    setError(null);
    downloadReport(run.run_id, kind)
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
      .finally(() => setBusy(null));
  };

  return (
    <Card
      label="Run report (hand this to a client)"
      right={
        <span className={`neo-badge ${verdictBadgeClass(run.verdict)}`}>
          {run.verdict ?? run.status}
        </span>
      }
    >
      <Note>
        Two exports of this run, both rendered by the server from what it stored:
        a single self-contained HTML file, and the same payload as JSON.
      </Note>

      <div style={{ marginTop: 14, fontSize: 13, lineHeight: 1.6, color: 'var(--vg-ink)' }}>
        <strong>The HTML file stands on its own.</strong> Its stylesheet is inline and it
        references nothing external — no CDN, no font, no image, no script — so it opens from a
        disk with no internet and reads the same months from now. It carries when the run happened,
        which LiveKit server it was pointed at, the thresholds each number was compared against,
        every gate with its recorded status, and a closing section naming what a diagnostics run
        structurally cannot measure.
      </div>

      <div style={{ marginTop: 12, fontSize: 13, lineHeight: 1.6, color: 'var(--vg-ink)' }}>
        <strong>The JSON is the machine-readable twin.</strong> It carries a{' '}
        <span className="mono">schema_version</span> field, and within one version the payload only
        ever gains keys: an existing key never changes meaning or disappears, so a parser written
        against it keeps working. A value nobody measured is <span className="mono">null</span>{' '}
        there, never a zero.
      </div>

      <div className="flex-row flex-wrap" style={{ gap: 10, marginTop: 16, alignItems: 'center' }}>
        <button
          type="button"
          className="neo-btn neo-btn--primary"
          onClick={() => start('html')}
          disabled={!canExport}
          title={DEMO_MODE ? 'The demo has no server to render a report' : undefined}
        >
          {busy === 'html' ? 'Preparing...' : 'Download HTML report'}
        </button>
        <button
          type="button"
          className="neo-btn"
          onClick={() => start('json')}
          disabled={!canExport}
          title={DEMO_MODE ? 'The demo has no server to render a report' : undefined}
        >
          {busy === 'json' ? 'Preparing...' : 'Download JSON'}
        </button>
        <span className="mono" style={{ fontSize: 12, color: 'var(--vg-muted)' }}>
          {fileName(run.run_id, 'html')}
        </span>
      </div>

      {DEMO_MODE && (
        <div style={{ marginTop: 12 }}>
          <Note>
            The demo runs on static fixtures with no backend, and the report is rendered by the
            server from a stored run — so there is nothing here to export. Run the dashboard against
            your own deployment to produce one.
          </Note>
        </div>
      )}

      {!DEMO_MODE && !finished && (
        <div style={{ marginTop: 12 }}>
          <Note tone="warn">
            This run has not finished. A report exported now would record “no result” for every
            check, because the results are written when the run ends. Wait for it, or pick a
            completed run from the history below.
          </Note>
        </div>
      )}

      {error && (
        <div style={{ marginTop: 12 }}>
          <Note tone="bad">The report was not exported: {error}</Note>
        </div>
      )}

      <div style={{ marginTop: 14 }}>
        <Note>
          The report reproduces this run’s verdict exactly as the gates recorded it when it
          executed, including UNKNOWN — which means a gate could not be evaluated, and is not a
          pass. It is never re-judged at export time, so the file cannot disagree with the CI log
          the same run printed.
        </Note>
      </div>
    </Card>
  );
}
