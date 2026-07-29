// Diagnostics > Errors: what failed, grouped by cause, across the run history.
//
// A single run's verdict says PASS or FAIL. It does not say whether the same
// thing keeps failing, which is the question an operator actually has. These bars
// group every failure the history holds by class, so "the collector times out
// every run" and "one auth error last Tuesday" stop looking the same.
//
// Two sources, both already on the wire: a failed check's own error string, and a
// latency check where an agent answered zero of its trials (measured nothing,
// which is a failure even though the check itself succeeded). Nothing here is
// inferred beyond matching those strings.

import type { DiagnosticRun } from '../../lib/types';
import { Card, Note } from './shared';

type ClassKey = 'timeout' | 'no_worker' | 'no_reply' | 'auth' | 'config' | 'connect' | 'other';

const CLASS_LABEL: Record<ClassKey, string> = {
  timeout: 'Timed out',
  no_worker: 'No worker joined',
  no_reply: 'No reply from agent',
  auth: 'Auth rejected',
  config: 'Not configured',
  connect: 'Could not connect',
  other: 'Other',
};

const CLASS_COLOR: Record<ClassKey, string> = {
  timeout: 'var(--vg-amber)',
  no_worker: 'var(--vg-red)',
  no_reply: 'var(--vg-red)',
  auth: 'var(--vg-red)',
  config: 'var(--vg-muted)',
  connect: 'var(--vg-amber)',
  other: 'var(--vg-muted-2)',
};

const ORDER: ClassKey[] = [
  'timeout',
  'no_worker',
  'no_reply',
  'auth',
  'connect',
  'config',
  'other',
];

/** Bucket one error string. Substring matching only: no cause is invented. */
function classify(message: string): ClassKey {
  const m = message.toLowerCase();
  if (m.includes('timed out') || m.includes('timeout')) return 'timeout';
  if (m.includes('no worker joined') || m.includes('worker joined')) return 'no_worker';
  if (
    m.includes('401') ||
    m.includes('403') ||
    m.includes('unauthorized') ||
    m.includes('forbidden') ||
    m.includes('invalid api key')
  ) {
    return 'auth';
  }
  if (m.includes('not configured') || m.includes('missing livekit credentials')) return 'config';
  if (
    m.includes('connect') ||
    m.includes('connection') ||
    m.includes('refused') ||
    m.includes('unreachable') ||
    m.includes('dns') ||
    m.includes('handshake') ||
    m.includes('ssl') ||
    m.includes('tls')
  ) {
    return 'connect';
  }
  return 'other';
}

interface Failure {
  runId: string;
  where: string;
  message: string;
  klass: ClassKey;
}

/** Every failure the history holds, in run order (newest run first). */
function collectFailures(runs: DiagnosticRun[]): Failure[] {
  const out: Failure[] = [];
  for (const run of runs) {
    if (run.error) {
      out.push({
        runId: run.run_id,
        where: 'run',
        message: run.error,
        klass: classify(run.error),
      });
    }
    const checks = run.results?.checks;
    if (!checks) continue;
    for (const [name, check] of Object.entries(checks)) {
      if (check === undefined) continue;
      if (!check.ok) {
        const message = check.error ?? 'the check reported no result';
        out.push({ runId: run.run_id, where: name, message, klass: classify(message) });
      }
    }
    // A latency check can succeed while measuring nothing: the call was placed
    // and no trial ever got a reply. That is a failure of the agent, not of the
    // check, and it is the single most common thing an operator needs to see.
    const latency = checks.latency;
    if (latency?.ok && latency.result) {
      for (const agent of latency.result.agents) {
        if (agent.stats.trials === 0) {
          out.push({
            runId: run.run_id,
            where: 'latency',
            message: `${agent.agent}: no trial produced a reply`,
            klass: 'no_reply',
          });
        }
      }
    }
  }
  return out;
}

function truncate(text: string, max = 130): string {
  return text.length <= max ? text : `${text.slice(0, max - 1)}…`;
}

export default function ErrorsTab({ runs }: { runs: DiagnosticRun[] }) {
  const failures = collectFailures(runs);
  const counts = ORDER.map((key) => ({
    key,
    count: failures.filter((f) => f.klass === key).length,
    examples: failures.filter((f) => f.klass === key).map((f) => `${f.where}: ${f.message}`),
  })).filter((c) => c.count > 0);
  const max = counts.reduce((m, c) => Math.max(m, c.count), 0);

  return (
    <Card
      label="Failures by class"
      right={
        <span className={`neo-badge ${failures.length === 0 ? 'neo-badge--green' : 'neo-badge--red'}`}>
          {failures.length} across {runs.length} {runs.length === 1 ? 'run' : 'runs'}
        </span>
      }
    >
      {counts.length === 0 ? (
        <Note tone="good">
          No failed check in the {runs.length} {runs.length === 1 ? 'run' : 'runs'} this dashboard
          is holding. Run history lives in memory and is capped at the newest 20 runs, so it starts
          empty again after a restart.
        </Note>
      ) : (
        <>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
            {counts.map((c) => (
              <div key={c.key}>
                <div
                  className="flex-row"
                  style={{ justifyContent: 'space-between', alignItems: 'baseline', gap: 10 }}
                >
                  <span style={{ fontSize: 13, fontWeight: 600 }}>{CLASS_LABEL[c.key]}</span>
                  <span className="mono" style={{ fontSize: 13, fontWeight: 700 }}>
                    {c.count}
                  </span>
                </div>
                <div
                  style={{
                    height: 10,
                    marginTop: 5,
                    borderRadius: 'var(--vg-radius-xs)',
                    background: 'var(--vg-hairline)',
                    overflow: 'hidden',
                  }}
                >
                  <div
                    style={{
                      width: `${max === 0 ? 0 : (c.count / max) * 100}%`,
                      height: '100%',
                      background: CLASS_COLOR[c.key],
                    }}
                  />
                </div>
                <div style={{ marginTop: 5 }}>
                  {c.examples.slice(0, 2).map((example, i) => (
                    <div
                      key={i}
                      className="mono"
                      style={{ fontSize: 11, color: 'var(--vg-muted)', lineHeight: 1.5 }}
                    >
                      {truncate(example)}
                    </div>
                  ))}
                  {c.examples.length > 2 && (
                    <div style={{ fontSize: 11, color: 'var(--vg-muted-2)' }}>
                      +{c.examples.length - 2} more like this
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>

          <div style={{ marginTop: 16 }}>
            <Note>
              Counted over the {runs.length} {runs.length === 1 ? 'run' : 'runs'} in this history
              (in memory, newest 20). A latency check that ran fine but got no reply from an agent
              counts here as well: it measured nothing, which no verdict on its own would tell you.
            </Note>
          </div>
        </>
      )}
    </Card>
  );
}
