// Voice synthesis pane: TTS frame markers + underrun spans.
// Implements REQ-VG-REPLAY-003 AC-3.
//
// Each TTS frame renders as a small mono row with its t_ms,
// duration, and provider. Frames flagged with payload.underrun
// render in red so the developer can spot synthesis-side latency
// gaps at a glance (the Refinery's "the developer pinpoints
// synthesis-side latency" requirement).

import type { ReplayEvent } from '../../lib/types';

interface Props {
  events: ReplayEvent[];
}

export default function SynthesisPane({ events }: Props) {
  if (events.length === 0) {
    return (
      <div className="neo-card neo-card--strip-orange">
        <div className="label">Voice synthesis (TTS)</div>
        <div className="empty-state mt-md">No synthesis frames captured yet.</div>
      </div>
    );
  }

  const underrunCount = events.filter(
    (e) => Boolean(e.payload.underrun),
  ).length;

  return (
    <div className="neo-card neo-card--strip-orange">
      <div className="label">
        Voice synthesis (TTS)
        {underrunCount > 0 && (
          <span
            className="neo-badge neo-badge--red"
            style={{ marginLeft: 8 }}
          >
            {underrunCount} underrun{underrunCount === 1 ? '' : 's'}
          </span>
        )}
      </div>
      <div
        className="mt-md mono"
        style={{
          maxHeight: 220,
          overflowY: 'auto',
          fontSize: 11,
          lineHeight: 1.4,
        }}
      >
        {events.map((event, idx) => {
          const durationMs = Number(event.payload.frame_duration_ms ?? 0);
          const isUnderrun = Boolean(event.payload.underrun);
          return (
            <div
              key={`${event.t_ms}-${idx}`}
              style={{ color: isUnderrun ? '#D14242' : 'inherit' }}
              title={
                isUnderrun
                  ? `Underrun at ${(event.t_ms / 1000).toFixed(2)}s -- synthesis fell behind`
                  : `${(event.t_ms / 1000).toFixed(2)}s -- ${event.provider}`
              }
            >
              {(event.t_ms / 1000).toFixed(2)}s · {durationMs}ms{' '}
              {isUnderrun ? '!! underrun' : ''}
            </div>
          );
        })}
      </div>
    </div>
  );
}
