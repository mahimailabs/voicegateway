// Pre-v0.3.0 banner: shown when a session has no captured replay
// events. Implements REQ-VG-REPLAY-001 AC-3 (no broken timeline;
// clear messaging + link out to the session detail page).
//
// v0.3.0 minimal: the link to the session detail page renders as
// a real router link so the developer can drill into per-modality
// cost without leaving the dashboard. Pre-v0.3.0 sessions still
// have a valid session detail view with cost/duration/breakdown.

import { Link } from 'react-router-dom';

interface Props {
  sessionId: string;
}

export default function PreV030Banner({ sessionId }: Props) {
  return (
    <div className="neo-card neo-card--strip-orange">
      <div className="label">Recorded before replay capture existed</div>
      <div className="mt-md">
        This session predates v0.3.0 conversation-replay capture, so no
        event timeline is available. Cost, duration, and per-modality
        breakdown are still on the{' '}
        <Link to={`/sessions/${encodeURIComponent(sessionId)}`}>
          session detail page
        </Link>
        .
      </div>
      <div className="label mt-sm" style={{ opacity: 0.7 }}>
        Replay capture started recording events for sessions that began
        after v0.3.0 shipped.
      </div>
    </div>
  );
}
