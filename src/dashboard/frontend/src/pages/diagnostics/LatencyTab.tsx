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
//
// An agent that answered nothing has two distinct states, and this tab keeps them
// apart: the probe recorded a reason (a dispatch that reached no worker, a
// connect that failed), or it recorded none and the caller simply got no reply.
// The first is printed verbatim; the second says so. An empty reason is never
// shown as an explanation.

import type { CSSProperties } from 'react';
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

/**
 * The reason the probe recorded for this agent, or null when it recorded none.
 *
 * Absent, null, and blank all collapse to null on purpose. "No reply and nobody
 * said why" is a real state and must read as an absence; a whitespace-only
 * string rendered as an explanation would claim a cause that is not there.
 *
 * Exported because the Errors tab groups the same failure and must read the
 * field the same way: one definition, so the two surfaces cannot drift.
 */
export function probeReason(agent: DiagLatencyAgent): string | null {
  const reason = agent.error?.trim();
  return reason ? reason : null;
}

/**
 * Remote text, so it is rendered as text and boxed.
 *
 * `overflowWrap: anywhere` is load-bearing: a provider message can be one long
 * unbroken token (a URL, a base64 body) which would otherwise push the card
 * wider than the page. The height cap keeps an unbounded message from pushing
 * everything below it off-screen; it scrolls instead of truncating, because
 * cutting an error string loses the part an operator needs.
 */
const REASON_BOX: CSSProperties = {
  fontSize: 12,
  lineHeight: 1.5,
  color: 'var(--vg-ink)',
  background: 'var(--vg-red-tint)',
  border: '1px solid var(--vg-red)',
  borderRadius: 'var(--vg-radius-xs)',
  padding: '8px 10px',
  whiteSpace: 'pre-wrap',
  overflowWrap: 'anywhere',
  wordBreak: 'break-word',
  maxHeight: 160,
  overflowY: 'auto',
};

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
  const reason = probeReason(agent);
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
        <>
          <Note tone="bad">
            Nothing was measured for this agent: no trial produced a reply. The end-to-end time is
            reported as {NOT_MEASURED} rather than as a zero, which would read as an instant answer.
          </Note>
          <div style={{ marginTop: 12 }}>
            {reason === null ? (
              // No reason recorded is its own state, not a blank one: say so in
              // the same words the CLI uses when it has nothing to print either.
              <Note>
                The probe recorded no reason for it, so this is a bare “no reply”: the synthetic
                caller heard nothing back and nothing said why. The cause is {NOT_MEASURED}, not
                empty.
              </Note>
            ) : (
              <>
                <div style={{ fontSize: 12, color: 'var(--vg-muted)', marginBottom: 5 }}>
                  Reason the probe recorded, verbatim
                </div>
                {/* Plain text child: React escapes it, and it is never markup. */}
                <div className="mono" style={REASON_BOX}>
                  {reason}
                </div>
              </>
            )}
          </div>
        </>
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
