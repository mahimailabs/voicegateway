// Running cost counter: sum of cost_usd before playhead with
// per-modality breakdown + top-3 costliest events tooltip.
// Implements REQ-VG-REPLAY-004 AC-1 + AC-2.
//
// The tooltip surfaces the three priciest events by their t_ms so
// the developer can spot the call's cost spikes. A click on a
// tooltip row would jump the playhead there; v0.3.0 minimal ships
// the tooltip read-only with timestamp labels (an `onClick` hook
// can land in v0.3.x as a follow-up).

import { useMemo, useState } from 'react';
import type { ReplayEvent } from '../../lib/types';

interface Props {
  eventsBeforePlayhead: ReplayEvent[];
}

export default function RunningCostCounter({ eventsBeforePlayhead }: Props) {
  const [showTooltip, setShowTooltip] = useState(false);

  const { total, byModality, top3 } = useMemo(() => {
    let total = 0;
    const byModality = { stt: 0, llm: 0, tts: 0 };
    for (const e of eventsBeforePlayhead) {
      const cost = e.cost_usd ?? 0;
      total += cost;
      if (e.modality === 'stt') byModality.stt += cost;
      else if (e.modality === 'llm') byModality.llm += cost;
      else if (e.modality === 'tts') byModality.tts += cost;
    }
    const top3 = [...eventsBeforePlayhead]
      .filter((e) => (e.cost_usd ?? 0) > 0)
      .sort((a, b) => (b.cost_usd ?? 0) - (a.cost_usd ?? 0))
      .slice(0, 3);
    return { total, byModality, top3 };
  }, [eventsBeforePlayhead]);

  return (
    <div
      className="neo-card neo-card--strip-green mt-lg"
      style={{ position: 'relative' }}
      onMouseEnter={() => setShowTooltip(true)}
      onMouseLeave={() => setShowTooltip(false)}
    >
      <div className="label">Running cost</div>
      <div className="stat-value stat-value--xl mt-md">${total.toFixed(6)}</div>
      <div
        className="flex flex-row gap-md mt-sm mono"
        style={{ fontSize: 12, opacity: 0.85 }}
      >
        <span>STT ${byModality.stt.toFixed(6)}</span>
        <span>LLM ${byModality.llm.toFixed(6)}</span>
        <span>TTS ${byModality.tts.toFixed(6)}</span>
      </div>
      <div className="label mt-sm" style={{ opacity: 0.7 }}>
        Sum of cost_usd for {eventsBeforePlayhead.length} events before
        playhead. Hover for top-3 costliest events.
      </div>
      {showTooltip && top3.length > 0 && (
        <div
          className="neo-card"
          style={{
            position: 'absolute',
            top: '100%',
            right: 0,
            zIndex: 10,
            minWidth: 280,
            background: 'var(--bg, #fff)',
            border: '2px solid var(--ink, #000)',
            boxShadow: '4px 4px 0 var(--ink, #000)',
            padding: 12,
            marginTop: 8,
          }}
        >
          <div className="label">Top 3 costliest events</div>
          <div className="mt-sm" style={{ fontSize: 12 }}>
            {top3.map((e, idx) => (
              <div
                key={`${e.t_ms}-${idx}`}
                className="mono"
                style={{ marginBottom: 4 }}
              >
                {(e.t_ms / 1000).toFixed(2)}s · {e.modality} · ${(e.cost_usd ?? 0).toFixed(6)}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
