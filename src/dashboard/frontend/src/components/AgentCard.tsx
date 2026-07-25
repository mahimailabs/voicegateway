import { useCallback, useEffect, useState } from 'react';
import type { MouseEvent } from 'react';
import { Link } from 'react-router-dom';
import { ApiError, PROBE_COOLDOWN_SECONDS, probeAgent } from '../lib/api';
import type { AgentProbeResult, AgentRow } from '../lib/types';
import {
  agentStatus,
  agentStatusBadgeClass,
  formatCost,
  rosterStatusBadge,
} from '../lib/ui';
import LatencyWaterfall from './LatencyWaterfall';
import ProbeSample from './ProbeSample';

// A single agent tile for the Overview fleet grid: identity + presence, the
// model stack, 24h cost/requests, and the STT/LLM/TTS latency waterfall. The
// name and the body both link to the agent's detail page; the play button sits
// between them as a real button, not nested inside a link.

function PlayIcon() {
  return (
    <svg width="9" height="10" viewBox="0 0 9 10" aria-hidden="true" focusable="false">
      <path d="M1 0.5l7 4.5-7 4.5z" fill="currentColor" />
    </svg>
  );
}

function Spinner() {
  return (
    <svg width="11" height="11" viewBox="0 0 12 12" className="vg-spin" aria-hidden="true" focusable="false">
      <circle cx="6" cy="6" r="4.5" fill="none" stroke="currentColor" strokeOpacity="0.25" strokeWidth="1.6" />
      <path d="M6 1.5a4.5 4.5 0 0 1 4.5 4.5" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  );
}

export default function AgentCard({ agent }: { agent: AgentRow }) {
  // Live roster status (idle/busy/offline) when present, matching Server > Fleet;
  // else the telemetry-recency status.
  const fleet = agent.fleet_status || null;
  const status = fleet ?? agentStatus(agent.last_seen);
  const badgeClass = fleet
    ? rosterStatusBadge(fleet)
    : agentStatusBadgeClass(agentStatus(agent.last_seen));

  const probe = agent.probe ?? null;
  const [running, setRunning] = useState(false);
  const [sample, setSample] = useState<AgentProbeResult | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [cooldownUntil, setCooldownUntil] = useState(0);
  const [nowMs, setNowMs] = useState(() => Date.now());

  // Tick once a second only while a cooldown is actually counting down, and stop
  // the timer the moment it lapses: an always-on interval per card would re-render
  // the whole fleet grid forever.
  useEffect(() => {
    if (cooldownUntil <= Date.now()) return;
    const id = window.setInterval(() => {
      setNowMs(Date.now());
      if (Date.now() >= cooldownUntil) window.clearInterval(id);
    }, 1000);
    return () => window.clearInterval(id);
  }, [cooldownUntil]);

  const coolingSeconds = Math.max(0, Math.ceil((cooldownUntil - nowMs) / 1000));

  const runProbe = useCallback(
    async (e: MouseEvent<HTMLButtonElement>) => {
      // The button sits inside a card whose regions are links; stop the press
      // from also navigating away from the result it is about to produce.
      e.preventDefault();
      e.stopPropagation();
      if (running || coolingSeconds > 0) return;
      setRunning(true);
      setFailure(null);
      try {
        setSample(await probeAgent(agent.agent_id));
        // Only a returned result means a call was actually placed, which is what
        // the cooldown is about. A refusal (no observed job, one already in
        // flight, the server's own cooldown) placed nothing, so it must not
        // start a local timer on top of the server's.
        setCooldownUntil(Date.now() + PROBE_COOLDOWN_SECONDS * 1000);
      } catch (err) {
        setSample(null);
        setFailure(err instanceof Error ? err.message : String(err));
        // A refusal placed no call, so it earns no cooldown of its own. But a
        // 429 means the SERVER is still counting one down (a reload drops the
        // local timer while the server's keeps running), and it says how long
        // in Retry-After. Adopting that leaves the button honest about being
        // unavailable instead of re-offering a press already decided against.
        if (err instanceof ApiError && err.retryAfterSeconds !== null) {
          setCooldownUntil(Date.now() + err.retryAfterSeconds * 1000);
        }
      } finally {
        setRunning(false);
      }
    },
    [agent.agent_id, coolingSeconds, running],
  );

  const disabled = !probe?.eligible || running || coolingSeconds > 0;
  const buttonTitle = !probe
    ? 'this server does not report probe eligibility'
    : !probe.eligible
      ? (probe.reason ?? 'this agent cannot be probed')
      : running
        ? 'placing one real call through this agent'
        : coolingSeconds > 0
          ? `a probe places a billed call: wait ${coolingSeconds}s`
          : [
              "Place one real call through this agent's cascade and measure it.",
              'It is billed like any other call.',
              probe.reason ?? '',
            ]
              .filter(Boolean)
              .join(' ');

  return (
    <div className="vg-card">
      <div className="flex-row" style={{ justifyContent: 'space-between', alignItems: 'flex-start', gap: 8 }}>
        <Link
          to={`/agents/${encodeURIComponent(agent.agent_id)}`}
          className="mono"
          style={{
            fontWeight: 700,
            color: 'var(--vg-ink)',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
          title={agent.agent_id}
        >
          {agent.agent_name || agent.agent_id}
        </Link>
        <div className="flex-row" style={{ gap: 6, alignItems: 'center', flexShrink: 0 }}>
          {/* A disabled button receives no pointer events, so a title on the
              button itself never renders a tooltip: the one state that most
              needs to explain itself would be a dead control with no reason.
              The wrapper span stays enabled and carries the title in both
              states. */}
          <span style={{ display: 'inline-flex' }} title={buttonTitle}>
            <button
              type="button"
              className="neo-btn neo-btn--sm vg-probe-btn"
              onClick={runProbe}
              disabled={disabled}
              aria-label={`Probe ${agent.agent_name || agent.agent_id} with one real call: ${buttonTitle}`}
            >
              {running ? <Spinner /> : coolingSeconds > 0 ? `${coolingSeconds}s` : <PlayIcon />}
            </button>
          </span>
          <span className={`neo-badge ${badgeClass}`}>{status}</span>
        </div>
      </div>

      {/* The body is a second link to the same page so the card stays clickable
          anywhere. tabIndex=-1 keeps it out of the tab order (the name link
          already covers that) without hiding the numbers from a screen reader. */}
      <Link
        to={`/agents/${encodeURIComponent(agent.agent_id)}`}
        style={{ display: 'block' }}
        tabIndex={-1}
      >
        <div className="flex-row gap-sm mt-sm flex-wrap">
          {(['stt', 'llm', 'tts'] as const).map((m) => {
            const model = agent.models?.[m] ?? null;
            return (
              <span
                key={m}
                className="neo-badge neo-badge--black"
                title={model ? `${m.toUpperCase()}: ${model}` : `${m.toUpperCase()}: unknown`}
                style={{ opacity: model ? 1 : 0.35, fontSize: 10 }}
              >
                {model ? model.split('/').pop() : '-'}
              </span>
            );
          })}
        </div>

        <div className="flex-row mt-md" style={{ justifyContent: 'space-between', gap: 12 }}>
          <div>
            <div className="vg-card__label">Cost 24h</div>
            <div className="mono" style={{ fontWeight: 700, marginTop: 2 }}>
              {formatCost(agent.total_cost_usd, 4)}
            </div>
          </div>
          <div>
            <div className="vg-card__label">Requests</div>
            <div className="mono" style={{ fontWeight: 700, marginTop: 2 }}>
              {agent.request_count}
            </div>
          </div>
        </div>

        <div className="mt-md">
          <LatencyWaterfall latency={agent.latency_ms} models={agent.models} />
        </div>
      </Link>

      {/* Why the play button is dead, on-screen rather than on hover. The
          wrapper span above carries the same text in a title, but a control
          that ignores clicks and only explains itself to a mouse that stops
          on it reads as broken to the operator who just pressed it. Rendered
          only for the ineligible state: "running" and the cooldown countdown
          are already legible from the button itself. */}
      {probe && !probe.eligible && (
        <div
          style={{
            marginTop: 10,
            fontSize: 10,
            lineHeight: 1.35,
            color: 'var(--vg-muted)',
          }}
        >
          {probe.reason ?? 'this agent cannot be probed'}
        </div>
      )}
      {failure && (
        <div
          style={{
            marginTop: 12,
            paddingTop: 10,
            borderTop: '1px dashed var(--vg-hairline)',
            fontSize: 11,
            color: 'var(--vg-red)',
          }}
        >
          {failure}
        </div>
      )}
      {sample && <ProbeSample result={sample} />}
    </div>
  );
}
