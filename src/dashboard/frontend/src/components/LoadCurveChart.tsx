// The concurrency curve from an SFU load ramp: data-channel round trip against
// the number of synthetic clients in the room, with the budget and the knee
// marked on it.
//
// Two honesty rules live in this component rather than in its callers, so no
// caller can render the curve without them.
//
// 1. The Y axis is LABELLED, and it is labelled "SFU data-channel RTT (ms)".
//    This is what the prober measured by bouncing a message through the SFU's
//    data channel. It is NOT the latency a caller hears: LiveKit gives no
//    `track_subscribed` webhook and no per-call SIP timing, so answer latency
//    cannot be derived from this run at all. An axis of bare milliseconds reads
//    as answer latency to anyone glancing at it, which would misstate what
//    VoiceGateway measured. The series name says the same thing in the tooltip.
// 2. There is no loss series and there is no loss axis. `sfu.py` hardcodes
//    `loss_pct = 0.0`, so a loss line would draw a flat 0% clean bill of health
//    that nobody measured.
//
// Percentiles are also absent by construction: a tier is one rtt sample, so
// there is nothing here that could be labelled p95.

import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import NeoTooltip from './NeoTooltip';
import type { DiagSfuStep } from '../lib/types';

const TEAL = '#1F96AA';

/** What the Y axis measures. Stated once, used for the axis and the tooltip. */
export const RTT_AXIS_LABEL = 'SFU data-channel RTT (ms)';

export default function LoadCurveChart({
  ramp,
  targetRttMs,
  knee,
  height = 250,
  emptyText = 'No ramp tier was measured, so there is no curve to plot.',
}: {
  /** The measured tiers, ascending by client count. */
  ramp: DiagSfuStep[];
  /** The rtt budget the knee was computed against, drawn as a reference line. */
  targetRttMs: number;
  /**
   * The last healthy tier, or null when there is no knee. The caller must have
   * already resolved WHICH null this is (nothing breached vs the first tier
   * breached) - this component only marks a knee it is given, it never implies
   * that a missing knee means a healthy ramp.
   */
  knee: number | null;
  height?: number;
  emptyText?: string;
}) {
  const data = ramp.map((s) => ({
    clients: s.clients,
    rtt: Number(s.rtt_ms.toFixed(1)),
  }));

  if (data.length === 0) {
    return <div style={{ fontSize: 12, color: 'var(--vg-muted-2)' }}>{emptyText}</div>;
  }

  return (
    <div style={{ width: '100%', height, marginTop: 16 }}>
      <ResponsiveContainer>
        <LineChart data={data} margin={{ top: 16, right: 16, left: 0, bottom: 8 }}>
          <CartesianGrid strokeDasharray="4 4" stroke="var(--vg-hairline-2)" />
          <XAxis
            dataKey="clients"
            type="number"
            domain={[0, 'dataMax']}
            ticks={ramp.map((s) => s.clients)}
            tick={{ fontSize: 11, fontWeight: 600 }}
            label={{ value: 'concurrent clients', position: 'insideBottom', offset: -6, fontSize: 11 }}
          />
          <YAxis
            tick={{ fontSize: 11, fontWeight: 600 }}
            width={68}
            tickFormatter={(v) => `${v}`}
            label={{
              value: RTT_AXIS_LABEL,
              angle: -90,
              position: 'insideLeft',
              offset: 12,
              fontSize: 11,
              style: { textAnchor: 'middle' },
            }}
          />
          <Tooltip content={<NeoTooltip />} cursor={{ stroke: TEAL, strokeWidth: 1 }} />
          <ReferenceLine
            y={targetRttMs}
            stroke="var(--vg-amber)"
            strokeDasharray="5 4"
            label={{
              value: `${targetRttMs.toFixed(0)} ms budget`,
              position: 'insideTopRight',
              fontSize: 11,
              fill: 'var(--vg-amber)',
            }}
          />
          {knee !== null && (
            <ReferenceLine
              x={knee}
              stroke="var(--vg-red)"
              strokeDasharray="3 3"
              label={{
                value: `knee ${knee}`,
                position: 'top',
                fontSize: 11,
                fill: 'var(--vg-red)',
              }}
            />
          )}
          <Line
            type="monotone"
            dataKey="rtt"
            name={RTT_AXIS_LABEL}
            stroke={TEAL}
            strokeWidth={2}
            dot={{ r: 3, fill: TEAL }}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
