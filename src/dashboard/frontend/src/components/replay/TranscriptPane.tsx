// Transcript pane: STT chunks up to playhead with partial-revision
// highlighting. Implements REQ-VG-REPLAY-003 AC-1.
//
// Partial chunks (is_final=false) render with reduced opacity so the
// reader can see how the agent's speech-to-text was rendering
// progressively. Final chunks render at full opacity.

import type { ReplayEvent } from '../../lib/types';

interface Props {
  events: ReplayEvent[];
}

export default function TranscriptPane({ events }: Props) {
  if (events.length === 0) {
    return (
      <div className="neo-card neo-card--strip-green">
        <div className="label">Transcript (STT)</div>
        <div className="empty-state mt-md">No caller speech captured yet.</div>
      </div>
    );
  }

  return (
    <div className="neo-card neo-card--strip-green">
      <div className="label">Transcript (STT)</div>
      <div
        className="mt-md"
        style={{
          maxHeight: 220,
          overflowY: 'auto',
          display: 'flex',
          flexDirection: 'column',
          gap: 4,
        }}
      >
        {events.map((event, idx) => {
          const text = String(event.payload.text ?? '');
          const isFinal = Boolean(event.payload.is_final);
          return (
            <div
              key={`${event.t_ms}-${idx}`}
              title={`${(event.t_ms / 1000).toFixed(2)}s -- ${event.provider}`}
              style={{
                opacity: isFinal ? 1 : 0.55,
                fontStyle: isFinal ? 'normal' : 'italic',
              }}
            >
              <span className="mono" style={{ fontSize: 11, opacity: 0.6 }}>
                {(event.t_ms / 1000).toFixed(2)}s
              </span>{' '}
              {text}
            </div>
          );
        })}
      </div>
    </div>
  );
}
