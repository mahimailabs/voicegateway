import { useCallback, useEffect, useState } from 'react';
import type { MouseEvent } from 'react';
import { Link } from 'react-router-dom';
import { ApiError, PROBE_COOLDOWN_SECONDS, probeAgent } from '../lib/api';
import { attemptedProbes } from '../lib/autoProbe';
import type { AgentProbeResult, AgentRow } from '../lib/types';
import {
  agentStatus,
  agentStatusBadgeClass,
  formatCost,
  rosterStatusBadge,
} from '../lib/ui';
import LatencyWaterfall from './LatencyWaterfall';
import ProbeSample from './ProbeSample';
import ResourceMeter from './ResourceMeter';

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

// A gauge/meter glyph for the compute + memory toggle: a dial with a needle.
function GaugeIcon() {
  return (
    <svg width="11" height="11" viewBox="0 0 12 12" aria-hidden="true" focusable="false">
      <path d="M1.5 9a4.5 4.5 0 1 1 9 0" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
      <path d="M6 9l2.4-2.4" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
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
  // Compute + memory are a free heartbeat read, so show them by default rather
  // than behind a click; the gauge button collapses them.
  const [showResources, setShowResources] = useState(true);
  const [running, setRunning] = useState(false);
  const [sample, setSample] = useState<AgentProbeResult | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  // True only when a probe was actually placed and did NOT complete (a timeout /
  // crash), as opposed to a refusal (429 cooldown, 409 in-flight, 400 ineligible)
  // that placed no call. A real failure must not leave a stale earlier success on
  // screen; a refusal leaves the prior cached measurement standing.
  const [probeFailed, setProbeFailed] = useState(false);
  const [cooldownUntil, setCooldownUntil] = useState(0);
  const [nowMs, setNowMs] = useState(() => Date.now());

  // What the card shows for latency: a fresh probe result if one was just placed,
  // else the agent's last cached probe (rendered on load so the split is visible
  // without pressing play). A real failed attempt suppresses the cache so a stale
  // success never sits beneath a red "it failed" line.
  const cached = cachedProbeSample(agent);
  const displayedSample = sample ?? (probeFailed ? null : cached);

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

  // The core call, shared by the play button and the on-load auto-run. Splitting
  // it off the click handler lets the auto-run place a probe without a synthetic
  // event, while both go through the exact same guards and cooldown handling.
  const placeProbe = useCallback(async () => {
    if (running || coolingSeconds > 0) return;
    setRunning(true);
    setFailure(null);
    setProbeFailed(false);
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
      // A refusal (429 cooldown, 409 in-flight, 400 ineligible) placed no call,
      // so the prior cached measurement still stands and is kept on screen. Any
      // other error means the call was attempted and did not complete, so the
      // stale success is suppressed.
      const refusedNoCall =
        err instanceof ApiError && [400, 409, 429].includes(err.status);
      setProbeFailed(!refusedNoCall);
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
  }, [agent.agent_id, coolingSeconds, running]);

  const runProbe = useCallback(
    (e: MouseEvent<HTMLButtonElement>) => {
      // The button sits inside a card whose regions are links; stop the press
      // from also navigating away from the result it is about to produce.
      e.preventDefault();
      e.stopPropagation();
      void placeProbe();
    },
    [placeProbe],
  );

  // On load, run one probe for an eligible agent that has no cached latency yet,
  // so the split is populated on visit ("run once, then cache") rather than left
  // blank until someone presses play. `attemptedProbes` is module scope, so
  // navigating away and back does not re-bill, and it is shared with the Agents
  // page: whichever surface renders the agent first places the single call. Once
  // cached, `latency_probe` is set and this is skipped; play re-runs on demand.
  useEffect(() => {
    if (!probe?.eligible || agent.latency_probe || attemptedProbes.has(agent.agent_id)) {
      return;
    }
    attemptedProbes.add(agent.agent_id);
    void placeProbe();
  }, [agent.agent_id, agent.latency_probe, probe?.eligible, placeProbe]);

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
          {/* Compute + memory toggle. Reads the live heartbeat sample already in
              the fleet index (no extra call); expands the readout below the card. */}
          <button
            type="button"
            className="neo-btn neo-btn--sm"
            onClick={() => setShowResources((v) => !v)}
            aria-pressed={showResources}
            aria-label={`Show compute and memory for ${agent.agent_name || agent.agent_id}`}
            title="Compute & memory this agent is using"
          >
            <GaugeIcon />
          </button>
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

      {showResources && (
        <div
          style={{
            marginTop: 12,
            paddingTop: 10,
            borderTop: '1px dashed var(--vg-hairline)',
          }}
        >
          <ResourceMeter resources={agent.resources} />
        </div>
      )}

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
      {displayedSample && <ProbeSample result={displayedSample} />}
    </div>
  );
}

// Rebuild a full probe result from the card's cached `latency_probe` so
// ProbeSample can render it verbatim. The cache carries the measured legs plus
// the mode/dispatch_name the probe ran with; the fields ProbeSample does not
// read (room, trials) are filled with honest placeholders.
function cachedProbeSample(agent: AgentRow): AgentProbeResult | null {
  const lp = agent.latency_probe;
  if (!lp) return null;
  return {
    agent_id: agent.agent_id,
    dispatch_name: lp.dispatch_name ?? agent.probe?.dispatch_name ?? '',
    mode: lp.mode ?? agent.probe?.mode ?? 'explicit',
    room: null,
    trials: 0,
    e2e: lp.e2e,
    components: lp.components,
    cost_usd: lp.cost_usd,
    models: lp.models ?? { stt: null, llm: null, tts: null },
    error: lp.error,
  };
}
