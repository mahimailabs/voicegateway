// The per-call layer waterfall: one call, decomposed across layers 1-6
// (SIP -> SFU -> dispatch -> agent -> inference -> provider).
//
// This is the surface that makes the product claim legible: "the caller heard
// 4.1 s of ring because the agent took 3.8 s to publish audio". Which means
// every honesty rule that keeps that sentence true lives HERE, in the component,
// so no caller can render the waterfall without them.
//
// 1. NOTHING IS COMPUTED HERE THAT THE BACKEND OWNS. The ring time is
//    `calls.answer_latency_ms`, derived once by
//    `calls_repository._derive_answer_latency` and displayed verbatim. This
//    component splits the leg timeline into segments, which is a different
//    claim, and it never sums those segments into a headline: when a stronger
//    clock measured the ring (`sipp_rtd` is the true INVITE -> 200 OK wall time)
//    the segments legitimately do not add up to it, because layers 1-2 are
//    inside the ring and cannot be split.
//
// 2. AN UNMEASURED LAYER IS NEVER A ZERO. A zero-width segment reads as
//    "instant"; a missing row reads as "nothing happened there". Both are lies
//    about a layer nobody measured. Every unmeasured layer gets a full-width
//    hatched track carrying the literal words "not measured" plus the reason it
//    cannot be measured, and it is excluded from the stacked bar entirely.
//
// 3. THE CLOCK IS NAMED. `call_legs.joined_at_source` /
//    `first_audio_track_at_source` say which clock produced each timestamp. A
//    webhook's `created_at` is whole SECONDS, so a number derived from one
//    carries up to a second of truncation and is rendered to a tenth of a second
//    with that caveat attached - never to the millisecond, which would be a
//    lie with a decimal point. Only a self-reporting process inside the call
//    (`agent`, `loadgen`) earns millisecond rendering, matching
//    `calls_repository.SELF_REPORT_ORIGINS`.
//
// 4. NO LOSS, NO JITTER, NO MOS, NO SIP RESPONSE-CODE LANE. None of it is
//    observable server-side (`sfu.py` hardcodes `loss_pct = 0.0`, and
//    livekit-api 1.1.0 exposes no ListSIPCallInfo). A flat green loss line reads
//    as a clean bill of health nobody measured.

import type { CallDetail, CallLegRow } from '../lib/types';
import { NOT_MEASURED } from '../pages/diagnostics/shared';

/** LiveKit's participant kinds, as both writers store them. */
const SIP_KIND = 'SIP';
const AGENT_KIND = 'AGENT';

/**
 * Clocks that belong to a process inside the call, i.e. a real millisecond
 * clock. Mirrors `calls_repository.SELF_REPORT_ORIGINS`. Anything else -
 * including null, "the writer did not say" - is treated as webhook precision,
 * which under-claims rather than over-claims.
 */
const SELF_REPORT_CLOCKS = new Set(['agent', 'loadgen']);

/** How precisely a span may honestly be printed. */
type Precision = 'ms' | 'coarse';

/** The caveat that must travel with every `coarse` number. */
const COARSE_CAVEAT = 'whole-second webhook clock, up to 1 s of truncation';

function precisionOf(source: string | null | undefined): Precision {
  return source != null && SELF_REPORT_CLOCKS.has(source) ? 'ms' : 'coarse';
}

/** The weaker of two clocks wins, the same way the backend picks a source. */
function weakerOf(a: Precision, b: Precision): Precision {
  return a === 'coarse' || b === 'coarse' ? 'coarse' : 'ms';
}

/** A span, printed only as precisely as its worst clock supports. */
function formatSpan(ms: number, precision: Precision): string {
  return precision === 'ms' ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(1)} s`;
}

/** Human name for a clock. Null is honest about being unattributed. */
function clockName(source: string | null | undefined): string {
  if (source === 'agent') return 'agent self-report';
  if (source === 'loadgen') return 'load worker self-report';
  if (source === 'webhook') return 'LiveKit webhook';
  return 'unattributed clock';
}

/**
 * What each `answer_latency_source` actually is, and how far to trust it.
 * Keyed by string, not by the union, so a name added server-side later falls
 * through to the unknown branch instead of being silently mislabelled.
 */
const SOURCE_META: Record<string, { badge: string; what: string; precision: Precision }> = {
  sipp_rtd: {
    badge: 'neo-badge--green',
    what: 'the true INVITE to 200 OK wall time, measured by the load worker that placed the call',
    precision: 'ms',
  },
  agent_report: {
    badge: 'neo-badge--blue',
    what: 'the track-publish subtraction, from two millisecond timestamps a process inside the call reported',
    precision: 'ms',
  },
  webhook_proxy: {
    badge: 'neo-badge--warning',
    what: 'the track-publish subtraction, from webhook timestamps',
    precision: 'coarse',
  },
};

/** One row of the ladder: a layer, and either its span or why there is none. */
interface Layer {
  n: number;
  name: string;
  /** What the layer spans, in the caller's terms. */
  span: string;
  color: string;
  /** Null means not measured; it is never rendered as 0. */
  ms: number | null;
  precision: Precision;
  /** Which clock(s) produced it, when it was measured. */
  clock: string | null;
  /** Why there is no number. Always set when `ms` is null. */
  reason: string;
}

/**
 * The earliest leg of `kind` that reported `field`, with the clock that
 * reported it.
 *
 * Earliest, because a transfer or SIP REFER adds a second leg of the same kind:
 * the caller is the SIP leg that joined first, and the audio that released the
 * 200 OK is the first one an agent published. Mirrors
 * `calls_repository._earliest_with_source` so the segments drawn here describe
 * the same two legs the stored headline was derived from.
 */
function earliestLegBy(
  legs: CallLegRow[],
  kind: string,
  field: 'joined_at_ms' | 'first_audio_track_at_ms',
): CallLegRow | null {
  let best: CallLegRow | null = null;
  for (const leg of legs) {
    if (leg.kind !== kind) continue;
    const value = leg[field];
    if (value == null) continue;
    if (best === null || value < (best[field] as number)) best = leg;
  }
  return best;
}

/**
 * True when a subtraction of two observations can be a real interval.
 *
 * Mirrors the non-positive half of `calls_repository._plausible_answer_latency`:
 * livekit-sip withholds the 200 OK until it subscribes to an audio track, so a
 * publish cannot precede the join it is being measured from. A non-positive
 * result means the two clocks disagree, and drawing it as a segment would put a
 * fabricated bar on screen.
 */
function isRealInterval(ms: number): boolean {
  return ms > 0;
}

/**
 * The six layers for one call, from the stored row and its legs.
 *
 * Layers 1, 2, 5 and 6 are not measurable from `calls` + `call_legs` today, and
 * each one says exactly why rather than sharing a generic "no data" string: the
 * reason is the useful part, because it tells an operator whether the gap is a
 * missing integration (fixable) or a platform limit (not).
 */
function buildLayers(call: CallDetail): Layer[] {
  const legs = call.legs;
  const callerLeg = earliestLegBy(legs, SIP_KIND, 'joined_at_ms');
  const publishLeg = earliestLegBy(legs, AGENT_KIND, 'first_audio_track_at_ms');
  const agentJoinLeg = publishLeg ?? earliestLegBy(legs, AGENT_KIND, 'joined_at_ms');

  const hasSipLeg = legs.some((l) => l.kind === SIP_KIND);
  const hasAgentLeg = legs.some((l) => l.kind === AGENT_KIND);

  // Layer 1 cannot be timed, but the SIP leg's disconnect reason is real
  // layer-1 truth (SIP_TRUNK_FAILURE, USER_UNAVAILABLE, USER_REJECTED) and is
  // the only thing this layer does tell us, so it is reported next to the gap.
  const sipDisconnect = legs.find(
    (l) => l.kind === SIP_KIND && l.disconnect_reason != null,
  )?.disconnect_reason;
  let sipReason =
    'livekit-api 1.1.0 exposes no ListSIPCallInfo, so LiveKit never reports a per-call ' +
    'SIP response. Only a load worker that placed the INVITE itself can time this leg.';
  if (sipDisconnect) {
    sipReason += ` The SIP leg did report ${sipDisconnect} as its disconnect reason.`;
  }

  // Layer 3: caller in the room -> agent joined.
  let dispatchMs: number | null = null;
  let dispatchPrecision: Precision = 'coarse';
  let dispatchClock: string | null = null;
  let dispatchReason = '';
  if (callerLeg == null) {
    dispatchReason = hasSipLeg
      ? 'The SIP leg on this call has no join time, so there is no caller-in-room moment to measure from.'
      : 'No SIP leg on this call, so there is no caller waiting on a dispatch.';
  } else if (agentJoinLeg == null || agentJoinLeg.joined_at_ms == null) {
    dispatchReason = hasAgentLeg
      ? 'The agent leg has no join time recorded, so the dispatch cannot be timed.'
      : 'No agent leg ever joined this call: the dispatch did not complete.';
  } else {
    const span = agentJoinLeg.joined_at_ms - (callerLeg.joined_at_ms as number);
    if (isRealInterval(span)) {
      dispatchMs = span;
      dispatchPrecision = weakerOf(
        precisionOf(callerLeg.joined_at_source),
        precisionOf(agentJoinLeg.joined_at_source),
      );
      dispatchClock = `${clockName(callerLeg.joined_at_source)} -> ${clockName(
        agentJoinLeg.joined_at_source,
      )}`;
    } else {
      dispatchReason =
        'The recorded times put the agent in the room before the caller, so the two ' +
        'clocks disagree. A negative interval is not a measurement.';
    }
  }

  // Layer 4: agent joined -> agent published its first audio track. Both
  // timestamps are taken from the SAME leg, so the segment describes one
  // process's own startup rather than two participants' clocks.
  let agentMs: number | null = null;
  let agentPrecision: Precision = 'coarse';
  let agentClock: string | null = null;
  let agentReason = '';
  if (publishLeg == null) {
    agentReason = hasAgentLeg
      ? 'The agent never published an audio track, so the caller never heard an answer.'
      : 'No agent leg on this call, so nothing published audio.';
  } else if (publishLeg.joined_at_ms == null) {
    agentReason =
      'The publishing agent leg has no join time, so its own startup cannot be separated ' +
      'from the dispatch that preceded it.';
  } else {
    const span = (publishLeg.first_audio_track_at_ms as number) - publishLeg.joined_at_ms;
    if (isRealInterval(span)) {
      agentMs = span;
      agentPrecision = weakerOf(
        precisionOf(publishLeg.joined_at_source),
        precisionOf(publishLeg.first_audio_track_at_source),
      );
      agentClock = `${clockName(publishLeg.joined_at_source)} -> ${clockName(
        publishLeg.first_audio_track_at_source,
      )}`;
    } else {
      agentReason =
        'The recorded audio-publish time is not after the agent joined, so the two ' +
        'observations came from clocks that disagree.';
    }
  }

  return [
    {
      n: 1,
      name: 'SIP',
      span: 'INVITE to the caller entering the room',
      color: 'var(--vg-teal-deep)',
      ms: null,
      precision: 'coarse',
      clock: null,
      reason: sipReason,
    },
    {
      n: 2,
      name: 'SFU',
      span: 'the media server admitting the caller',
      color: 'var(--vg-teal-bright)',
      ms: null,
      precision: 'coarse',
      clock: null,
      reason:
        'The SFU reports no per-call transport timing. The SFU baseline elsewhere on this ' +
        'page is a fleet round trip from the prober, not this call.',
    },
    {
      n: 3,
      name: 'Dispatch',
      span: 'caller in the room to the agent joining',
      color: 'var(--vg-teal)',
      ms: dispatchMs,
      precision: dispatchPrecision,
      clock: dispatchClock,
      reason: dispatchReason,
    },
    {
      n: 4,
      name: 'Agent',
      span: 'agent joining to its first published audio',
      color: 'var(--vg-green)',
      ms: agentMs,
      precision: agentPrecision,
      clock: agentClock,
      reason: agentReason,
    },
    {
      n: 5,
      name: 'Inference',
      span: 'STT, LLM and TTS to first byte, inside layer 4',
      color: 'var(--vg-amber)',
      ms: null,
      precision: 'coarse',
      clock: null,
      reason:
        'No inference is joined to this call on this payload: calls and call_legs carry no ' +
        'STT, LLM or TTS timing, so the share of layer 4 that was model time is unknown here.',
    },
    {
      n: 6,
      name: 'Provider',
      span: "the provider's own time inside layer 5",
      color: 'var(--vg-red)',
      ms: null,
      precision: 'coarse',
      clock: null,
      reason:
        'Provider time is a slice of layer 5, so it is unknown for the same reason: this call ' +
        'has no inference joined to it.',
    },
  ];
}

/** Diagonal hatch. Deliberately unlike every solid segment on the page. */
const HATCH =
  'repeating-linear-gradient(45deg, var(--vg-hairline) 0 5px, transparent 5px 10px)';

export default function CallLayerWaterfall({ call }: { call: CallDetail }) {
  const layers = buildLayers(call);
  const measured = layers.filter((l) => l.ms !== null);
  const measuredTotal = measured.reduce((sum, l) => sum + (l.ms as number), 0);

  const source = call.answer_latency_source;
  const meta = source ? SOURCE_META[source] : undefined;
  const ringMs = call.answer_latency_ms;
  // The headline is printed at the precision of ITS OWN source, not at the
  // precision of the segments below it: the two can legitimately differ, and a
  // sipp_rtd number is millisecond-true even when every leg came from webhooks.
  const ringText =
    ringMs == null ? NOT_MEASURED : formatSpan(ringMs, meta?.precision ?? 'coarse');

  return (
    <div>
      {/* Headline: the one number the backend owns, with its provenance. */}
      <div className="flex-row" style={{ gap: 28, flexWrap: 'wrap', alignItems: 'flex-end' }}>
        <div>
          <div
            className="mono"
            style={{
              fontSize: 30,
              fontWeight: 800,
              lineHeight: 1.1,
              color: ringMs == null ? 'var(--vg-muted-2)' : 'var(--vg-ink)',
            }}
          >
            {ringText}
          </div>
          <div style={{ fontSize: 12, color: 'var(--vg-muted)', marginTop: 4 }}>
            what the caller heard as ring
          </div>
        </div>
        <div style={{ minWidth: 240, flex: 1 }}>
          {source ? (
            <>
              <div className="flex-row" style={{ gap: 8, alignItems: 'center' }}>
                <span className={`neo-badge ${meta?.badge ?? 'neo-badge--black'}`}>{source}</span>
                {meta?.precision === 'coarse' && (
                  <span style={{ fontSize: 12, color: 'var(--vg-amber)' }}>&plusmn; up to 1 s</span>
                )}
              </div>
              <div style={{ fontSize: 12, color: 'var(--vg-muted)', marginTop: 6, lineHeight: 1.5 }}>
                {meta
                  ? `Source: ${meta.what}.`
                  : 'Source: a provenance this build does not recognise, so the number is shown ' +
                    'at the coarser precision rather than being trusted to the millisecond.'}
                {meta?.precision === 'coarse' && ` A ${COARSE_CAVEAT}.`}
              </div>
            </>
          ) : (
            <div style={{ fontSize: 12, color: 'var(--vg-muted)', lineHeight: 1.5 }}>
              No ring time is stored for this call, so it reads as {NOT_MEASURED} rather than as a
              zero, which would claim the caller heard no ring at all. The layers below say which
              observation is missing.
            </div>
          )}
        </div>
      </div>

      {/* The stacked bar: measured layers only, in order, contiguous. */}
      <div style={{ marginTop: 18 }}>
        <div
          className="flex-row"
          style={{ justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 6 }}
        >
          <span className="vg-card__label" style={{ fontSize: 11 }}>
            Measured span &middot; {measured.length} of {layers.length} layers
          </span>
          {measured.length > 0 && (
            <span className="mono" style={{ fontSize: 12, fontWeight: 700 }}>
              {formatSpan(
                measuredTotal,
                measured.reduce<Precision>((p, l) => weakerOf(p, l.precision), 'ms'),
              )}
            </span>
          )}
        </div>
        {measured.length === 0 ? (
          <div
            style={{
              height: 22,
              background: HATCH,
              border: '1px solid var(--vg-hairline)',
              borderRadius: 'var(--vg-radius-xs)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              fontSize: 11,
              fontWeight: 700,
              color: 'var(--vg-muted-2)',
            }}
          >
            no layer of this call was measured
          </div>
        ) : (
          <div
            style={{
              display: 'flex',
              height: 22,
              borderRadius: 'var(--vg-radius-xs)',
              overflow: 'hidden',
              border: '1px solid var(--vg-hairline)',
            }}
          >
            {measured.map((l) => {
              const pct = ((l.ms as number) / measuredTotal) * 100;
              return (
                <div
                  key={l.n}
                  title={`Layer ${l.n} ${l.name}: ${formatSpan(l.ms as number, l.precision)}`}
                  style={{
                    width: `${pct}%`,
                    background: l.color,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    color: '#fff',
                    fontSize: 10,
                    fontWeight: 700,
                    minWidth: 0,
                  }}
                >
                  {pct > 16 ? l.name : ''}
                </div>
              );
            })}
          </div>
        )}
        <div style={{ fontSize: 11, color: 'var(--vg-muted)', marginTop: 6, lineHeight: 1.5 }}>
          The bar holds only the layers that were measured, so it is not the whole call and it does
          not have to add up to the ring time above.
        </div>
      </div>

      {/* The ladder: every layer, measured or not. */}
      <div style={{ marginTop: 16 }}>
        {layers.map((l) => (
          <LayerRow key={l.n} layer={l} measuredTotal={measuredTotal} />
        ))}
      </div>
    </div>
  );
}

function LayerRow({ layer, measuredTotal }: { layer: Layer; measuredTotal: number }) {
  const isMeasured = layer.ms !== null;
  const pct = isMeasured && measuredTotal > 0 ? ((layer.ms as number) / measuredTotal) * 100 : 0;

  return (
    <div style={{ padding: '7px 0', borderTop: '1px solid var(--vg-hairline-2)' }}>
      <div
        className="flex-row"
        style={{ justifyContent: 'space-between', alignItems: 'baseline', gap: 12 }}
      >
        <span style={{ fontSize: 12, color: 'var(--vg-ink)', fontWeight: 700 }}>
          {layer.n}. {layer.name}
          <span style={{ fontWeight: 400, color: 'var(--vg-muted)' }}> &middot; {layer.span}</span>
        </span>
        <span
          className="mono"
          style={{
            fontSize: 13,
            fontWeight: 700,
            whiteSpace: 'nowrap',
            color: isMeasured ? 'var(--vg-ink)' : 'var(--vg-muted-2)',
          }}
        >
          {isMeasured ? formatSpan(layer.ms as number, layer.precision) : NOT_MEASURED}
        </span>
      </div>

      {/* An unmeasured layer gets a full-width hatch carrying the words, never a
          zero-width sliver that reads as "instant". */}
      <div
        style={{
          marginTop: 5,
          height: 14,
          borderRadius: 3,
          background: isMeasured ? 'var(--vg-hairline-2)' : HATCH,
          border: isMeasured ? 'none' : '1px dashed var(--vg-hairline)',
          display: 'flex',
          alignItems: 'center',
          overflow: 'hidden',
        }}
      >
        {isMeasured ? (
          <div style={{ width: `${pct}%`, height: '100%', background: layer.color }} />
        ) : (
          <span
            style={{
              fontSize: 9,
              fontWeight: 700,
              letterSpacing: 0.4,
              color: 'var(--vg-muted-2)',
              paddingLeft: 8,
              textTransform: 'uppercase',
            }}
          >
            {NOT_MEASURED}
          </span>
        )}
      </div>

      <div style={{ fontSize: 11, color: 'var(--vg-muted)', marginTop: 4, lineHeight: 1.5 }}>
        {isMeasured
          ? `Clock: ${layer.clock}.${
              layer.precision === 'coarse' ? ` A ${COARSE_CAVEAT}, so this is shown to a tenth of a second.` : ''
            }`
          : layer.reason}
      </div>
    </div>
  );
}
