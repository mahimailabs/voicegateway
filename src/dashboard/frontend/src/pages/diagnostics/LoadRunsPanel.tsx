// Diagnostics > load runs: what an external generator measured, per test.
//
// This panel sits OUTSIDE the run-result tabs, next to CorrelationPanel,
// CallsPanel and NodesPanel, for the same reason those do: it needs no billed
// run and no LiveKit credential. It reads what somebody already imported.
// Anything inside a tab is invisible until a probe has been run and paid for,
// and a load run has nothing to do with a probe.
//
// Three things this file refuses to do:
//
//   * It never renders an unmeasured value as 0. A null peak concurrency means
//     no interval CSV was imported; a 0 would claim the test carried no calls.
//     Both would print "0" and only one of them would be true.
//   * It never computes a verdict. Establishment is shown against the
//     threshold it is judged by, so a reader can check the comparison rather
//     than trust a colour.
//   * It never lets a synthetic run look measured. `data_provenance` is
//     derived server-side from the artifact checksum, and a synthetic run is
//     badged before any of its numbers are read.

import { useEffect, useRef, useState } from 'react';
import { ApiError, fetchLoadRuns } from '../../lib/api';
import type { LoadRun, LoadRunTest } from '../../lib/types';
import { Card, Note, NOT_MEASURED, Row } from './shared';

const LABEL = 'Load runs';

// The establishment floor a run is read against. Printed next to the number so
// the comparison is checkable rather than asserted.
const ESTABLISHMENT_FLOOR = 0.995;

/** A count, or the words. Never a 0 for something nobody measured. */
function count(value: number | null): string {
  return value === null ? NOT_MEASURED : String(value);
}

/** A 0..1 fraction as a percentage, or the words. */
function pct(value: number | null, digits = 1): string {
  return value === null ? NOT_MEASURED : `${(value * 100).toFixed(digits)}%`;
}

/**
 * Establishment from the counts, never from a stored rate.
 *
 * Null when attempts were not measured AND null when zero calls were
 * attempted: a run that placed no calls has no establishment rate, and 0%
 * there would fail a run that never ran.
 */
function establishment(test: LoadRunTest): number | null {
  if (test.attempted_calls === null || test.succeeded_calls === null) return null;
  if (test.attempted_calls <= 0) return null;
  return test.succeeded_calls / test.attempted_calls;
}

/** Wall duration in minutes, or null when either end is missing. */
function durationMin(test: LoadRunTest): number | null {
  if (test.started_at_ms === null || test.ended_at_ms === null) return null;
  const span = test.ended_at_ms - test.started_at_ms;
  return span >= 0 ? span / 60000 : null;
}

/** Only the causes actually parsed. An absent cause is absent, not zero. */
function causes(test: LoadRunTest): string {
  const pairs: Array<[string, number | null]> = [
    ['timeout', test.failed_timeout],
    ['unexpected SIP', test.failed_unexpected_sip],
    ['transport', test.failed_transport_error],
    ['parse', test.failed_parse_error],
    ['scenario', test.failed_scenario_error],
    ['cancelled', test.failed_cancelled],
  ];
  const known = pairs.filter(([, v]) => v !== null);
  if (known.length === 0) return NOT_MEASURED;
  return known.map(([k, v]) => `${k} ${v}`).join(', ');
}

function TestRows({ test }: { test: LoadRunTest }) {
  const rate = establishment(test);
  const minutes = durationMin(test);
  return (
    <div style={{ marginTop: 12 }}>
      <div className="mono" style={{ fontSize: 13, marginBottom: 6 }}>
        {test.name}
      </div>
      <Row label="Peak concurrency" value={count(test.peak_concurrency)} />
      <Row
        label="Duration"
        value={minutes === null ? NOT_MEASURED : `${minutes.toFixed(1)} min`}
      />
      {/* The floor travels with the number rather than being encoded as a
          colour, so the comparison is checkable rather than asserted. */}
      <Row
        label="Established"
        value={
          rate === null
            ? NOT_MEASURED
            : `${pct(rate, 3)} of ${count(test.attempted_calls)} (floor ${pct(
                ESTABLISHMENT_FLOOR,
                1,
              )}, ${rate < ESTABLISHMENT_FLOOR ? 'below' : 'at or above'})`
        }
      />
      <Row label="Peak CPU" value={pct(test.peak_cpu_utilisation)} />
      <Row label="Peak memory" value={pct(test.peak_memory_utilisation)} />
      <Row label="Failed" value={count(test.failed_calls)} />
      <Row label="By cause" value={causes(test)} />
      {test.node_samples_in_window !== null && (
        <Row
          label="Node samples in window"
          value={`${test.node_samples_in_window} (overlap, not attribution)`}
        />
      )}
    </div>
  );
}

function RunCard({ run }: { run: LoadRun }) {
  const synthetic = run.data_provenance !== 'measured';
  return (
    <div style={{ marginBottom: 18 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <span className="mono" style={{ fontSize: 13 }}>
          {run.id}
        </span>
        <span
          className={`neo-badge ${synthetic ? 'neo-badge--warning' : 'neo-badge--green'}`}
        >
          {run.data_provenance}
        </span>
        {run.tool && (
          <span style={{ fontSize: 12, color: 'var(--vg-muted)' }}>
            {run.tool} {run.tool_version ?? ''}
          </span>
        )}
      </div>
      {synthetic && (
        <Note tone="warn">
          This run carries no artifact checksum, so every number below came from
          fixtures. It is not evidence of anything that happened.
        </Note>
      )}
      {run.tests.length === 0 ? (
        <Note>This run imported no test rows.</Note>
      ) : (
        run.tests.map((test) => <TestRows key={`${run.id}:${test.name}`} test={test} />)
      )}
    </div>
  );
}

export default function LoadRunsPanel() {
  const mountedRef = useRef(true);
  const [runs, setRuns] = useState<LoadRun[] | null>(null);
  const [error, setError] = useState<{ message: string; status: number | null } | null>(
    null,
  );

  useEffect(() => {
    mountedRef.current = true;
    fetchLoadRuns()
      .then((response) => {
        if (mountedRef.current) setRuns(response.runs);
      })
      .catch((err: unknown) => {
        if (!mountedRef.current) return;
        setError({
          message: err instanceof Error ? err.message : String(err),
          status: err instanceof ApiError ? err.status : null,
        });
      });
    return () => {
      mountedRef.current = false;
    };
  }, []);

  if (error) {
    // 503 is storage being off, which is a different fact from having imported
    // nothing, and the reader must not be shown the second when the first is
    // true.
    return (
      <Card label={LABEL}>
        <Note tone="warn">
          {error.status === 503
            ? 'Storage is not configured, so no load run could have been imported. This is not an empty list.'
            : error.message}
        </Note>
      </Card>
    );
  }

  if (runs === null) {
    return (
      <Card label={LABEL}>
        <Note>Loading...</Note>
      </Card>
    );
  }

  if (runs.length === 0) {
    return (
      <Card label={LABEL}>
        <Note>
          No load run has been imported. Import one with{' '}
          <span className="mono">voicegw loadtest import</span>. VoiceGateway does
          not place calls; it reads what an external generator recorded.
        </Note>
      </Card>
    );
  }

  return (
    <Card label={LABEL}>
      {runs.map((run) => (
        <RunCard key={run.id} run={run} />
      ))}
    </Card>
  );
}
