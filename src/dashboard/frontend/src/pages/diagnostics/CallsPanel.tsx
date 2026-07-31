// Diagnostics > recorded calls: the layer 1-6 waterfall, one card per call.
//
// This panel sits OUTSIDE the run-result tabs on purpose. Every tab below it
// renders the payload of a diagnostics run, and a fresh install has no run - but
// it does have calls, written by the LiveKit webhook receiver and by agent
// self-reports without anyone pressing anything and without spending a cent.
// Putting the flagship surface behind "run the billed checks first" would hide
// it from exactly the deployment it is most useful on.
//
// Three things this file refuses to do:
//
//   * It never computes an answer latency. `calls.answer_latency_ms` is derived
//     once, in `calls_repository._derive_answer_latency`, and displayed.
//   * It never prints a p95. Decision 10.3: under 10 samples renders "max of N".
//     The backend serves no percentile on this payload, so even at 10 or more
//     samples this says so rather than computing one in the browser off a page
//     of rows that is not the population.
//   * It never polls over a socket. The dashboard has zero WebSocket by design;
//     this reads once per mount.

import { useEffect, useRef, useState } from 'react';
import CallLayerWaterfall from '../../components/CallLayerWaterfall';
import { ApiError, fetchCalls } from '../../lib/api';
import { formatDateTime } from '../../lib/time';
import type { CallDetail } from '../../lib/types';
import { Card, Note, NOT_MEASURED, Row } from './shared';

/** Percentiles need at least 10 samples; below that we show "max of N". */
const MIN_SAMPLES_FOR_PERCENTILE = 10;

/** How many recent calls the panel draws. Each one is a tall card. */
const CALL_LIMIT = 6;

/** Clocks that support millisecond rendering (`SELF_REPORT_ORIGINS`). */
const MS_PRECISION_SOURCES = new Set(['sipp_rtd', 'agent_report']);

/**
 * A stored ring time, printed only as precisely as its source supports. A
 * `webhook_proxy` number came off a whole-second `created_at` and rendering it
 * to the millisecond would be a lie with three decimal places.
 */
function formatRing(ms: number, source: string | null): string {
  return source != null && MS_PRECISION_SOURCES.has(source)
    ? `${Math.round(ms)} ms`
    : `${(ms / 1000).toFixed(1)} s`;
}

/** Label for a call card: the most specific identifier the row actually has. */
function callLabel(call: CallDetail): string {
  if (call.room_name) return call.room_name;
  if (call.room_sid) return call.room_sid;
  // A 503 on INVITE never creates a room, so neither identifier exists and the
  // load attempt id is all there is. That row is the most important one in a
  // load test, so it must still be nameable.
  if (call.attempt_id) return `attempt ${call.attempt_id}`;
  return `call ${call.id.slice(0, 12)}`;
}

export default function CallsPanel() {
  const mountedRef = useRef(true);
  const [calls, setCalls] = useState<CallDetail[] | null>(null);
  const [error, setError] = useState<{ message: string; status: number | null } | null>(null);

  useEffect(() => {
    mountedRef.current = true;
    fetchCalls({ limit: CALL_LIMIT })
      .then((res) => {
        if (mountedRef.current) setCalls(res.calls);
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

  if (error !== null) {
    return (
      <Card label="Answer latency by layer">
        <Note tone="bad">
          {error.status === 404
            ? 'This dashboard build does not serve recorded calls: /api/calls answered 404. ' +
              'Calls are being recorded by the webhook receiver and by agent self-reports; ' +
              'nothing on this page reads them until that endpoint exists.'
            : `Recorded calls could not be read: ${error.message}`}
        </Note>
      </Card>
    );
  }

  if (calls === null) {
    return (
      <Card label="Answer latency by layer">
        <Note>Reading the calls this deployment has recorded...</Note>
      </Card>
    );
  }

  if (calls.length === 0) {
    return (
      <Card label="Answer latency by layer">
        <Note>
          No call has been recorded yet. A row appears the moment the LiveKit webhook receiver or an
          agent self-report observes one, including a call that runs no inference at all and a call
          that never answers. Nothing here is invented from a call that did not happen.
        </Note>
      </Card>
    );
  }

  return (
    <>
      <RingSummary calls={calls} />
      {calls.map((call) => (
        <Card
          key={call.id}
          label={`Call · ${callLabel(call)}`}
          right={
            <span className="flex-row" style={{ gap: 6 }}>
              {call.is_probe === 1 && <span className="neo-badge neo-badge--black">probe</span>}
              <span className="neo-badge neo-badge--white">{call.origin}</span>
            </span>
          }
        >
          <div style={{ marginBottom: 14 }}>
            <Row
              label="Started"
              value={
                call.started_at_ms == null ? NOT_MEASURED : formatDateTime(call.started_at_ms)
              }
              muted={call.started_at_ms == null}
            />
            <Row
              label="Duration"
              value={
                call.duration_ms == null
                  ? NOT_MEASURED
                  : `${(call.duration_ms / 1000).toFixed(1)} s`
              }
              muted={call.duration_ms == null}
            />
            <Row
              label="Ended because"
              value={call.end_reason ?? 'not reported'}
              muted={call.end_reason == null}
            />
            <Row label="Legs" value={String(call.num_legs)} />
          </div>
          <CallLayerWaterfall call={call} />
        </Card>
      ))}
    </>
  );
}

/**
 * The headline across the calls on screen. Deliberately "max of N", never p95 -
 * see the module comment. The max is printed at the precision of its own
 * source, and the mix of sources is shown, because a page whose numbers came
 * from three different clocks must not present one confident aggregate.
 */
function RingSummary({ calls }: { calls: CallDetail[] }) {
  const measured = calls.filter((c) => c.answer_latency_ms != null);
  const slowest = measured.reduce<CallDetail | null>(
    (best, c) =>
      best === null || (c.answer_latency_ms as number) > (best.answer_latency_ms as number)
        ? c
        : best,
    null,
  );

  const sourceCounts = new Map<string, number>();
  for (const c of measured) {
    const key = c.answer_latency_source ?? 'unattributed';
    sourceCounts.set(key, (sourceCounts.get(key) ?? 0) + 1);
  }

  return (
    <Card
      label="Answer latency by layer"
      right={
        <span className="neo-badge neo-badge--blue">
          {calls.length} {calls.length === 1 ? 'call' : 'calls'}
        </span>
      }
    >
      <div className="flex-row" style={{ gap: 28, flexWrap: 'wrap', alignItems: 'flex-end' }}>
        <div>
          <div
            className="mono"
            style={{
              fontSize: 30,
              fontWeight: 800,
              lineHeight: 1.1,
              color: slowest === null ? 'var(--vg-muted-2)' : 'var(--vg-ink)',
            }}
          >
            {slowest === null
              ? NOT_MEASURED
              : formatRing(
                  slowest.answer_latency_ms as number,
                  slowest.answer_latency_source,
                )}
          </div>
          <div style={{ fontSize: 12, color: 'var(--vg-muted)', marginTop: 4 }}>
            {slowest === null
              ? `none of these ${calls.length} calls has a stored ring time`
              : `max of ${measured.length} — longest ring a caller heard`}
          </div>
        </div>
        <div style={{ minWidth: 240, flex: 1 }}>
          {Array.from(sourceCounts.entries()).map(([source, count]) => (
            <Row key={source} label={source} value={`${count}`} />
          ))}
          {measured.length < calls.length && (
            <Row
              label="no ring time stored"
              value={`${calls.length - measured.length}`}
              muted
            />
          )}
        </div>
      </div>

      <div style={{ marginTop: 12 }}>
        <Note>
          {measured.length < MIN_SAMPLES_FOR_PERCENTILE
            ? `${measured.length} ${measured.length === 1 ? 'sample is' : 'samples are'} below the ${MIN_SAMPLES_FOR_PERCENTILE} a percentile needs, so the headline is the max of ${measured.length} rather than a p95.`
            : `The headline is the max of ${measured.length} on this page, not a p95: a percentile belongs to the whole population and this is one page of rows.`}{' '}
          Each number is shown to the precision its own source supports, and the layer breakdown
          under every call names the clock it was drawn from.
        </Note>
      </div>
    </Card>
  );
}
