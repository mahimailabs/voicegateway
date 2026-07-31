// Diagnostics > Agents: the two agent populations, side by side.
//
// They are NOT the same list, and conflating them is the mistake this tab
// exists to prevent. `agents` is what LiveKit's server API can see: a worker
// only appears once it has JOINED a room. `roster` is what the workers say about
// themselves through their own heartbeat, which is the only way an idle
// registered worker is visible at all.

import type { DiagAgentsResult, DiagnosticCheckResult } from '../../lib/types';
import { formatRelativeTime, rosterStatusBadge } from '../../lib/ui';
import { Card, CheckGate, Note } from './shared';

const ROSTER_NOTE =
  'Idle and registered workers are not reported by LiveKit’s server API, so this run could only see agents already in a room. Set VOICEGW_COLLECTOR_URL and VOICEGW_API_KEY, and call register_worker() in your agents, to list the heartbeat roster here too.';

function formatAge(ageSeconds: number | null): string {
  if (ageSeconds == null) return '—'; // the server reported no join time
  if (ageSeconds < 60) return `${Math.round(ageSeconds)}s`;
  const minutes = Math.floor(ageSeconds / 60);
  if (minutes < 60) return `${minutes}m`;
  return `${Math.floor(minutes / 60)}h`;
}

export default function AgentsTab({
  check,
}: {
  check: DiagnosticCheckResult<DiagAgentsResult> | undefined;
}) {
  return (
    <CheckGate
      check={check}
      label="Agents in rooms"
      missingHint="Tick “Agents in rooms” above and run the checks again."
    >
      {(result) => (
        <>
          <InRoomCard agents={result.agents} />
          <RosterCard roster={result.roster} />
        </>
      )}
    </CheckGate>
  );
}

function InRoomCard({ agents }: { agents: DiagAgentsResult['agents'] }) {
  const rooms = new Set(agents.map((a) => a.room)).size;
  return (
    <Card
      label="In-room agents (LiveKit server API)"
      right={
        <span className="neo-badge neo-badge--blue">
          {agents.length} in {rooms} {rooms === 1 ? 'room' : 'rooms'}
        </span>
      }
    >
      {agents.length === 0 ? (
        <Note>
          No agent was in a room when this check ran. LiveKit reports a worker only once it has
          joined a room, so an idle worker that is running fine looks identical to no worker at
          all here: the heartbeat roster below is what tells them apart.
        </Note>
      ) : (
        <>
          <table className="neo-table neo-table--blue">
            <thead>
              <tr>
                <th>Agent</th>
                <th>Room</th>
                <th>State</th>
                <th>Humans</th>
                <th>Age</th>
              </tr>
            </thead>
            <tbody>
              {agents.map((a) => (
                <tr key={`${a.agent_name}:${a.room}:${a.identity ?? ''}`}>
                  <td style={{ fontWeight: 600 }}>{a.agent_name}</td>
                  <td className="mono" style={{ fontSize: 12 }}>{a.room}</td>
                  <td>
                    <span
                      className={`neo-badge ${a.state === 'active' ? 'neo-badge--green' : 'neo-badge--yellow'}`}
                      title={
                        a.state === 'active'
                          ? 'the worker joined the room'
                          : 'the job was assigned but no worker has joined yet'
                      }
                    >
                      {a.state}
                    </span>
                  </td>
                  <td className="mono">{a.humans}</td>
                  <td className="mono" style={{ color: 'var(--vg-muted)' }}>{formatAge(a.age_s)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div style={{ marginTop: 10 }}>
            <Note>
              “dispatched” means LiveKit assigned the job but no worker has joined the room yet.
              Humans counts the non-agent participants sharing that room.
            </Note>
          </div>
        </>
      )}
    </Card>
  );
}

function RosterCard({ roster }: { roster: DiagAgentsResult['roster'] }) {
  // null and [] are different answers: nobody was asked, versus asked and told
  // nothing. Rendering both as "0 workers" would report a missing collector as
  // an empty fleet.
  if (roster === null) {
    return (
      <Card
        label="Registered workers (heartbeat roster)"
        right={<span className="neo-badge neo-badge--black">not configured</span>}
      >
        <Note>{ROSTER_NOTE}</Note>
      </Card>
    );
  }

  const counts = {
    idle: roster.filter((w) => w.status === 'idle').length,
    busy: roster.filter((w) => w.status === 'busy').length,
    offline: roster.filter((w) => w.status === 'offline').length,
  };

  return (
    <Card
      label="Registered workers (heartbeat roster)"
      right={
        <span className="neo-badge neo-badge--blue">
          {roster.length} registered · {counts.idle} idle · {counts.busy} busy · {counts.offline} offline
        </span>
      }
    >
      {roster.length === 0 ? (
        <Note>
          The collector is configured but reported no workers. Either none have sent a heartbeat
          yet, or it could not be reached on this run: a roster fetch is best-effort and never
          fails the check.
        </Note>
      ) : (
        <table className="neo-table neo-table--blue">
          <thead>
            <tr>
              <th>Worker</th>
              <th>Status</th>
              <th>Region</th>
              <th>Version</th>
              <th>Last heartbeat</th>
            </tr>
          </thead>
          <tbody>
            {roster.map((w, i) => (
              <tr key={w.agent_id ?? w.agent_name ?? i}>
                <td style={{ fontWeight: 600 }}>{w.agent_name || w.agent_id || 'unnamed worker'}</td>
                <td>
                  <span className={`neo-badge ${rosterStatusBadge(w.status ?? 'offline')}`}>
                    {w.status ?? 'unknown'}
                  </span>
                </td>
                <td className="mono" style={{ fontSize: 12 }}>{w.region ?? '—'}</td>
                <td className="mono" style={{ fontSize: 12 }}>{w.version ?? '—'}</td>
                <td className="mono" style={{ fontSize: 12, color: 'var(--vg-muted)' }}>
                  {formatRelativeTime(w.last_seen ?? null)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}
