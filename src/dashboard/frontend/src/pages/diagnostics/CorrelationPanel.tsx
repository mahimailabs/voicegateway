// Diagnostics > correlation rate: how often a session joins the call it ran in.
//
// This panel sits OUTSIDE the run-result tabs, next to CallsPanel, for the same
// reason that one does: it needs no billed run and no LiveKit credential. It
// reports on what the deployment already recorded.
//
// It exists because the sessions <-> calls join fails SILENTLY. A missing
// webhook receiver, a room name the agent never saw, or one room name pinned
// across concurrent calls all leave a dashboard that looks completely normal
// with no correlation behind it. Nothing else on this page would show that.
//
// Three things this file refuses to do:
//
//   * It never computes the rate. `session_repository.read_correlation_rate`
//     decides the numerator, the denominator, the threshold and the verdict;
//     GET /api/correlation forwards them and this renders them.
//   * It never renders an unmeasured rate as 0%. `rate: null` (status
//     "unknown") means no session in this deployment ever had a room to join,
//     and 0% there would read as a total outage. It renders as NOT_MEASURED,
//     the same word every other unmeasured value on this page uses.
//   * It never hides the threshold it is judged against. 90% is printed next to
//     the number, so "below threshold" is checkable rather than asserted.

import { useEffect, useRef, useState } from 'react';
import { ApiError, fetchCorrelationRate } from '../../lib/api';
import type { CorrelationRate } from '../../lib/types';
import { Card, Note, NOT_MEASURED, Row } from './shared';

const LABEL = 'Session to call correlation';

/** A rate as a whole percent. Only ever called with a measured number. */
function pct(rate: number): string {
  return `${(rate * 100).toFixed(0)}%`;
}

/**
 * The verdict badge. `unknown` is deliberately neutral, not red: nothing was
 * measured, which is not the same as something being wrong.
 */
function statusBadgeClass(status: CorrelationRate['status']): string {
  if (status === 'ok') return 'neo-badge--green';
  if (status === 'warn') return 'neo-badge--warning';
  return 'neo-badge--black';
}

export default function CorrelationPanel() {
  const mountedRef = useRef(true);
  const [rate, setRate] = useState<CorrelationRate | null>(null);
  const [error, setError] = useState<{ message: string; status: number | null } | null>(
    null,
  );

  useEffect(() => {
    mountedRef.current = true;
    fetchCorrelationRate()
      .then((res) => {
        if (mountedRef.current) setRate(res);
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
      <Card label={LABEL}>
        <Note tone="bad">
          {error.status === 404
            ? 'This dashboard build does not serve the correlation rate: ' +
              '/api/correlation answered 404. The join is still running; nothing ' +
              'reports on whether it resolves until that endpoint exists.'
            : `The correlation rate could not be read: ${error.message}`}
        </Note>
      </Card>
    );
  }

  if (rate === null) {
    return (
      <Card label={LABEL}>
        <Note>Reading how often sessions joined the call they ran in...</Note>
      </Card>
    );
  }

  const measured = rate.rate !== null;
  const threshold = pct(rate.warn_threshold);

  return (
    <Card
      label={LABEL}
      right={
        <span className={`neo-badge ${statusBadgeClass(rate.status)}`}>
          {rate.status === 'unknown' ? NOT_MEASURED : rate.status}
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
              color: measured ? 'var(--vg-ink)' : 'var(--vg-muted-2)',
            }}
          >
            {measured ? pct(rate.rate as number) : NOT_MEASURED}
          </div>
          <div style={{ fontSize: 12, color: 'var(--vg-muted)', marginTop: 4 }}>
            {measured
              ? `${rate.correlated} of ${rate.eligible} sessions that had a room joined a call`
              : 'no session that could have joined a call has been recorded yet'}
          </div>
        </div>
        <div style={{ minWidth: 240, flex: 1 }}>
          <Row label="warn below" value={threshold} />
          <Row
            label="uncorrelated"
            value={measured ? String(rate.eligible - rate.correlated) : NOT_MEASURED}
            muted={!measured}
          />
          <Row label="ambiguous room name" value={String(rate.ambiguous)} />
          <Row label="call already pruned" value={String(rate.dangling)} />
          <Row label="never had a room" value={String(rate.no_room)} muted />
        </div>
      </div>

      <div style={{ marginTop: 12 }}>
        {/* Three distinct sentences for three distinct facts. "Below the
            threshold" and "not measured" must never be shown with the same
            words, which is the whole point of this card. */}
        {rate.status === 'unknown' ? (
          <Note>
            Nothing has been measured. The denominator is sessions that HAD a room and so
            should have joined a call, and this deployment has recorded none of those, so
            there is no rate to publish. {rate.no_room > 0
              ? `The ${rate.no_room} recorded ${rate.no_room === 1 ? 'session' : 'sessions'} had no room at all (web and Pipecat sessions never can), which is correct and is why they are not counted as failures.`
              : 'Neither 100% nor 0% would be true here, so neither is shown.'}
          </Note>
        ) : rate.status === 'warn' ? (
          <Note tone="warn">
            {pct(rate.rate as number)} is below the {threshold} warn threshold. The join is
            by room name and it fails quietly: a webhook receiver that is not deployed, an
            agent that never saw the room name, or one room name pinned across concurrent
            calls all look like this.{' '}
            {rate.ambiguous > 0
              ? `${rate.ambiguous} of them matched more than one call, which is refused rather than guessed: give each call its own room name.`
              : 'None of them was ambiguous, so this is a missing call row, not a pinned room.'}
          </Note>
        ) : (
          <Note>
            {pct(rate.rate as number)} is at or above the {threshold} warn threshold. The
            gap to 100% is normally the in-flight sessions whose call row has not been
            written yet at read time.
          </Note>
        )}
      </div>
    </Card>
  );
}
