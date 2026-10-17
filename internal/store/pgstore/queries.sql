-- name: CreateProject :one
INSERT INTO projects (organisation_id, id, name, livekit_url, livekit_api_key, livekit_api_secret_sealed)
VALUES ($1, $2, $3, $4, $5, $6)
RETURNING organisation_id, id, name, livekit_url, livekit_api_key, created_at;

-- name: ProjectByLiveKitKey :one
SELECT organisation_id, id, name, livekit_url, livekit_api_key, created_at
FROM projects
WHERE livekit_api_key = $1;

-- name: SealedLiveKitSecret :one
SELECT livekit_api_secret_sealed
FROM projects
WHERE organisation_id = $1 AND id = $2;

-- name: OrganisationExists :one
SELECT EXISTS (SELECT 1 FROM organisations WHERE id = $1);

-- name: UpsertCallByRoomSID :one
-- Opens the call of one room instance or adds to it. A value already set is kept; the latest
-- reported activity wins. GREATEST and COALESCE skip nulls, so an unreported time changes nothing.
INSERT INTO calls (organisation_id, project_id, room_sid, room_name, job_id, started_at, last_activity_at)
VALUES (@organisation_id, @project_id, @room_sid, sqlc.narg(room_name), sqlc.narg(job_id), sqlc.narg(started_at), sqlc.narg(last_activity_at))
ON CONFLICT (organisation_id, project_id, room_sid) WHERE room_sid IS NOT NULL DO UPDATE SET
    room_name        = COALESCE(calls.room_name, EXCLUDED.room_name),
    job_id           = COALESCE(calls.job_id, EXCLUDED.job_id),
    started_at       = COALESCE(calls.started_at, EXCLUDED.started_at),
    last_activity_at = GREATEST(calls.last_activity_at, EXCLUDED.last_activity_at),
    updated_at       = now()
RETURNING id;

-- name: CallByRoomSID :one
SELECT id, ended_at FROM calls
WHERE organisation_id = @organisation_id AND project_id = @project_id AND room_sid = @room_sid;

-- name: CallByJobID :one
SELECT id, ended_at FROM calls
WHERE organisation_id = @organisation_id AND project_id = @project_id AND job_id = @job_id
ORDER BY started_at DESC NULLS LAST
LIMIT 1;

-- name: UpsertCallByJobID :one
-- Opens a call for a job no source has named a room SID for, or adds to it.
INSERT INTO calls (organisation_id, project_id, job_id, last_activity_at)
VALUES (@organisation_id, @project_id, @job_id, sqlc.narg(last_activity_at))
ON CONFLICT (organisation_id, project_id, job_id) WHERE room_sid IS NULL AND job_id IS NOT NULL DO UPDATE SET
    last_activity_at = GREATEST(calls.last_activity_at, EXCLUDED.last_activity_at),
    updated_at       = now()
RETURNING id;

-- name: OpenCallInRoom :one
-- The call in a named room at a reported time: the room-name-and-window fallback of 0012, for
-- sources that name no room SID.
SELECT id FROM calls
WHERE organisation_id = @organisation_id AND project_id = @project_id AND room_name = @room_name
  AND (started_at IS NULL OR started_at <= @at)
  AND (ended_at IS NULL OR ended_at >= @at)
  AND (end_state = 'open' OR ended_at IS NOT NULL)
ORDER BY started_at DESC NULLS LAST
LIMIT 1;

-- name: TouchCall :exec
-- Adds what a source joined by job id or room name reported to its call.
UPDATE calls SET
    job_id           = COALESCE(job_id, sqlc.narg(job_id)),
    started_at       = COALESCE(started_at, sqlc.narg(started_at)),
    last_activity_at = GREATEST(last_activity_at, sqlc.narg(last_activity_at)),
    updated_at       = now()
WHERE id = @id;

-- name: FinishCall :exec
-- room_finished: the end LiveKit reported, observed.
UPDATE calls SET
    ended_at         = COALESCE(sqlc.narg(ended_at), ended_at),
    end_state        = 'observed',
    last_activity_at = GREATEST(last_activity_at, sqlc.narg(ended_at)),
    updated_at       = now()
WHERE id = @id;

-- name: UpsertParticipant :exec
INSERT INTO call_participants (call_id, participant_sid, identity, kind, joined_at, left_at)
VALUES (@call_id, @participant_sid, @identity, @kind, sqlc.narg(joined_at), sqlc.narg(left_at))
ON CONFLICT (call_id, participant_sid) DO UPDATE SET
    identity  = CASE WHEN EXCLUDED.identity <> '' THEN EXCLUDED.identity ELSE call_participants.identity END,
    kind      = EXCLUDED.kind,
    joined_at = COALESCE(call_participants.joined_at, EXCLUDED.joined_at),
    left_at   = COALESCE(EXCLUDED.left_at, call_participants.left_at);

-- name: CloseIdleCalls :execrows
-- Closes calls with no reported activity since @idle_since, and that nothing has joined since then
-- either, so replaying old data does not close a call the moment it is opened. The end is the
-- latest reported activity, null when no source reported one.
UPDATE calls SET end_state = 'inferred', ended_at = last_activity_at
WHERE end_state = 'open' AND updated_at < @idle_since
  AND (last_activity_at IS NULL OR last_activity_at < @idle_since);

-- name: ProjectByID :one
SELECT organisation_id, id, name, livekit_url, livekit_api_key, created_at
FROM projects
WHERE organisation_id = $1 AND id = $2;

-- name: CallByID :one
-- One call of a project, with how many participants webhooks reported for it.
SELECT c.id, c.room_name, c.room_sid, c.job_id, c.started_at, c.ended_at, c.end_state,
       (SELECT count(*) FROM call_participants p WHERE p.call_id = c.id) AS participants
FROM calls c
WHERE c.organisation_id = @organisation_id AND c.project_id = @project_id AND c.id = @id;
