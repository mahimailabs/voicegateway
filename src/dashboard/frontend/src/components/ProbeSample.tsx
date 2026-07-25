import type { AgentProbeResult } from '../lib/types';
import { formatCost } from '../lib/ui';
import LatencyWaterfall from './LatencyWaterfall';

// The result of one press of an agent card's play button: a single real call
// placed through that agent's real cascade.
//
// It renders BESIDE the card's 24h average, never over it. One call is a
// sample, not a trend, and the two answer different questions. Every number
// here was measured on that call; anything that could not be measured says so
// in words instead of showing a zero.

/** Probe times come back in seconds; the rest of the UI speaks milliseconds. */
function toMs(seconds: number | undefined): number | null {
  return seconds == null ? null : seconds * 1000;
}

const NOT_MEASURED = 'not measured';

function Row({ label, value, muted }: { label: string; value: string; muted?: boolean }) {
  return (
    <div className="flex-row" style={{ justifyContent: 'space-between', alignItems: 'baseline', gap: 8 }}>
      <span style={{ fontSize: 11, color: 'var(--vg-muted)' }}>{label}</span>
      <span
        className="mono"
        style={{
          fontSize: 12,
          fontWeight: 700,
          color: muted ? 'var(--vg-muted-2)' : 'var(--vg-ink)',
        }}
      >
        {value}
      </span>
    </div>
  );
}

export default function ProbeSample({ result }: { result: AgentProbeResult }) {
  const c = result.components;
  const split = {
    stt: toMs(c?.stt),
    llm: toMs(c?.llm_ttft),
    tts: toMs(c?.tts),
  };
  const unmeasured = (['stt', 'llm', 'tts'] as const).filter((k) => split[k] == null);

  return (
    <div
      style={{
        marginTop: 14,
        paddingTop: 12,
        borderTop: '1px dashed var(--vg-hairline)',
      }}
    >
      <div className="flex-row" style={{ justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
        <span className="vg-card__label" style={{ fontSize: 11 }}>
          Sample probe
        </span>
        <span
          className="neo-badge neo-badge--blue"
          style={{ fontSize: 10 }}
          title={
            result.mode === 'explicit'
              ? `dispatched to ${result.dispatch_name}`
              : 'automatic dispatch: the room itself was the dispatch'
          }
        >
          {result.mode}
        </span>
      </div>

      {result.error ? (
        <div style={{ fontSize: 11, color: 'var(--vg-red)', marginTop: 8 }}>
          The call did not complete: {result.error}
        </div>
      ) : (
        <>
          <div style={{ marginTop: 8 }}>
            <Row
              label="Reply, end to end"
              value={result.e2e ? `${Math.round(result.e2e.avg * 1000)}ms` : NOT_MEASURED}
              muted={!result.e2e}
            />
          </div>

          <div style={{ marginTop: 10 }}>
            <LatencyWaterfall
              latency={split}
              models={result.models}
              label="This call's first-byte split"
              emptyText="Split not measured: this agent does not write telemetry to this host"
            />
          </div>

          {c != null && unmeasured.length > 0 && (
            <div style={{ fontSize: 11, color: 'var(--vg-muted-2)', marginTop: 6 }}>
              {unmeasured.map((k) => k.toUpperCase()).join(', ')} not measured on this call
            </div>
          )}

          <div style={{ marginTop: 10 }}>
            <Row
              label="Cost of this call"
              value={result.cost_usd == null ? NOT_MEASURED : formatCost(result.cost_usd, 4)}
              muted={result.cost_usd == null}
            />
          </div>
        </>
      )}
    </div>
  );
}
