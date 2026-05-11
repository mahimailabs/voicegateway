import { useEffect, useMemo, useState } from 'react';
import { useParams } from 'react-router-dom';
import PageHeader from '../components/PageHeader';
import StalenessBanner from '../components/StalenessBanner';
import { fetchJson } from '../lib/api';
import type { ReplayEvent, ReplayResponse } from '../lib/types';

// v0.3.0 Conversation Replay page (REQ-VG-REPLAY-001..006).
//
// Scaffolding for T11. The four panes + RunningCostCounter +
// Scrubber subcomponents land in T12; PreV030Banner lands in T12 as
// well. This file owns:
//
// - Reading `session_id` from the URL via `useParams`.
// - Fetching the full replay on mount (OQ3 pre-fetch resolution;
//   `Replay` events are bounded by per-minute capture + retention).
// - Holding the scrubber `t_ms` state shared across the panes.
// - Layout: StalenessBanner + PageHeader + scrubber row +
//   four-pane grid + cost counter.
// - The pre-v0.3.0 empty-events fallback (REQ-VG-REPLAY-001 AC-3).
//
// T12 swaps the placeholder card slots for the real components.

export default function Replay() {
  const { sessionId } = useParams<{ sessionId: string }>();
  const [data, setData] = useState<ReplayResponse | null>(null);
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  // Playhead in ms relative to call start. Shared state so the four
  // panes + cost counter (T12) all render the same moment.
  const [playheadMs, setPlayheadMs] = useState<number>(0);

  useEffect(() => {
    if (!sessionId) return;
    setLoading(true);
    setError(null);
    fetchJson<ReplayResponse>(
      `/api/sessions/${encodeURIComponent(sessionId)}/replay`,
    )
      .then((response) => {
        setData(response);
        setPlayheadMs(0);
      })
      .catch((err: Error) => {
        setError(err.message ?? 'Failed to load replay');
        setData(null);
      })
      .finally(() => setLoading(false));
  }, [sessionId]);

  const totalMs = useMemo<number>(() => {
    if (!data || data.events.length === 0) return 0;
    return data.events[data.events.length - 1].t_ms;
  }, [data]);

  const eventsBeforePlayhead = useMemo<ReplayEvent[]>(() => {
    if (!data) return [];
    return data.events.filter((e) => e.t_ms <= playheadMs);
  }, [data, playheadMs]);

  const costSoFarUsd = useMemo<number>(() => {
    return eventsBeforePlayhead.reduce<number>(
      (acc, e) => acc + (e.cost_usd ?? 0),
      0,
    );
  }, [eventsBeforePlayhead]);

  if (!sessionId) {
    return (
      <div className="empty-state">Replay route missing session id.</div>
    );
  }

  return (
    <div>
      <StalenessBanner />
      <PageHeader
        title="Replay"
        subtitle={`Session ${sessionId}`}
        accent="orange"
      />

      {loading && !data && (
        <div className="empty-state">Loading replay...</div>
      )}

      {error && (
        <div className="neo-card neo-card--strip-red">
          <div className="label">Replay unavailable</div>
          <div className="stat-value mt-md">{error}</div>
        </div>
      )}

      {data && data.events.length === 0 && (
        <div className="neo-card neo-card--strip-orange">
          <div className="label">Recorded before replay capture existed</div>
          <div className="mt-md">
            This session predates v0.3.0 replay capture. No event timeline
            is available. The session detail page still shows cost,
            duration, and per-modality breakdown.
          </div>
        </div>
      )}

      {data && data.events.length > 0 && (
        <>
          <div className="neo-card mb-lg">
            <div className="flex flex-row items-center gap-md">
              <div className="label" style={{ minWidth: 88 }}>
                Playhead
              </div>
              <input
                type="range"
                min={0}
                max={totalMs}
                value={playheadMs}
                onChange={(e) => setPlayheadMs(Number(e.target.value))}
                style={{ flex: 1 }}
                aria-label="Replay timeline"
              />
              <div className="mono" style={{ minWidth: 110, textAlign: 'right' }}>
                {(playheadMs / 1000).toFixed(2)}s / {(totalMs / 1000).toFixed(2)}s
              </div>
            </div>
            <div className="label mt-md" style={{ opacity: 0.7 }}>
              {eventsBeforePlayhead.length} of {data.events.length} events
              before playhead. T12 swaps this row for the keyboard-driven
              Scrubber component.
            </div>
          </div>

          <div className="grid grid-cols-2 gap-lg">
            {/* T12 ships TranscriptPane, ModelOutputPane, SynthesisPane,
                ConversationStatePane into these slots. Placeholders below
                render the event count + last text for each modality so the
                page is functional standalone. */}
            <PaneSlot
              label="Transcript (STT)"
              accent="green"
              events={eventsBeforePlayhead.filter((e) => e.modality === 'stt')}
            />
            <PaneSlot
              label="Model output (LLM)"
              accent="blue"
              events={eventsBeforePlayhead.filter((e) => e.modality === 'llm')}
            />
            <PaneSlot
              label="Voice synthesis (TTS)"
              accent="orange"
              events={eventsBeforePlayhead.filter((e) => e.modality === 'tts')}
            />
            <PaneSlot
              label="Conversation state"
              accent="red"
              events={eventsBeforePlayhead.filter((e) => e.modality === 'state')}
            />
          </div>

          <div className="neo-card neo-card--strip-green mt-lg">
            <div className="label">Running cost</div>
            <div className="stat-value stat-value--xl mt-md">
              ${costSoFarUsd.toFixed(6)}
            </div>
            <div className="label mt-sm" style={{ opacity: 0.7 }}>
              Sum of cost_usd for {eventsBeforePlayhead.length} events
              before the playhead. T12 swaps this for RunningCostCounter
              with per-modality breakdown and top-3 tooltip.
            </div>
          </div>
        </>
      )}
    </div>
  );
}

// Inline placeholder pane. T12 replaces each instance with the proper
// pane component while keeping the same `events` prop shape.
function PaneSlot({
  label,
  accent,
  events,
}: {
  label: string;
  accent: 'green' | 'blue' | 'orange' | 'red';
  events: ReplayEvent[];
}) {
  return (
    <div className={`neo-card neo-card--strip-${accent}`}>
      <div className="label">{label}</div>
      <div className="stat-value mt-md">
        {events.length} event{events.length === 1 ? '' : 's'}
      </div>
      {events.length > 0 && (
        <div
          className="mono mt-sm"
          style={{
            fontSize: 12,
            opacity: 0.8,
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
            maxHeight: 120,
            overflow: 'auto',
          }}
        >
          {JSON.stringify(events[events.length - 1].payload, null, 2)}
        </div>
      )}
    </div>
  );
}
