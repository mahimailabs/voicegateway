// Replay scrubber: range input + keyboard arrows + click-to-jump.
// Implements REQ-VG-REPLAY-002 (drag, click, keyboard).
//
// The keyboard semantics: ArrowLeft / ArrowRight step to the
// previous / next event boundary, not by a fixed time delta.
// Stepping by event keeps the playhead snapping to meaningful
// moments rather than landing in dead time between captures.
// Shift+Arrow steps by a coarser 1-second jump.

import { useCallback, useEffect, useRef } from 'react';

interface Props {
  totalMs: number;
  playheadMs: number;
  onChange: (newMs: number) => void;
  // Event timestamps used for arrow-key stepping. Sorted ASC.
  eventTimestampsMs: number[];
}

export default function Scrubber({
  totalMs,
  playheadMs,
  onChange,
  eventTimestampsMs,
}: Props) {
  const rangeRef = useRef<HTMLInputElement | null>(null);

  const stepToPrev = useCallback(() => {
    // Largest event timestamp strictly less than the current playhead.
    let target = 0;
    for (const t of eventTimestampsMs) {
      if (t < playheadMs) target = t;
      else break;
    }
    onChange(target);
  }, [eventTimestampsMs, playheadMs, onChange]);

  const stepToNext = useCallback(() => {
    // Smallest event timestamp strictly greater than the current playhead.
    for (const t of eventTimestampsMs) {
      if (t > playheadMs) {
        onChange(t);
        return;
      }
    }
    onChange(totalMs);
  }, [eventTimestampsMs, playheadMs, onChange, totalMs]);

  useEffect(() => {
    const el = rangeRef.current;
    if (!el) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'ArrowLeft') {
        e.preventDefault();
        if (e.shiftKey) onChange(Math.max(0, playheadMs - 1000));
        else stepToPrev();
      } else if (e.key === 'ArrowRight') {
        e.preventDefault();
        if (e.shiftKey) onChange(Math.min(totalMs, playheadMs + 1000));
        else stepToNext();
      } else if (e.key === 'Home') {
        e.preventDefault();
        onChange(0);
      } else if (e.key === 'End') {
        e.preventDefault();
        onChange(totalMs);
      }
    };
    el.addEventListener('keydown', onKey);
    return () => el.removeEventListener('keydown', onKey);
  }, [playheadMs, totalMs, stepToPrev, stepToNext, onChange]);

  return (
    <div className="neo-card mb-lg">
      <div className="flex flex-row items-center gap-md">
        <div className="label" style={{ minWidth: 88 }}>
          Playhead
        </div>
        <input
          ref={rangeRef}
          type="range"
          min={0}
          max={totalMs}
          value={playheadMs}
          onChange={(e) => onChange(Number(e.target.value))}
          style={{ flex: 1 }}
          aria-label="Replay timeline scrubber"
        />
        <div
          className="mono"
          style={{ minWidth: 110, textAlign: 'right' }}
        >
          {(playheadMs / 1000).toFixed(2)}s / {(totalMs / 1000).toFixed(2)}s
        </div>
      </div>
      <div className="label mt-md" style={{ opacity: 0.7 }}>
        Arrow keys step to prev/next event. Shift+Arrow jumps 1s.
        Home/End jump to call start/end.
      </div>
    </div>
  );
}
