// Diagnostics > Load: the client ramp, its knee, and the prober's own load.
//
// Two honesty rules bind this tab.
//
// 1. `find_knee` returns null for two OPPOSITE outcomes: nothing breached the
//    budget, or the FIRST tier already breached it. A chart that shows only
//    "knee: none" labels a total failure as a pass, so the ramp is compared
//    against `target_rtt_ms` here and the two cases are named differently.
// 2. The curve is rtt only. Loss is a hardcoded placeholder in `sfu.py`, so there
//    is no loss series and no loss column anywhere on this tab.
//
// The saturation card is not decoration either: a laptop that pegged its own CPU
// at 25 clients draws the same curve as an SFU that ran out of headroom, and this
// is the only block that says which one happened.

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
import NeoTooltip from '../../components/NeoTooltip';
import type { DiagSfuResult, DiagSfuStep, DiagnosticCheckResult } from '../../lib/types';
import { Card, CheckGate, Note, NOT_MEASURED, qualityBadgeClass, Row } from './shared';

const TEAL = '#1F96AA';

type KneeState =
  | { kind: 'no-ramp' }
  | { kind: 'knee'; knee: number; breach: DiagSfuStep }
  | { kind: 'all-healthy'; maxClients: number }
  | { kind: 'first-tier-breached'; step: DiagSfuStep };

/**
 * Resolve `knee` against the ramp so a null knee is never ambiguous.
 *
 * Loss plays no part: it is not measured, so rtt against `target_rtt_ms` is the
 * whole test, exactly as `find_knee` computed it.
 */
function classifyKnee(result: DiagSfuResult): KneeState {
  const { ramp, knee, target_rtt_ms } = result;
  if (ramp.length === 0) return { kind: 'no-ramp' };
  const breach = ramp.find((s) => s.rtt_ms > target_rtt_ms);
  if (knee !== null) {
    return { kind: 'knee', knee, breach: breach ?? ramp[ramp.length - 1] };
  }
  if (breach === undefined) {
    return { kind: 'all-healthy', maxClients: Math.max(...ramp.map((s) => s.clients)) };
  }
  return { kind: 'first-tier-breached', step: breach };
}

export default function LoadTab({
  check,
}: {
  check: DiagnosticCheckResult<DiagSfuResult> | undefined;
}) {
  return (
    <CheckGate
      check={check}
      label="SFU load"
      missingHint="Tick “SFU load” above and run the checks again. It opens real connections, so it is billed by your LiveKit deployment."
    >
      {(result) => (
        <>
          <RampCard result={result} />
          <SaturationCard resource={result.resource} ramp={result.ramp} />
        </>
      )}
    </CheckGate>
  );
}

function KneeBanner({ state, target }: { state: KneeState; target: number }) {
  if (state.kind === 'no-ramp') {
    return (
      <Note>
        This run measured the baseline but no ramp, so there is no capacity curve and no knee to
        report. Nothing is inferred from an absent measurement.
      </Note>
    );
  }
  if (state.kind === 'knee') {
    return (
      <Note tone="warn">
        Knee at {state.knee} clients: every tier up to {state.knee} stayed inside the{' '}
        {target.toFixed(0)} ms budget, and {state.breach.clients} clients broke it at{' '}
        {state.breach.rtt_ms.toFixed(1)} ms ({state.breach.quality}).
      </Note>
    );
  }
  if (state.kind === 'all-healthy') {
    return (
      <Note tone="good">
        No knee inside this ramp: every tier up to {state.maxClients} clients stayed under the{' '}
        {target.toFixed(0)} ms budget. That is a floor on capacity, not a ceiling — the ramp
        stopped at {state.maxClients}, it did not find a limit.
      </Note>
    );
  }
  return (
    <Note tone="bad">
      No healthy tier in this ramp: the smallest tier ({state.step.clients} clients) already
      exceeded the {target.toFixed(0)} ms budget at {state.step.rtt_ms.toFixed(1)} ms (
      {state.step.quality}). There is no knee because nothing passed, which is the opposite of a
      clean ramp.
    </Note>
  );
}

function RampCard({ result }: { result: DiagSfuResult }) {
  const state = classifyKnee(result);
  const data = result.ramp.map((s) => ({
    clients: s.clients,
    rtt: Number(s.rtt_ms.toFixed(1)),
  }));
  const knee = state.kind === 'knee' ? state.knee : null;

  return (
    <Card
      label="Client ramp (round-trip vs concurrent clients)"
      right={
        <span className="neo-badge neo-badge--blue">
          {result.ramp.length} {result.ramp.length === 1 ? 'tier' : 'tiers'}
        </span>
      }
    >
      <KneeBanner state={state} target={result.target_rtt_ms} />

      {data.length > 0 && (
        <>
          <div style={{ width: '100%', height: 250, marginTop: 16 }}>
            <ResponsiveContainer>
              <LineChart data={data} margin={{ top: 16, right: 16, left: 0, bottom: 8 }}>
                <CartesianGrid strokeDasharray="4 4" stroke="var(--vg-hairline-2)" />
                <XAxis
                  dataKey="clients"
                  type="number"
                  domain={[0, 'dataMax']}
                  ticks={result.ramp.map((s) => s.clients)}
                  tick={{ fontSize: 11, fontWeight: 600 }}
                  label={{ value: 'concurrent clients', position: 'insideBottom', offset: -6, fontSize: 11 }}
                />
                <YAxis
                  tick={{ fontSize: 11, fontWeight: 600 }}
                  width={48}
                  tickFormatter={(v) => `${v}`}
                />
                <Tooltip content={<NeoTooltip />} cursor={{ stroke: TEAL, strokeWidth: 1 }} />
                <ReferenceLine
                  y={result.target_rtt_ms}
                  stroke="var(--vg-amber)"
                  strokeDasharray="5 4"
                  label={{
                    value: `${result.target_rtt_ms.toFixed(0)} ms budget`,
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
                  name="Round trip (ms)"
                  stroke={TEAL}
                  strokeWidth={2}
                  dot={{ r: 3, fill: TEAL }}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>

          <table className="neo-table neo-table--blue" style={{ marginTop: 8 }}>
            <thead>
              <tr>
                <th>Clients</th>
                <th>Round trip</th>
                <th>Quality</th>
                <th>Against budget</th>
              </tr>
            </thead>
            <tbody>
              {result.ramp.map((s) => {
                const over = s.rtt_ms > result.target_rtt_ms;
                return (
                  <tr key={s.clients}>
                    <td className="mono" style={{ fontWeight: 700 }}>{s.clients}</td>
                    <td className="mono">{s.rtt_ms.toFixed(1)} ms</td>
                    <td>
                      <span className={`neo-badge ${qualityBadgeClass(s.quality)}`}>{s.quality}</span>
                    </td>
                    <td>
                      <span className={`neo-badge ${over ? 'neo-badge--red' : 'neo-badge--green'}`}>
                        {over ? 'over' : 'within'}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>

          <div style={{ marginTop: 10 }}>
            <Note>
              Round trip and quality are the only measured columns. Packet loss is {NOT_MEASURED} at
              every tier, so no loss series is plotted and no loss column is shown.
            </Note>
          </div>
        </>
      )}
    </Card>
  );
}

function SaturationCard({
  resource,
  ramp,
}: {
  resource: DiagSfuResult['resource'];
  ramp: DiagSfuStep[];
}) {
  if (resource === null) {
    return (
      <Card label="Prober host during the ramp">
        <Note>
          The prober’s own CPU, memory and uplink are sampled only while the load ramp runs, so
          this run has no host sample to attribute its numbers with.
        </Note>
      </Card>
    );
  }

  const cpu = resource.cpu_peak;
  const perCpu = resource.per_client.cpu_pct;
  const perKbps = resource.per_client.kbps_up;
  const topTier = ramp.length > 0 ? Math.max(...ramp.map((s) => s.clients)) : null;

  return (
    <Card
      label="Prober host during the ramp"
      right={
        <span
          className={`neo-badge ${
            resource.saturated === true
              ? 'neo-badge--red'
              : resource.saturated === false
                ? 'neo-badge--green'
                : 'neo-badge--black'
          }`}
        >
          {resource.saturated === true
            ? 'saturated'
            : resource.saturated === false
              ? 'not saturated'
              : 'saturation unknown'}
        </span>
      }
    >
      {resource.saturated === true ? (
        <Note tone="bad">
          This host saturated during the ramp (peak CPU {cpu?.toFixed(1)}%). The curve above
          describes THIS machine, not your SFU: re-run from a larger host, or with lower tiers,
          before treating the knee as a server limit.
        </Note>
      ) : resource.saturated === false ? (
        <Note tone="good">
          This host did not saturate (peak CPU {cpu?.toFixed(1)}%), so the ramp above is a
          measurement of the SFU rather than of the prober.
        </Note>
      ) : (
        <Note tone="warn">
          Saturation is unknown for this run: no usable CPU sample was taken, so we cannot say
          whether the prober or the SFU was the limit. It is not reported as “not saturated”,
          because nobody measured that.
        </Note>
      )}

      <div style={{ marginTop: 14 }}>
        <Row
          label="Peak CPU on this host"
          value={cpu == null ? NOT_MEASURED : `${cpu.toFixed(1)}%`}
          muted={cpu == null}
        />
        <Row
          label="Peak memory (RSS)"
          value={
            resource.mem_peak_mb == null ? NOT_MEASURED : `${resource.mem_peak_mb.toFixed(0)} MB`
          }
          muted={resource.mem_peak_mb == null}
        />
        <Row
          label="Uplink during the ramp"
          value={
            resource.net_kbps_up == null
              ? NOT_MEASURED
              : `${resource.net_kbps_up.toFixed(0)} kbps`
          }
          muted={resource.net_kbps_up == null}
        />
        <Row
          label={`CPU per client${topTier ? ` (at ${topTier} clients)` : ''}`}
          value={perCpu == null ? NOT_MEASURED : `${perCpu.toFixed(2)}%`}
          muted={perCpu == null}
        />
        <Row
          label="Uplink per client"
          value={perKbps == null ? NOT_MEASURED : `${perKbps.toFixed(1)} kbps`}
          muted={perKbps == null}
        />
        <Row
          label="Clients this host sustains"
          value={
            resource.sustainable_n == null ? NOT_MEASURED : `~${resource.sustainable_n} before CPU-bound`
          }
          muted={resource.sustainable_n == null}
        />
      </div>
    </Card>
  );
}
