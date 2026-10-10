import { useEffect, useMemo, useState } from 'react';
import { useParams } from 'react-router-dom';
import PageHeader from '../components/PageHeader';
import ConversationStatePane from '../components/replay/ConversationStatePane';
import PreV030Banner from '../components/replay/PreV030Banner';
import Scrubber from '../components/replay/Scrubber';
import { fetchJson } from '../lib/api';
import type { ReplayEvent, ReplayResponse } from '../lib/types';

// Conversation replay: the conversation-state snapshots attach() captured for
// one session, scrubbable by time. Replay records state snapshots only; it
// never sits in the STT/LLM/TTS streams.

export default function Replay() {
  const { sessionId } = useParams<{ sessionId: string }>();
  const [data, setData] = useState<ReplayResponse | null>(null);
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  // Playhead in ms relative to call start.
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

  if (!sessionId) {
    return (
      <div className="empty-state">Replay route missing session id.</div>
    );
  }

  return (
    <div>
      <PageHeader
        title="Replay"
        subtitle={`Session ${sessionId}`}
        accent="orange"
      />

      {loading && !data && (
        <div className="empty-state">Loading replay...</div>
      )}

      {error && (
        <div className="vg-card" style={{ borderColor: 'var(--vg-red)', borderLeftWidth: 3 }}>
          <div className="vg-card__label" style={{ color: 'var(--vg-red)' }}>Replay unavailable</div>
          <div className="vg-stat mt-md" style={{ fontSize: 16, fontWeight: 600 }}>{error}</div>
        </div>
      )}

      {data && data.events.length === 0 && (
        <PreV030Banner sessionId={sessionId} />
      )}

      {data && data.events.length > 0 && (
        <>
          <Scrubber
            totalMs={totalMs}
            playheadMs={playheadMs}
            onChange={setPlayheadMs}
            eventTimestampsMs={data.events.map((e) => e.t_ms)}
          />

          <ConversationStatePane
            events={eventsBeforePlayhead.filter((e) => e.modality === 'state')}
          />
        </>
      )}
    </div>
  );
}
