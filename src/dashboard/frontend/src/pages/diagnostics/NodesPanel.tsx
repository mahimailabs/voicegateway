// Diagnostics > node samples: what the boxes looked like while each call ran.
//
// This panel sits OUTSIDE the run-result tabs, next to CallsPanel and
// CorrelationPanel, for the same reason those do: it needs no billed run and no
// LiveKit credential. It reports what the scrape worker already recorded.
//
// It renders OVERLAP, never attribution. `node_samples` has no `call_id`
// because layer 7 counters are node-wide, so a node under a call here means
// only "rows exist for that node inside this call's padded window". Two
// concurrent calls list the same node; a call on a node nobody scrapes lists
// none. The wording on screen has to survive that, which is why nothing here
// says "this call ran on".
//
// Four things this file refuses to do:
//
//   * It never collapses the three window statuses. `correlated`,
//     `scrape_failed` and `no_samples` are three different claims: a scrape
//     succeeded through the window, the scrapes in the window all failed so we
//     know nothing, and nothing was scraped in the window at all. Each gets its
//     own badge colour and its own sentence. A UI that showed them the same way
//     would undo the entire T3/C2 design, which stores NULL rather than 0 for
//     exactly this hop.
//   * It never renders an unmeasured value as 0. A gauge with no samples and a
//     counter with no sourceable rate are suppressed from the numbers and named
//     as NOT_MEASURED, the same word every other unmeasured value on this page
//     uses. A flat zero line reads as a clean bill of health.
//   * It never hides the pad. The correlation is by TIME WINDOW, and "within
//     15 s of this call" is a weaker claim than "during this call". The pad is
//     printed on every window and in the summary, so the reader can judge the
//     correlation instead of taking it.
//   * It never computes. Every count, bound, peak and rate comes from
//     `node_correlation_repository` through GET /api/nodes. The peak statistic
//     is printed with the name the backend gave it, so "max of 3" can never be
//     read as a p95.

import { useEffect, useRef, useState } from 'react';
import { ApiError, fetchNodeCorrelation } from '../../lib/api';
import { formatDateTime } from '../../lib/time';
import type {
  CallNodeCorrelation,
  NodeCorrelationResponse,
  NodeCounterRateSummary,
  NodeGaugeSummary,
  NodePeakStat,
  NodeSamplesInWindow,
  NodeWindow,
  NodeWindowStatus,
} from '../../lib/types';
import { Card, Note, NOT_MEASURED, Row } from './shared';

const LABEL = 'Node samples around each call';

/** How many recent calls the panel correlates. The endpoint caps this at 10. */
const CALL_LIMIT = 5;

/** A duration in milliseconds, as seconds. */
function secs(ms: number): string {
  return `${(ms / 1000).toFixed(1)} s`;
}

/** A measured value. Only ever called with a number the backend measured. */
function num(value: number): string {
  return value.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

/**
 * The name of the statistic beside the number, which is what stops a maximum
 * over three points being read as a percentile. `n` is the count the statistic
 * was taken over, so "max of 3" is checkable.
 */
function peakStatLabel(stat: NodePeakStat, n: number): string {
  if (stat === 'p95') return 'p95';
  if (stat === 'max_of_n') return `max of ${n}`;
  return NOT_MEASURED;
}

/** Green, red, neutral. Three statuses, three colours, never shared. */
function statusBadgeClass(status: NodeWindowStatus): string {
  if (status === 'correlated') return 'neo-badge--green';
  // The scrape itself did not work. That is a failure of the watching, and it
  // is the one state that must not look calm.
  if (status === 'scrape_failed') return 'neo-badge--red';
  // `no_samples`: nothing was measured. Neutral on purpose, because nothing was
  // measured is not the same as something being wrong.
  return 'neo-badge--black';
}

function statusLabel(status: NodeWindowStatus): string {
  if (status === 'correlated') return 'correlated';
  if (status === 'scrape_failed') return 'scrape failed';
  return 'no samples';
}

/**
 * Label for a call card: the most specific identifier the row actually has.
 *
 * Deliberately a local copy of the same fallback chain CallsPanel uses. It is
 * not imported from there because that file renders a different payload and is
 * not this panel's dependency; if a third surface needs it, that is the point
 * to lift it into `shared.tsx`.
 */
function callLabel(call: CallNodeCorrelation): string {
  if (call.room_name) return call.room_name;
  if (call.room_sid) return call.room_sid;
  if (call.attempt_id) return `attempt ${call.attempt_id}`;
  return `call ${call.id.slice(0, 12)}`;
}

export default function NodesPanel() {
  const mountedRef = useRef(true);
  const [data, setData] = useState<NodeCorrelationResponse | null>(null);
  const [error, setError] = useState<{ message: string; status: number | null } | null>(
    null,
  );

  useEffect(() => {
    mountedRef.current = true;
    fetchNodeCorrelation({ limit: CALL_LIMIT })
      .then((res) => {
        if (mountedRef.current) setData(res);
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
            ? 'This dashboard build does not serve node samples: /api/nodes answered 404. ' +
              'The scrape worker may still be recording them; nothing on this page reads ' +
              'them until that endpoint exists.'
            : `Node samples could not be read: ${error.message}`}
        </Note>
      </Card>
    );
  }

  if (data === null) {
    return (
      <Card label={LABEL}>
        <Note>Reading the node scrapes that overlap the recent calls...</Note>
      </Card>
    );
  }

  return (
    <>
      <SummaryCard data={data} />
      {/* Nothing scraped anywhere means every window below would say
          `no_samples` for the one reason already stated above, so the per-call
          cards are not drawn: there is nothing per-call about it. */}
      {data.samples_stored > 0 &&
        data.calls.map((call) => <CallNodesCard key={call.id} call={call} />)}
    </>
  );
}

/** The deployment-wide facts: is anything scraped, and how wide is the window. */
function SummaryCard({ data }: { data: NodeCorrelationResponse }) {
  const scraping = data.samples_stored > 0;

  return (
    <Card
      label={LABEL}
      right={
        <span className={`neo-badge ${scraping ? 'neo-badge--blue' : 'neo-badge--black'}`}>
          {scraping ? `${data.samples_stored.toLocaleString()} samples stored` : 'not scraping'}
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
              color: scraping ? 'var(--vg-ink)' : 'var(--vg-muted-2)',
            }}
          >
            {data.samples_stored.toLocaleString()}
          </div>
          <div style={{ fontSize: 12, color: 'var(--vg-muted)', marginTop: 4 }}>
            {scraping
              ? 'node scrapes on disk (livekit-server, livekit-sip, node_exporter)'
              : 'node scrapes on disk: nothing has ever been scraped here'}
          </div>
        </div>
        <div style={{ minWidth: 240, flex: 1 }}>
          {/* The pad is printed here as well as on every window, so it is
              readable even by a deployment with no call to carry one. */}
          <Row label="window pad, each side" value={secs(data.pad_ms)} />
          {/* How many calls are ON SCREEN, which is NOT how many correlated:
              two of them can be `no_samples` and `scrape_failed`. Labelling
              this "correlated" would be the exact collapse this panel exists
              to prevent. */}
          <Row label="calls on this page" value={String(data.calls.length)} />
        </div>
      </div>

      <div style={{ marginTop: 12 }}>
        {!scraping ? (
          <Note>
            No node has ever been scraped by this deployment, so every recent call's window
            is <span className="mono">no_samples</span> for that one reason and no per-call
            card is drawn. That is not a healthy fleet and it is not a zero: nobody looked.
            Point the scrape worker at your exporters with{' '}
            <span className="mono">VOICEGW_NODE_SCRAPE_TARGETS</span> (comma separated{' '}
            <span className="mono">source:name=url</span> entries), and a card per call
            appears here once the first tick has written a row.
          </Note>
        ) : data.calls.length === 0 ? (
          <Note>
            Node scrapes are on disk, but no call has been recorded to correlate them
            against. A call row appears the moment the LiveKit webhook receiver or an agent
            self-report observes one, and its window is matched against these samples then.
            Load-test traffic is excluded here, the same as on the calls panel.
          </Note>
        ) : (
          <Note>
            Each card below lists the nodes whose scrapes OVERLAP that call's window,
            widened by {secs(data.pad_ms)} on each side. Overlap is not attribution: layer 7
            counters are node-wide and no node is recorded against a call, so this says what
            the boxes looked like while the call was open, never that the call's media
            crossed them. Two calls running at once list the same nodes.
          </Note>
        )}
      </div>
    </Card>
  );
}

/** One call, its window, and the three ways that window can come back. */
function CallNodesCard({ call }: { call: CallNodeCorrelation }) {
  const correlation = call.correlation;

  return (
    <Card
      label={`Nodes · ${callLabel(call)}`}
      right={
        correlation === null ? (
          // A fourth, distinct shape: no window was ever built, so none of the
          // three statuses applies to it.
          <span className="neo-badge neo-badge--white">no window</span>
        ) : (
          <span className={`neo-badge ${statusBadgeClass(correlation.status)}`}>
            {statusLabel(correlation.status)}
          </span>
        )
      }
    >
      {correlation === null ? (
        <Note>
          This call has no closed span (it is still in flight, or it is an INVITE that never
          produced a room), so no window was built and no sample was looked for. That is not{' '}
          <span className="mono">no_samples</span>: substituting the current time for the
          missing end would invent the span, and the nodes it matched would then depend on
          when this page was loaded.
        </Note>
      ) : (
        <>
          <WindowRows window={correlation.window} />
          {correlation.status === 'no_samples' ? (
            <Note>
              Nothing was scraped inside this window at all, so there is nothing to show and
              no node is listed. This is <span className="mono">no_samples</span>, which is
              not a reading: it does not say the nodes were idle or healthy, only that no
              scrape covered this time. A window older than the scrape worker's first tick,
              a target that was not configured yet, or a window past the trim all look like
              this.
            </Note>
          ) : correlation.status === 'scrape_failed' ? (
            <>
              <Note tone="bad">
                Rows exist for this window and every one of them is a FAILED scrape, so we
                know nothing about these nodes while the call was open. This is{' '}
                <span className="mono">scrape_failed</span>, and it is emphatically not
                "the nodes were fine": the boxes were being watched and the watching did not
                work. Every value below stays {NOT_MEASURED}; the outcome on each node names
                what went wrong.
              </Note>
              <NodeList entries={correlation.nodes_sampled} />
            </>
          ) : (
            <NodeList entries={correlation.nodes_sampled} />
          )}
        </>
      )}
    </Card>
  );
}

/** The window, with the pad it was widened by spelled out rather than implied. */
function WindowRows({ window }: { window: NodeWindow }) {
  const requested = window.requested_end_ms - window.requested_start_ms;
  const queried = window.end_ms - window.start_ms;

  return (
    <div style={{ marginBottom: 12 }}>
      <Row label="call span" value={secs(requested)} />
      <Row label="window queried" value={secs(queried)} />
      {/* THE number that makes this correlation auditable. */}
      <Row label="pad, each side" value={secs(window.pad_ms)} />
      {/* "Searched", not "matched": this line is the query, and it is printed
          the same way whether the window came back with samples, with nothing,
          or with nothing but failures. */}
      <div style={{ fontSize: 12, color: 'var(--vg-muted)', marginTop: 6 }}>
        Samples were searched for between {formatDateTime(window.start_ms)} and{' '}
        {formatDateTime(window.end_ms)}. The call itself ran from{' '}
        {formatDateTime(window.requested_start_ms)} to{' '}
        {formatDateTime(window.requested_end_ms)}; the {secs(window.pad_ms)} on each side is
        padding, not part of the call.
      </div>
    </div>
  );
}

function NodeList({ entries }: { entries: NodeSamplesInWindow[] }) {
  return (
    <>
      {entries.map((entry) => (
        <NodeBlock key={`${entry.node}/${entry.source}`} entry={entry} />
      ))}
    </>
  );
}

/** One `(node, source)` that has rows in the window. Sampled, not responsible. */
function NodeBlock({ entry }: { entry: NodeSamplesInWindow }) {
  const gauges = Object.values(entry.gauges);
  const measuredGauges = gauges.filter((g) => g.samples > 0);
  const unmeasuredGauges = gauges.filter((g) => g.samples === 0);
  const counters = Object.values(entry.counters);
  const ratedCounters = counters.filter((c) => c.peak_per_second !== null);
  const unratedCounters = counters.filter((c) => c.peak_per_second === null);
  const outcomes = Object.entries(entry.outcomes);

  return (
    <div
      style={{
        borderTop: '1px solid var(--vg-border, rgba(0,0,0,0.12))',
        paddingTop: 10,
        marginTop: 12,
      }}
    >
      <div
        className="flex-row"
        style={{ justifyContent: 'space-between', alignItems: 'center', gap: 10 }}
      >
        <span className="mono" style={{ fontSize: 13, fontWeight: 700 }}>
          {entry.node} · {entry.source}
        </span>
        <span
          className={`neo-badge ${entry.ok_samples > 0 ? 'neo-badge--green' : 'neo-badge--red'}`}
        >
          {entry.ok_samples} ok / {entry.failed_samples} failed
        </span>
      </div>

      <div style={{ marginTop: 8 }}>
        <Row label="samples in window" value={String(entry.samples)} />
        <Row
          label="outcomes"
          value={outcomes.map(([name, count]) => `${name} x${count}`).join(', ')}
        />
        <Row
          label="first sample"
          value={
            entry.first_sample_at_ms == null
              ? NOT_MEASURED
              : formatDateTime(entry.first_sample_at_ms)
          }
          muted={entry.first_sample_at_ms == null}
        />
        <Row
          label="last sample"
          value={
            entry.last_sample_at_ms == null
              ? NOT_MEASURED
              : formatDateTime(entry.last_sample_at_ms)
          }
          muted={entry.last_sample_at_ms == null}
        />
      </div>

      {entry.truncated && (
        <div style={{ marginTop: 8 }}>
          <Note tone="warn">
            The read hit its row limit, so these summaries describe the START of the window
            rather than the whole of it. Narrow the window to get numbers for all of it.
          </Note>
        </div>
      )}

      {measuredGauges.length > 0 && (
        <div style={{ marginTop: 8 }}>
          {measuredGauges.map((gauge) => (
            <GaugeRow key={gauge.column} gauge={gauge} />
          ))}
        </div>
      )}

      {ratedCounters.length > 0 && (
        <div style={{ marginTop: 8 }}>
          {ratedCounters.map((counter) => (
            <CounterRow key={counter.column} counter={counter} />
          ))}
        </div>
      )}

      {/* Suppressed, and named. An absent series must never be drawn as a flat
          zero, and it must never simply vanish either: the reader has to be able
          to see that it was asked for and not answered. */}
      {unmeasuredGauges.length > 0 && (
        <div style={{ fontSize: 12, color: 'var(--vg-muted)', marginTop: 8, lineHeight: 1.5 }}>
          {NOT_MEASURED} by this scrape ({unmeasuredGauges.length} of {gauges.length}{' '}
          gauges):{' '}
          <span className="mono">{unmeasuredGauges.map((g) => g.column).join(', ')}</span>. A
          source only carries its own series, so node-exporter having no{' '}
          <span className="mono">rooms</span> is correct. No value is shown for these
          because none was measured.
        </div>
      )}

      {unratedCounters.length > 0 && (
        <div style={{ fontSize: 12, color: 'var(--vg-muted)', marginTop: 8, lineHeight: 1.5 }}>
          No per-second rate is sourceable for {unratedCounters.length} of {counters.length}{' '}
          counters: <span className="mono">{unratedCounters.map((c) => c.column).join(', ')}</span>
          . Either the series was absent, or the counter reset inside the window (a restart
          zeroes every total, and the increment across that boundary is unknowable). Which
          one is not guessed here. That is {NOT_MEASURED}, not 0/s.
        </div>
      )}
    </div>
  );
}

/** One gauge: its last measured value, and its peak with the statistic named. */
function GaugeRow({ gauge }: { gauge: NodeGaugeSummary }) {
  return (
    <Row
      label={gauge.column}
      value={
        <>
          {gauge.latest == null ? NOT_MEASURED : num(gauge.latest)} latest
          {gauge.peak != null && (
            <span style={{ color: 'var(--vg-muted)', fontWeight: 500 }}>
              {' '}
              · {num(gauge.peak)} {peakStatLabel(gauge.peak_stat, gauge.samples)}
            </span>
          )}
        </>
      }
      muted={gauge.latest == null}
    />
  );
}

/** One counter, as a rate. Unknown points are reported, never dropped. */
function CounterRow({ counter }: { counter: NodeCounterRateSummary }) {
  const known = counter.points - counter.unknown_points;

  return (
    <Row
      label={counter.column}
      value={
        <>
          {counter.peak_per_second == null ? NOT_MEASURED : `${num(counter.peak_per_second)}/s`}
          <span style={{ color: 'var(--vg-muted)', fontWeight: 500 }}>
            {' '}
            {peakStatLabel(counter.peak_stat, known)}
            {counter.unknown_points > 0 &&
              ` · ${counter.unknown_points} of ${counter.points} points unknown`}
          </span>
        </>
      }
      muted={counter.peak_per_second == null}
    />
  );
}
