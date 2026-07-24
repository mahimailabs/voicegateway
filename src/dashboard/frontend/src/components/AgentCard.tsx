import { Link } from 'react-router-dom';
import type { AgentRow } from '../lib/types';
import {
  agentStatus,
  agentStatusBadgeClass,
  formatCost,
  rosterStatusBadge,
} from '../lib/ui';
import LatencyWaterfall from './LatencyWaterfall';

// A single agent tile for the Overview fleet grid: identity + presence, the
// model stack, 24h cost/requests, and the STT/LLM/TTS latency waterfall. The
// whole card links to the agent's detail page.
export default function AgentCard({ agent }: { agent: AgentRow }) {
  // Live roster status (idle/busy/offline) when present, matching Server > Fleet;
  // else the telemetry-recency status.
  const fleet = agent.fleet_status || null;
  const status = fleet ?? agentStatus(agent.last_seen);
  const badgeClass = fleet
    ? rosterStatusBadge(fleet)
    : agentStatusBadgeClass(agentStatus(agent.last_seen));

  return (
    <Link
      to={`/agents/${encodeURIComponent(agent.agent_id)}`}
      className="vg-card"
      style={{ display: 'block' }}
    >
      <div className="flex-row" style={{ justifyContent: 'space-between', alignItems: 'flex-start', gap: 8 }}>
        <div
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
        </div>
        <span className={`neo-badge ${badgeClass}`}>{status}</span>
      </div>

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
        <LatencyWaterfall latency={agent.latency_ms} />
      </div>
    </Link>
  );
}
