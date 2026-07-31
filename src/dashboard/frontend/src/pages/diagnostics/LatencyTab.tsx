// Diagnostics > Latency: real calls placed to every agent found in a room.
//
// The headline is deliberately "max of N", not "p95". A diagnostics run places at
// most MAX_LATENCY_TRIALS = 3 calls per agent, and a percentile computed from
// three samples is not a percentile: it is the max wearing a more confident
// label. The backend does return `p95` (from `summarize`), and this tab does not
// render it.
//
// The per-leg split is only available when the probed agent writes its telemetry
// to THIS host: it is read back out of the rows the agent itself wrote for the
// probe room. An agent reporting to a remote collector says so, rather than
// showing zeros.

import LatencyWaterfall from '../../components/LatencyWaterfall';
import type {
  DiagLatencyAgent,
  DiagLatencyResult,
  DiagnosticCheckResult,
} from '../../lib/types';
import { Card, CheckGate, Note, NOT_MEASURED, Row } from './shared';

/** Percentiles need at least 10 samples; below that we show "max of N". */
const MIN_SAMPLES_FOR_PERCENTILE = 10;

/** Probe times come back in seconds; the rest of the dashboard speaks ms. */
function toMs(seconds: number | undefined): number | null {
  return seconds == null ? null : seconds * 1000;
}

export default function LatencyTab({
  check,
}: {
  check: DiagnosticCheckResult<DiagLatencyResult> | undefined;
}) {
  return (
    <CheckGate
      check={check}
      label="Latency (real calls)"
      missingHint="Tick “Latency (real calls)” above and run the checks again. Every trial is a real, billed call through the agent’s own providers."
    >
      {(result) =>
        result.agents.length === 0 ? (
          <Card label="Latency (real calls)">
            <Note>
              No agent was in a room to call, so no latency was measured. The run probes the
              agents the LiveKit server reported; it never invents a target to dial.
            </Note>
          </Card>
        ) : (
          <>
            {result.agents.map((a) => (
              <AgentCard key={a.agent} agent={a} />
            ))}
          </>
        )
      }
    </CheckGate>
  );
}

function AgentCard({ agent }: { agent: DiagLatencyAgent }) {
  const { stats, components } = agent;
  const measured = stats.trials > 0;
  const split = {
    stt: toMs(components?.stt),
    llm: toMs(components?.llm_ttft),
    tts: toMs(components?.tts),
  };

  return (
    <Card
      label={`Reply latency · ${agent.agent}`}
      right={
        // `stats.trials` counts the trials that ANSWERED, not the trials placed
        // (the payload does not carry the attempted count), so the badge states
        // successes without implying a denominator it cannot know.
        <span className={`neo-badge ${measured ? 'neo-badge--blue' : 'neo-badge--red'}`}>
          {measured
            ? `${stats.trials} ${stats.trials === 1 ? 'trial' : 'trials'} answered`
            : 'no trial answered'}
        </span>
      }
    >
      {!measured ? (
        <Note tone="bad">
          Nothing was measured for this agent: no trial produced a reply. The end-to-end time is
          reported as {NOT_MEASURED} rather than as a zero, which would read as an instant answer.
        </Note>
      ) : (
        <>
          <div className="flex-row" style={{ gap: 28, flexWrap: 'wrap', alignItems: 'flex-end' }}>
            <div>
              <div className="mono" style={{ fontSize: 30, fontWeight: 800, lineHeight: 1.1 }}>
                {Math.round(stats.max * 1000)}
                <span style={{ fontSize: 14, fontWeight: 600, color: 'var(--vg-muted)' }}> ms</span>
              </div>
              <div style={{ fontSize: 12, color: 'var(--vg-muted)', marginTop: 4 }}>
                max of {stats.trials} — slowest reply, end to end
              </div>
            </div>
            <div style={{ minWidth: 220, flex: 1 }}>
              <Row label="Average reply" value={`${Math.round(stats.avg * 1000)} ms`} />
              <Row label="Fastest reply" value={`${Math.round(stats.min * 1000)} ms`} />
              <Row
                label="Turn detection (end of utterance)"
                value={
                  components?.eou == null
                    ? NOT_MEASURED
                    : `${Math.round(components.eou * 1000)} ms`
                }
                muted={components?.eou == null}
              />
            </div>
          </div>

          <div style={{ marginTop: 16 }}>
            <LatencyWaterfall
              latency={split}
              label="First-byte split, measured on these calls"
              emptyText="Split not measured: this agent does not write its telemetry to this host, so the STT/LLM/TTS legs were never recorded here."
            />
          </div>

          <div style={{ marginTop: 12 }}>
            <Note>
              {stats.trials} {stats.trials === 1 ? 'sample is' : 'samples are'} below the{' '}
              {MIN_SAMPLES_FOR_PERCENTILE} a percentile needs, so the headline is the max of{' '}
              {stats.trials} rather than a p95. A run places at most 3 calls per agent, because
              each one is billed.
            </Note>
          </div>
        </>
      )}
    </Card>
  );
}
