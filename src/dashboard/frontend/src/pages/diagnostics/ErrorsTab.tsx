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

import ErrorClassChart from '../../components/ErrorClassChart';
import { classifyError, type ClassifiedError } from '../../lib/errorClass';
import type { DiagnosticRun } from '../../lib/types';
import { Card, Note } from './shared';

/** A classified failure plus the run it came from. */
interface Failure extends ClassifiedError {
  runId: string;
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
        klass: classifyError(run.error),
      });
    }
    const checks = run.results?.checks;
    if (!checks) continue;
    for (const [name, check] of Object.entries(checks)) {
      if (check === undefined) continue;
      if (!check.ok) {
        const message = check.error ?? 'the check reported no result';
        out.push({ runId: run.run_id, where: name, message, klass: classifyError(message) });
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

export default function ErrorsTab({ runs }: { runs: DiagnosticRun[] }) {
  const failures = collectFailures(runs);

  return (
    <Card
      label="Failures by class"
      right={
        <span className={`neo-badge ${failures.length === 0 ? 'neo-badge--green' : 'neo-badge--red'}`}>
          {failures.length} across {runs.length} {runs.length === 1 ? 'run' : 'runs'}
        </span>
      }
    >
      {failures.length === 0 ? (
        <Note tone="good">
          No failed check in the {runs.length} {runs.length === 1 ? 'run' : 'runs'} this dashboard
          is holding. Run history lives in memory and is capped at the newest 20 runs, so it starts
          empty again after a restart.
        </Note>
      ) : (
        <>
          <ErrorClassChart errors={failures} />

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
