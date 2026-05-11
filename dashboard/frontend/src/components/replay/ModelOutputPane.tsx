// Model output pane: LLM tokens up to playhead.
// Implements REQ-VG-REPLAY-003 AC-2.
//
// v0.3.0 minimal: tokens render in arrival order, concatenated for
// readability. Tool-invoke tokens are surfaced inline as a labeled
// span. The Foundry's "character-by-character pacing" is preserved
// in storage (each token is its own row with a t_ms); the visual
// pacing belongs in a future enhancement that animates tokens in.

import type { ReplayEvent } from '../../lib/types';

interface Props {
  events: ReplayEvent[];
}

export default function ModelOutputPane({ events }: Props) {
  if (events.length === 0) {
    return (
      <div className="neo-card neo-card--strip-blue">
        <div className="label">Model output (LLM)</div>
        <div className="empty-state mt-md">No model output captured yet.</div>
      </div>
    );
  }

  return (
    <div className="neo-card neo-card--strip-blue">
      <div className="label">Model output (LLM)</div>
      <div
        className="mt-md"
        style={{
          maxHeight: 220,
          overflowY: 'auto',
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-word',
          fontSize: 14,
          lineHeight: 1.5,
        }}
      >
        {events.map((event, idx) => {
          const token = String(event.payload.token_text ?? '');
          const isToolInvoke = Boolean(event.payload.is_tool_invoke);
          if (isToolInvoke) {
            return (
              <span
                key={`${event.t_ms}-${idx}`}
                className="neo-badge neo-badge--black"
                style={{ marginRight: 4 }}
                title={`${(event.t_ms / 1000).toFixed(2)}s -- tool invoke`}
              >
                tool:{token}
              </span>
            );
          }
          return (
            <span
              key={`${event.t_ms}-${idx}`}
              title={`${(event.t_ms / 1000).toFixed(2)}s -- ${event.provider}`}
            >
              {token}
            </span>
          );
        })}
      </div>
    </div>
  );
}
