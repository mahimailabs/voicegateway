// Conversation state pane: agent state at the current playhead.
// Implements REQ-VG-REPLAY-005.
//
// v0.3.0 minimal: render the most-recent state snapshot's payload
// in full (system prompt, message history, tool-in-flight,
// structured outputs). The Foundry's "walk the most recent
// snapshot plus subsequent message diffs to reconstruct" is the
// long-term plan; the snapshot-only rendering is correct in the
// boundary cases (snapshot happens at every message-add per T03)
// and a v0.3.x follow-up can add diff-walk for non-message
// inflection points.

import type { ReplayEvent } from '../../lib/types';

interface Props {
  events: ReplayEvent[];
}

export default function ConversationStatePane({ events }: Props) {
  if (events.length === 0) {
    return (
      <div className="neo-card neo-card--strip-red">
        <div className="label">Conversation state</div>
        <div className="empty-state mt-md">
          No state snapshots captured yet.
        </div>
      </div>
    );
  }

  // Most recent state snapshot before playhead.
  const latest = events[events.length - 1];
  const systemPrompt = String(latest.payload.system_prompt ?? '');
  const messageHistory = (latest.payload.message_history as
    | Array<Record<string, unknown>>
    | undefined) ?? [];
  const toolInFlight = latest.payload.tool_call_in_flight as
    | Record<string, unknown>
    | null
    | undefined;
  const structured = latest.payload.structured_output_collected as
    | Record<string, unknown>
    | null
    | undefined;

  return (
    <div className="neo-card neo-card--strip-red">
      <div className="label">
        Conversation state at {(latest.t_ms / 1000).toFixed(2)}s
      </div>
      <div
        className="mt-md"
        style={{
          maxHeight: 220,
          overflowY: 'auto',
          fontSize: 12,
          display: 'flex',
          flexDirection: 'column',
          gap: 8,
        }}
      >
        {systemPrompt && (
          <div>
            <div className="label" style={{ fontSize: 10 }}>
              System prompt
            </div>
            <div
              className="mono mt-sm"
              style={{ opacity: 0.85, whiteSpace: 'pre-wrap' }}
            >
              {systemPrompt}
            </div>
          </div>
        )}
        <div>
          <div className="label" style={{ fontSize: 10 }}>
            Messages ({messageHistory.length})
          </div>
          <div
            className="mono mt-sm"
            style={{ opacity: 0.85, whiteSpace: 'pre-wrap' }}
          >
            {messageHistory.map((m, i) => `${m.role ?? '?'}: ${m.content ?? ''}`).join('\n')}
          </div>
        </div>
        {toolInFlight && (
          <div>
            <div className="label" style={{ fontSize: 10 }}>
              Tool call in flight
            </div>
            <div
              className="mono mt-sm"
              style={{ opacity: 0.85, whiteSpace: 'pre-wrap' }}
            >
              {JSON.stringify(toolInFlight, null, 2)}
            </div>
          </div>
        )}
        {structured && (
          <div>
            <div className="label" style={{ fontSize: 10 }}>
              Structured output
            </div>
            <div
              className="mono mt-sm"
              style={{ opacity: 0.85, whiteSpace: 'pre-wrap' }}
            >
              {JSON.stringify(structured, null, 2)}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
