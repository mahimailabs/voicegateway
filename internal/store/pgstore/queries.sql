-- name: CreateProject :one
INSERT INTO projects (organisation_id, id, name, livekit_url, livekit_api_key, livekit_api_secret_sealed)
VALUES ($1, $2, $3, $4, $5, $6)
RETURNING organisation_id, id, name, livekit_url, livekit_api_key, created_at, recordings;

-- name: SealedLiveKitSecret :one
SELECT livekit_api_secret_sealed
FROM projects
WHERE organisation_id = $1 AND id = $2;

-- name: OrganisationExists :one
SELECT EXISTS (SELECT 1 FROM organisations WHERE id = $1);

-- name: ListProjects :many
SELECT organisation_id, id, name, livekit_url, livekit_api_key, created_at, recordings
FROM projects
ORDER BY organisation_id, id;

-- name: UpsertCallByRoomSID :one
-- Opens the call of one room instance or adds to it. A value already set is kept; the latest
-- reported activity wins. GREATEST and COALESCE skip nulls, so an unreported time changes nothing.
INSERT INTO calls (organisation_id, project_id, room_sid, room_name, job_id, started_at, last_activity_at, first_seen_at)
VALUES (@organisation_id, @project_id, @room_sid, sqlc.narg(room_name), sqlc.narg(job_id), sqlc.narg(started_at), sqlc.narg(last_activity_at), sqlc.narg(first_seen_at))
ON CONFLICT (organisation_id, project_id, room_sid) WHERE room_sid IS NOT NULL DO UPDATE SET
    room_name        = COALESCE(calls.room_name, EXCLUDED.room_name),
    job_id           = COALESCE(calls.job_id, EXCLUDED.job_id),
    started_at       = COALESCE(calls.started_at, EXCLUDED.started_at),
    last_activity_at = GREATEST(calls.last_activity_at, EXCLUDED.last_activity_at),
    first_seen_at    = LEAST(calls.first_seen_at, EXCLUDED.first_seen_at),
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
INSERT INTO calls (organisation_id, project_id, job_id, last_activity_at, first_seen_at)
VALUES (@organisation_id, @project_id, @job_id, sqlc.narg(last_activity_at), sqlc.narg(first_seen_at))
ON CONFLICT (organisation_id, project_id, job_id) WHERE room_sid IS NULL AND job_id IS NOT NULL DO UPDATE SET
    last_activity_at = GREATEST(calls.last_activity_at, EXCLUDED.last_activity_at),
    first_seen_at    = LEAST(calls.first_seen_at, EXCLUDED.first_seen_at),
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
    first_seen_at    = LEAST(first_seen_at, sqlc.narg(first_seen_at)),
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
SELECT organisation_id, id, name, livekit_url, livekit_api_key, created_at, recordings
FROM projects
WHERE organisation_id = $1 AND id = $2;

-- name: CallByID :one
-- One call of a project as the API reads it, with the servable rule listCalls shares.
-- With its latest recording: an agent that uploads twice for one call, from two jobs, leaves two,
-- and the call view plays the latest.
SELECT l.id, l.room_name, l.room_sid, l.job_id, l.started_at, l.ended_at, l.end_state, l.first_seen_at,
       l.participants, l.servable, l.summarized::boolean AS summarized, c.tags, c.archived, c.metadata_revision, c.fields,
       r.id AS recording_id, r.started_at AS recording_started_at
FROM call_list l JOIN calls c ON c.id = l.id
LEFT JOIN LATERAL (
    SELECT id, started_at FROM call_recordings
    WHERE call_id = l.id ORDER BY stored_at DESC, id LIMIT 1
) r ON true
WHERE l.organisation_id = @organisation_id AND l.project_id = @project_id AND l.id = @id;

-- name: StaleCalls :many
-- Calls whose summary needs rebuilding, oldest mark first; a call whose rebuild failed waits until
-- its mark, set ahead by the backoff, comes due.
SELECT organisation_id, project_id, id, summary_stale_at
FROM calls
WHERE summary_stale_at IS NOT NULL AND summary_stale_at <= now()
ORDER BY summary_stale_at
LIMIT @max_calls;

-- name: PutSummary :exec
-- Stores a call's rebuilt summary.
INSERT INTO call_summaries (call_id, turn_count, answer_ms, answer_ms_median, models, pipeline, agent_key, computed_at)
VALUES (@call_id, sqlc.narg(turn_count), @answer_ms, sqlc.narg(answer_ms_median), @models, @pipeline, sqlc.narg(agent_key), @computed_at)
ON CONFLICT (call_id) DO UPDATE SET
    turn_count       = EXCLUDED.turn_count,
    answer_ms        = EXCLUDED.answer_ms,
    answer_ms_median = EXCLUDED.answer_ms_median,
    models           = EXCLUDED.models,
    pipeline         = EXCLUDED.pipeline,
    agent_key        = EXCLUDED.agent_key,
    computed_at      = EXCLUDED.computed_at;

-- name: HealCall :exec
-- Fills what an older call lacks from its spans: the room name the agent reported and the earliest
-- time a span reported. A value already stored is kept.
UPDATE calls SET
    room_name     = COALESCE(room_name, sqlc.narg(room_name)),
    first_seen_at = LEAST(first_seen_at, sqlc.narg(first_seen_at))
WHERE id = @id;

-- name: ClearStale :exec
-- Clears a call's stale mark only if nothing joined since the rebuild read it.
UPDATE calls SET summary_stale_at = NULL, summary_failures = 0
WHERE id = @id AND summary_stale_at = @seen;

-- name: ListCalls :many
-- One page of a project's calls as of a snapshot. The sort key is one number, negated for
-- descending order, so unknowns sort last and ties break by id in both orders; the cursor is the
-- last row's (unknown, key, id).
WITH f AS (
    SELECT l.*, c.tags, c.archived, c.metadata_revision,
        CASE @sort_by::text
            WHEN 'startedAt'       THEN EXTRACT(EPOCH FROM l.start_at)::double precision
            WHEN 'durationSeconds' THEN l.duration_seconds
            WHEN 'turnCount'       THEN l.turn_count::double precision
            WHEN 'answerMs'        THEN l.answer_ms_median
        END * CASE WHEN @descending::bool THEN -1 ELSE 1 END AS sort_key
    FROM call_list l JOIN calls c ON c.id = l.id
    WHERE l.organisation_id = @organisation_id AND l.project_id = @project_id AND l.created_at <= @snapshot_at AND l.servable
      AND (sqlc.narg(from_at)::timestamptz IS NULL OR l.start_at >= sqlc.narg(from_at)::timestamptz)
      AND (sqlc.narg(to_at)::timestamptz IS NULL OR l.start_at < sqlc.narg(to_at)::timestamptz)
      AND (sqlc.narg(room)::text IS NULL OR l.room_name = sqlc.narg(room)::text)
      AND (cardinality(@channels::text[]) = 0 OR l.channel = ANY(@channels::text[]))
      AND (sqlc.narg(answer_min)::float8 IS NULL OR l.answer_ms_median >= sqlc.narg(answer_min)::float8)
      AND (sqlc.narg(answer_max)::float8 IS NULL OR l.answer_ms_median <= sqlc.narg(answer_max)::float8)
      AND (sqlc.narg(duration_min)::float8 IS NULL OR l.duration_seconds >= sqlc.narg(duration_min)::float8)
      AND (sqlc.narg(duration_max)::float8 IS NULL OR l.duration_seconds <= sqlc.narg(duration_max)::float8)
      AND (sqlc.narg(turns_min)::int IS NULL OR l.turn_count >= sqlc.narg(turns_min)::int)
      AND (sqlc.narg(turns_max)::int IS NULL OR l.turn_count <= sqlc.narg(turns_max)::int)
      AND (NOT @unknown_answer::bool OR l.answer_ms_median IS NULL)
      AND (NOT @unknown_duration::bool OR l.duration_seconds IS NULL)
      AND (NOT @unknown_turns::bool OR l.turn_count IS NULL)
      AND (cardinality(@tags::text[]) = 0 OR c.tags && @tags::text[])
      AND c.fields @> @fields::jsonb
      AND (sqlc.narg(archived)::boolean IS NULL OR c.archived = sqlc.narg(archived)::boolean)
      AND (sqlc.narg(recorded)::bool IS NULL OR l.recorded = sqlc.narg(recorded)::bool)
      AND NOT @match_none::bool
)
SELECT f.id, f.room_name, f.room_sid, f.start_at, f.started_at, f.ended_at, f.end_state, f.duration_seconds,
       f.participants, f.channel, f.turn_count, f.answer_ms, f.answer_ms_median, f.models, f.pipeline, f.agent_key, f.tags, f.archived, f.metadata_revision,
       f.recorded, (f.sort_key IS NULL) AS key_unknown, COALESCE(f.sort_key, 0)::double precision AS sort_value
FROM f
WHERE NOT @after_cursor::bool
   OR ((f.sort_key IS NULL), COALESCE(f.sort_key, 0), f.id) > (@cursor_unknown::bool, @cursor_value::float8, @cursor_id::uuid)
ORDER BY (f.sort_key IS NULL), COALESCE(f.sort_key, 0), f.id
LIMIT @max_rows;

-- name: CallTotals :one
-- Totals over every call the same filters match as of the snapshot, not one page. Answer values
-- come newest call first, each call's in turn order, so a cut drops the oldest calls first; the
-- median is the console's, the upper middle value.
WITH f AS (
    SELECT l.*
    FROM call_list l JOIN calls c ON c.id = l.id
    WHERE l.organisation_id = @organisation_id AND l.project_id = @project_id AND l.created_at <= @snapshot_at AND l.servable
      AND (sqlc.narg(from_at)::timestamptz IS NULL OR l.start_at >= sqlc.narg(from_at)::timestamptz)
      AND (sqlc.narg(to_at)::timestamptz IS NULL OR l.start_at < sqlc.narg(to_at)::timestamptz)
      AND (sqlc.narg(room)::text IS NULL OR l.room_name = sqlc.narg(room)::text)
      AND (cardinality(@channels::text[]) = 0 OR l.channel = ANY(@channels::text[]))
      AND (sqlc.narg(answer_min)::float8 IS NULL OR l.answer_ms_median >= sqlc.narg(answer_min)::float8)
      AND (sqlc.narg(answer_max)::float8 IS NULL OR l.answer_ms_median <= sqlc.narg(answer_max)::float8)
      AND (sqlc.narg(duration_min)::float8 IS NULL OR l.duration_seconds >= sqlc.narg(duration_min)::float8)
      AND (sqlc.narg(duration_max)::float8 IS NULL OR l.duration_seconds <= sqlc.narg(duration_max)::float8)
      AND (sqlc.narg(turns_min)::int IS NULL OR l.turn_count >= sqlc.narg(turns_min)::int)
      AND (sqlc.narg(turns_max)::int IS NULL OR l.turn_count <= sqlc.narg(turns_max)::int)
      AND (NOT @unknown_answer::bool OR l.answer_ms_median IS NULL)
      AND (NOT @unknown_duration::bool OR l.duration_seconds IS NULL)
      AND (NOT @unknown_turns::bool OR l.turn_count IS NULL)
      AND (cardinality(@tags::text[]) = 0 OR c.tags && @tags::text[])
      AND c.fields @> @fields::jsonb
      AND (sqlc.narg(archived)::boolean IS NULL OR c.archived = sqlc.narg(archived)::boolean)
      AND (sqlc.narg(recorded)::bool IS NULL OR l.recorded = sqlc.narg(recorded)::bool)
      AND NOT @match_none::bool
), a AS (
    SELECT u.v, f.start_at, f.id, u.ord
    FROM f, unnest(f.answer_ms) WITH ORDINALITY AS u(v, ord)
)
SELECT
    (SELECT count(*) FROM f) AS call_count,
    (SELECT count(*) FROM f WHERE channel = 'phone') AS phone,
    (SELECT count(*) FROM f WHERE channel = 'web') AS web,
    (SELECT count(*) FROM f WHERE channel = 'unknown') AS unknown_channel,
    (SELECT count(*) FROM f WHERE recorded) AS recorded,
    (SELECT count(*) FROM a) AS answer_turns,
    (SELECT count(*) FROM a WHERE a.v > @budget_ms::float8) AS over_budget,
    -- Null when no turn measured an answer.
    (SELECT (array_agg(a.v ORDER BY a.v))[count(*) / 2 + 1] FROM a) AS p50_ms,
    COALESCE((SELECT (array_agg(a.v ORDER BY a.start_at DESC, a.id, a.ord))[1:@max_values::int] FROM a), '{}')::double precision[] AS values_ms;

-- name: MarkStale :exec
-- Marks calls whose summary must be rebuilt: called after their rows reached ClickHouse, so a
-- sweep never rebuilds from a store that lacks them.
UPDATE calls SET summary_stale_at = now() WHERE id = ANY(@ids::uuid[]);

-- name: DeferStale :exec
-- Moves a call whose rebuild failed behind the others, and backs it off: 10 s, doubling with each
-- failure in a row, at most an hour. The exponent stops at 9 (10 s x 512, past the hour) before
-- the cap, so a long run of failures never overflows the interval.
UPDATE calls SET
    summary_failures = summary_failures + 1,
    summary_stale_at = now() + LEAST(interval '10 seconds' * power(2, LEAST(summary_failures, 9)), interval '1 hour')
WHERE id = @id AND summary_stale_at IS NOT NULL;

-- name: TransactionTime :one
-- The Postgres clock, for a list snapshot: the same clock that wrote created_at.
SELECT now()::timestamptz AS at;

-- name: PutListSnapshot :exec
INSERT INTO list_snapshots (id, organisation_id, project_id, filters, at, totals)
VALUES (@id, @organisation_id, @project_id, @filters, @at, @totals);

-- name: ForgetListSnapshots :exec
-- Deletes snapshots past their day, by the Postgres clock that wrote them.
DELETE FROM list_snapshots WHERE created_at < now() - interval '1 day';

-- name: ListSnapshot :one
-- A snapshot not yet expired, judged by the Postgres clock that wrote created_at.
SELECT organisation_id, project_id, filters, at, totals FROM list_snapshots
WHERE id = @id AND created_at >= now() - interval '1 day';

-- name: CountUsers :one
SELECT count(*) FROM users;

-- name: CreateUser :one
INSERT INTO users (email, password_hash) VALUES (@email, @password_hash) RETURNING id;

-- name: AddMembership :exec
INSERT INTO memberships (user_id, organisation_id, role) VALUES (@user_id, @organisation_id, @role);

-- name: UserByEmail :one
SELECT id, email, password_hash, password_version FROM users WHERE email = @email;

-- name: SetPassword :execrows
-- A new password, and a new password_version, so every session from before is signed out.
UPDATE users SET password_hash = @password_hash, password_version = password_version + 1 WHERE email = @email;

-- name: OrganisationMembers :many
SELECT u.id AS user_id, u.email, m.role
FROM memberships m JOIN users u ON u.id = m.user_id
WHERE m.organisation_id = @organisation_id
ORDER BY u.email;

-- name: LockOrganisation :one
-- Serialises role changes in one organisation until the transaction ends, so the last-admin check
-- and the write see the same admins.
SELECT id FROM organisations WHERE id = @id FOR UPDATE;

-- name: CountAdmins :one
SELECT count(*) FROM memberships WHERE organisation_id = @organisation_id AND role = 'admin';

-- name: SetMemberRole :execrows
UPDATE memberships SET role = @role WHERE organisation_id = @organisation_id AND user_id = @user_id;

-- name: RemoveMembership :execrows
DELETE FROM memberships WHERE organisation_id = @organisation_id AND user_id = @user_id;

-- name: Membership :one
SELECT role FROM memberships WHERE user_id = @user_id AND organisation_id = @organisation_id;

-- name: MemberOf :one
SELECT u.id AS user_id, u.email, m.role
FROM memberships m JOIN users u ON u.id = m.user_id
WHERE m.organisation_id = @organisation_id AND m.user_id = @user_id;

-- name: LockSignInKey :exec
-- Serialises reservations for one key until the transaction ends.
SELECT pg_advisory_xact_lock(hashtext(concat(sqlc.arg(lock_kind)::text, '/', sqlc.arg(lock_key)::text)));

-- name: SignInAttemptsSince :one
-- Attempts for one key in the 15-minute window, by the Postgres clock.
SELECT count(*) FROM sign_in_attempts WHERE kind = @kind AND key = @key AND at >= now() - interval '15 minutes';

-- name: LimitingSignInAttempt :one
-- How long until the attempt that keeps a key at its limit, the limit-th newest in the window,
-- leaves it; then the key is under the limit again.
SELECT EXTRACT(EPOCH FROM at + interval '15 minutes' - now())::double precision AS wait_seconds
FROM sign_in_attempts
WHERE kind = @kind AND key = @key AND at >= now() - interval '15 minutes'
ORDER BY at DESC
LIMIT 1 OFFSET @skip;

-- name: RecordSignInAttempt :one
INSERT INTO sign_in_attempts (kind, key) VALUES (@kind, @key) RETURNING id;

-- name: ForgetSignInAttempts :exec
DELETE FROM sign_in_attempts WHERE id = ANY(@ids::bigint[]);

-- name: FailedSignIns :one
-- Attempts for one key since a time, and the oldest of them.
SELECT count(*) AS failures, min(at)::timestamptz AS oldest
FROM sign_in_attempts WHERE kind = @kind AND key = @key AND at >= @since;

-- name: ForgetSignIns :exec
-- Deletes attempts past the 15-minute window, by the Postgres clock.
DELETE FROM sign_in_attempts WHERE at < now() - interval '15 minutes';

-- name: WorkspaceProjects :many
-- The projects of the organisations a user belongs to.
SELECT o.id AS organisation_id, o.name AS organisation_name, m.role, p.id AS project_id, p.name AS project_name
FROM memberships m
JOIN organisations o ON o.id = m.organisation_id
LEFT JOIN projects p ON p.organisation_id = o.id
WHERE m.user_id = @user_id
ORDER BY o.id, p.id;

-- name: StatsCalls :many
-- getCallStats' calls: every servable call of a project that started in [from_at, to_at).
SELECT id, room_name, start_at, channel, answer_ms, agent_key, models
FROM call_list
WHERE organisation_id = @organisation_id AND project_id = @project_id AND servable
  AND start_at >= @from_at AND start_at < @to_at
ORDER BY start_at, id;

-- name: ProjectConnected :one
-- When a project was connected, and the Postgres clock: buckets before the first, or after the
-- second, could hold no stored call.
SELECT created_at, now()::timestamptz AS now FROM projects WHERE organisation_id = @organisation_id AND id = @project_id;

-- name: CreateProjectKey :one
INSERT INTO project_keys (key_id, organisation_id, project_id, name, secret_sha256, created_by)
VALUES (@key_id, @organisation_id, @project_id, @name, @secret_sha256, sqlc.narg(created_by))
RETURNING created_at;

-- name: ProjectKey :one
-- The key the gate checks: its project, its hash, and whether it is revoked.
SELECT key_id, organisation_id, project_id, secret_sha256, revoked_at, last_used_at FROM project_keys WHERE key_id = @key_id;

-- name: ListProjectKeys :many
SELECT k.key_id, k.name, k.created_at, u.email AS created_by, k.last_used_at, k.revoked_at
FROM project_keys k LEFT JOIN users u ON u.id = k.created_by
WHERE k.organisation_id = @organisation_id AND k.project_id = @project_id
ORDER BY k.created_at DESC, k.key_id;

-- name: RevokeProjectKey :execrows
-- Revoking twice keeps the first time.
UPDATE project_keys SET revoked_at = COALESCE(revoked_at, now())
WHERE organisation_id = @organisation_id AND project_id = @project_id AND key_id = @key_id;

-- name: TouchProjectKey :exec
-- At most one write a minute per key, however busy, with no lock.
UPDATE project_keys SET last_used_at = now()
WHERE key_id = @key_id AND (last_used_at IS NULL OR last_used_at < now() - interval '1 minute');

-- name: KeyWorkspace :one
-- What getWorkspaces shows a project key: its organisation and its one project.
SELECT o.id AS organisation_id, o.name AS organisation_name, p.id AS project_id, p.name AS project_name
FROM projects p JOIN organisations o ON o.id = p.organisation_id
WHERE p.organisation_id = @organisation_id AND p.id = @project_id;

-- name: CreateWorkerEndpoint :one
INSERT INTO project_worker_endpoints (organisation_id, project_id, id, host, url_sealed)
VALUES ($1, $2, $3, $4, $5)
RETURNING organisation_id, project_id, id, host, created_at;

-- name: WorkerEndpoints :many
SELECT organisation_id, project_id, id, host, url_sealed, created_at
FROM project_worker_endpoints
WHERE organisation_id = $1 AND project_id = $2
ORDER BY created_at, id;

-- name: DeleteWorkerEndpoint :execrows
DELETE FROM project_worker_endpoints
WHERE organisation_id = $1 AND project_id = $2 AND id = $3;

-- name: PrincipalRows :many
-- A signed-in user with every organisation role, in one round trip: one row per membership, or
-- one row with a null organisation when there is none; no row when the user does not exist.
SELECT u.id, u.email, u.password_version, m.organisation_id, m.role
FROM users u LEFT JOIN memberships m ON m.user_id = u.id
WHERE u.id = @id
ORDER BY m.organisation_id;

-- name: UpdatePasswordHash :execrows
-- The same password hashed again with current parameters: not a new password, so password_version
-- stays and no session is signed out. Only if the hash is still the one the sign-in verified, so a
-- password reset that lands in between is never overwritten with the old password.
UPDATE users SET password_hash = @password_hash WHERE id = @id AND password_hash = @verified_hash;

-- name: RevokeOpenInvitation :exec
-- Creating a link replaces the open one for the same organisation, email and kind.
UPDATE invitations SET revoked_at = now()
WHERE organisation_id = @organisation_id AND email = @email AND kind = @kind AND accepted_at IS NULL AND revoked_at IS NULL;

-- name: LockUser :one
-- Serialises reset links for one member, made by admins of different organisations at once.
SELECT id FROM users WHERE id = @id FOR UPDATE;

-- name: RevokeOpenResets :exec
-- A new reset link replaces every open one for the member, whichever organisation's admin made it.
UPDATE invitations SET revoked_at = now()
WHERE kind = 'reset' AND user_id = @user_id AND accepted_at IS NULL AND revoked_at IS NULL;

-- name: InsertInvitation :one
INSERT INTO invitations (kind, organisation_id, email, role, user_id, token_sha256, invited_by, idempotency_key, expires_at, email_started_at)
VALUES (@kind, @organisation_id, @email, sqlc.narg(role), sqlc.narg(user_id), @token_sha256, sqlc.narg(invited_by), sqlc.narg(idempotency_key), @expires_at, sqlc.narg(email_started_at))
RETURNING id, created_at;

-- name: InvitationByIdempotencyKey :one
SELECT i.id, i.kind, i.email, i.role, i.created_at, i.expires_at, i.emailed_at, i.accepted_at, i.revoked_at, u.email AS invited_by_email, i.email_started_at, i.email_failed_at
FROM invitations i LEFT JOIN users u ON u.id = i.invited_by
WHERE i.organisation_id = @organisation_id AND i.idempotency_key = @idempotency_key;

-- name: MarkInvitationEmailed :exec
UPDATE invitations SET emailed_at = now() WHERE id = @id;

-- name: MarkInvitationNotEmailed :exec
-- The send failed. A recorded success is never overwritten.
UPDATE invitations SET email_failed_at = now() WHERE id = @id AND emailed_at IS NULL;

-- name: ListInvitations :many
SELECT i.id, i.kind, i.email, i.role, i.created_at, i.expires_at, i.emailed_at, i.accepted_at, i.revoked_at, u.email AS invited_by_email, i.email_started_at, i.email_failed_at
FROM invitations i LEFT JOIN users u ON u.id = i.invited_by
WHERE i.organisation_id = @organisation_id
ORDER BY i.created_at DESC, i.id
LIMIT 500;

-- name: RevokeInvitation :execrows
-- Revoking twice keeps the first time; an accepted link stays accepted.
UPDATE invitations SET revoked_at = COALESCE(revoked_at, now())
WHERE organisation_id = @organisation_id AND id = @id AND accepted_at IS NULL;

-- name: InvitationExists :one
SELECT EXISTS (SELECT 1 FROM invitations WHERE organisation_id = @organisation_id AND id = @id);

-- name: OpenInvitationByToken :one
-- The accept screen's read: an open, unexpired link, with its organisation's name.
SELECT i.id, i.kind, i.organisation_id, o.name AS organisation_name, i.email, i.role, i.user_id
FROM invitations i JOIN organisations o ON o.id = i.organisation_id
WHERE i.token_sha256 = @token_sha256 AND i.accepted_at IS NULL AND i.revoked_at IS NULL AND i.expires_at > now();

-- name: UseInvitation :one
-- Uses a link once: anything but one row means it was used, revoked or expired meanwhile.
UPDATE invitations SET accepted_at = now()
WHERE token_sha256 = @token_sha256 AND accepted_at IS NULL AND revoked_at IS NULL AND expires_at > now()
RETURNING id, kind, organisation_id, email, role, user_id, invited_by;

-- name: RevokeUsedLink :exec
-- A reset link that fails its guard at use is revoked, not left looking accepted.
UPDATE invitations SET accepted_at = NULL, revoked_at = now() WHERE id = @id;

-- name: OrganisationRoles :many
-- Every organisation a user belongs to, with their role: what a reset link's guard checks.
SELECT organisation_id, role FROM memberships WHERE user_id = @user_id;

-- name: SetPasswordByID :exec
-- A reset: the new password and a new password_version, signing every other session out.
UPDATE users SET password_hash = @password_hash, password_version = password_version + 1 WHERE id = @id;

-- name: CreateUserIfAbsent :one
-- A new account from an invite. A parallel accept that created the same email first wins: this
-- returns no row, and the caller signs that account in only if the password matches.
INSERT INTO users (email, password_hash) VALUES (@email, @password_hash)
ON CONFLICT (email) DO NOTHING
RETURNING id;

-- name: AddMembershipIfAbsent :exec
-- An invite for someone already a member keeps their role.
INSERT INTO memberships (user_id, organisation_id, role) VALUES (@user_id, @organisation_id, @role)
ON CONFLICT (user_id, organisation_id) DO NOTHING;

-- name: IsMemberByEmail :one
SELECT EXISTS (
    SELECT 1 FROM memberships m JOIN users u ON u.id = m.user_id
    WHERE m.organisation_id = @organisation_id AND u.email = @email
);

-- name: OrganisationName :one
SELECT name FROM organisations WHERE id = $1;

-- name: PutOrganisation :one
-- Creates an organisation with a caller-chosen id, or renames it (phase 06e). Parallel puts for one
-- id meet the primary key: exactly one inserts, and created is true for that one alone.
INSERT INTO organisations (id, name) VALUES (@id, @name)
ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name
RETURNING id, name, created_at, (xmax = 0)::boolean AS created;

-- name: OrganisationSummary :one
-- An organisation's members, admins and open invitations, counted at this read (phase 06e).
SELECT o.id, o.name, o.created_at,
    (SELECT count(*) FROM memberships m WHERE m.organisation_id = o.id) AS members,
    (SELECT count(*) FROM memberships m WHERE m.organisation_id = o.id AND m.role = 'admin') AS admins,
    (SELECT count(*) FROM invitations i
        WHERE i.organisation_id = o.id AND i.kind = 'invite' AND i.accepted_at IS NULL AND i.revoked_at IS NULL AND i.expires_at > now()) AS pending_invitations
FROM organisations o WHERE o.id = @id;

-- name: OrganisationUsage :one
-- An organisation's profiled call time in [from, to) (phase 06e, decision 0029). An observed call
-- ends at ended_at; an inferred one at its last activity, a lower bound. A call is measured when
-- its start and end are known and the end is not before the start, and counts in the period that
-- holds its end. Exact durations are summed first and the sum is rounded down to whole seconds
-- once, never per call. Open calls, and closed calls with no end or an end before their start,
-- are counted apart by their start and never summed.
WITH period_calls AS (
    SELECT started_at, end_state,
        CASE end_state WHEN 'observed' THEN ended_at WHEN 'inferred' THEN last_activity_at END AS effective_end
    FROM calls WHERE organisation_id = @organisation_id
), classified AS (
    SELECT *,
        started_at IS NOT NULL AND effective_end >= started_at
            AND effective_end >= @from_at::timestamptz AND effective_end < @to_at::timestamptz AS measured,
        started_at >= @from_at::timestamptz AND started_at < @to_at::timestamptz AS started_in_period
    FROM period_calls
)
SELECT
    COALESCE(floor(sum(extract(epoch FROM effective_end - started_at)) FILTER (WHERE measured)), 0)::bigint AS call_seconds,
    count(*) FILTER (WHERE measured) AS calls,
    count(*) FILTER (WHERE measured AND end_state = 'inferred') AS inferred_calls,
    COALESCE(floor(sum(extract(epoch FROM effective_end - started_at)) FILTER (WHERE measured AND end_state = 'inferred')), 0)::bigint AS inferred_call_seconds,
    count(*) FILTER (WHERE end_state = 'open' AND started_in_period) AS unended_calls,
    count(*) FILTER (WHERE end_state IN ('observed', 'inferred') AND started_in_period
        AND (effective_end IS NULL OR effective_end < started_at)) AS unmeasured_calls
FROM classified;

-- name: CreateResourceEndpoint :one
INSERT INTO project_resource_endpoints
(organisation_id, project_id, id, host, url_sealed, kind, target, container_id)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
RETURNING *;

-- name: ResourceEndpoints :many
SELECT * FROM project_resource_endpoints
WHERE organisation_id = $1 AND project_id = $2
ORDER BY created_at, id;

-- name: DeleteResourceEndpoint :execrows
DELETE FROM project_resource_endpoints
WHERE organisation_id = $1 AND project_id = $2 AND id = $3;

-- name: ReadProjectSettings :one
SELECT COALESCE(s.answer_ms, 1500::double precision) AS answer_ms,
       COALESCE(s.join_ms, 2000::double precision) AS join_ms,
       COALESCE(s.tts_start_ms, 1000::double precision) AS tts_start_ms,
       COALESCE(s.revision, 0::bigint) AS revision,
       s.allowed_origins,
       COALESCE(s.allowed_origins_revision, 0::bigint) AS allowed_origins_revision
FROM projects p LEFT JOIN project_settings s ON s.organisation_id = p.organisation_id AND s.project_id = p.id
WHERE p.organisation_id= @organisation_id AND p.id= @project_id;

-- name: LockSettingsProject :one
SELECT id FROM projects WHERE organisation_id= @organisation_id AND id= @project_id FOR KEY SHARE;

-- name: EnsureProjectSettings :exec
INSERT INTO project_settings(organisation_id, project_id) VALUES (@organisation_id, @project_id)
ON CONFLICT (organisation_id, project_id) DO NOTHING;

-- name: UpdateProjectSettings :one
UPDATE project_settings SET answer_ms= @answer_ms, join_ms= @join_ms, tts_start_ms= @tts_start_ms, revision = revision + 1
WHERE organisation_id= @organisation_id AND project_id= @project_id AND revision= @expected_revision
RETURNING answer_ms, join_ms, tts_start_ms, revision, allowed_origins, allowed_origins_revision;

-- name: UpdateAllowedOrigins :one
-- NULL clears the list, which allows no origin. It moves only the list's own revision, never the
-- thresholds revision the issue engine compares against.
UPDATE project_settings SET allowed_origins = sqlc.narg(allowed_origins)::text[], allowed_origins_revision = allowed_origins_revision + 1
WHERE organisation_id= @organisation_id AND project_id= @project_id AND allowed_origins_revision= @expected_revision
RETURNING answer_ms, join_ms, tts_start_ms, revision, allowed_origins, allowed_origins_revision;

-- name: EmbedGrantActive :one
-- Whether an embed token still holds, in one round trip: the project still allows its origin, and
-- the key that minted it is unrevoked, or the user who minted it is still a member, and still an
-- Admin when the token shares transcripts and audio.
SELECT (
    EXISTS (
        SELECT 1 FROM project_settings s
        WHERE s.organisation_id = @organisation_id AND s.project_id = @project_id AND @origin::text = ANY (s.allowed_origins)
    )
    AND (
        EXISTS (
            SELECT 1 FROM project_keys k
            WHERE k.key_id = @key_id::text AND k.organisation_id = @organisation_id AND k.project_id = @project_id AND k.revoked_at IS NULL
        )
        OR EXISTS (
            SELECT 1 FROM memberships m
            WHERE m.user_id = NULLIF(@user_id::text, '')::uuid AND m.organisation_id = @organisation_id
              AND (NOT @content::boolean OR m.role = 'admin')
        )
    )
)::boolean AS active;

-- name: ReadAccountProfile :one
SELECT display_name, profile_revision FROM users WHERE id = @user_id;

-- name: UpdateAccountProfile :one
UPDATE users SET display_name = sqlc.narg(display_name), profile_revision = profile_revision + 1
WHERE id = @user_id AND profile_revision = @expected_revision
RETURNING display_name, profile_revision;

-- name: ReadCallMetadata :one
SELECT tags, archived, metadata_revision FROM calls
WHERE organisation_id = sqlc.arg(organisation_id) AND project_id = sqlc.arg(project_id) AND id = sqlc.arg(call_id);

-- name: UpdateCallMetadata :one
UPDATE calls SET tags = sqlc.arg(tags), archived = sqlc.arg(archived), metadata_revision = metadata_revision + 1
WHERE organisation_id = sqlc.arg(organisation_id) AND project_id = sqlc.arg(project_id) AND id = sqlc.arg(call_id)
AND metadata_revision = sqlc.arg(expected_revision)
RETURNING tags, archived, metadata_revision;

-- name: CallMetadataRevision :one
SELECT call_metadata_revision FROM projects WHERE organisation_id = @organisation_id AND id = @project_id;

-- name: CallTags :many
SELECT DISTINCT unnest(c.tags)::text AS tag
FROM calls c JOIN call_list l ON l.id = c.id
WHERE c.organisation_id = @organisation_id AND c.project_id = @project_id AND l.servable
ORDER BY tag;

-- name: SetProjectRecordings :execrows
-- Turns a project's recordings on or off (phase 07a). Off keeps what is stored until it expires.
UPDATE projects SET recordings = @recordings WHERE organisation_id = @organisation_id AND id = @id;

-- name: InsertCallRecording :exec
-- Links a stored recording to its call. A redelivered session report names the same object, and
-- adds nothing.
INSERT INTO call_recordings (call_id, object_key, size_bytes, started_at, stored_at)
VALUES (@call_id, @object_key, @size_bytes, sqlc.narg(started_at), @stored_at)
ON CONFLICT (object_key) DO NOTHING;

-- name: RecordingByID :one
-- One recording, only within the project that owns its call, so an id from another project is not
-- found.
SELECT r.id, r.object_key, r.size_bytes, r.started_at, r.stored_at
FROM call_recordings r JOIN calls c ON c.id = r.call_id
WHERE r.id = @id AND c.organisation_id = @organisation_id AND c.project_id = @project_id;

-- name: DeleteExpiredRecordings :execrows
-- Forgets recordings stored before the cutoff; the worker removes their objects by day.
DELETE FROM call_recordings WHERE stored_at < @cutoff;

-- name: InsertProjectBucket :exec
-- The bucket a project brought for its recordings, written with the project and never updated.
INSERT INTO project_buckets (organisation_id, project_id, endpoint, use_ssl, bucket, region, access_key, secret_key_sealed)
VALUES (@organisation_id, @project_id, @endpoint, @use_ssl, @bucket, @region, @access_key, @secret_key_sealed);

-- name: ProjectBucket :one
SELECT organisation_id, project_id, endpoint, use_ssl, bucket, region, access_key, secret_key_sealed
FROM project_buckets
WHERE organisation_id = @organisation_id AND project_id = @project_id;

-- name: ListProjectBuckets :many
SELECT organisation_id, project_id, endpoint, use_ssl, bucket, region, access_key, secret_key_sealed
FROM project_buckets
ORDER BY organisation_id, project_id;

-- name: EnsureCallIssue :exec
INSERT INTO issues (organisation_id, project_id, id, title, kind, source, severity)
VALUES (@organisation_id, @project_id, @id, @title, 'event', 'calls', @severity)
ON CONFLICT (organisation_id, project_id, id) DO UPDATE SET title = EXCLUDED.title, severity = EXCLUDED.severity;

-- name: UpsertIssueOccurrence :one
-- changed reports any write; fresh only a new or reactivated occurrence, the one thing that is new evidence.
WITH prior AS (
    SELECT p.active FROM issue_occurrences p
    WHERE p.organisation_id = @organisation_id AND p.project_id = @project_id AND p.issue_id = @issue_id AND p.source_id = @source_id
), up AS (
INSERT INTO issue_occurrences (organisation_id, project_id, issue_id, source_id, call_id, occurred_at, detail, observed_at)
VALUES (@organisation_id, @project_id, @issue_id, @source_id, sqlc.narg(call_id), sqlc.narg(occurred_at), @detail, now())
ON CONFLICT (organisation_id, project_id, issue_id, source_id) DO UPDATE
SET detail = EXCLUDED.detail, occurred_at = EXCLUDED.occurred_at, active = true, observed_at = now()
WHERE NOT issue_occurrences.active OR issue_occurrences.detail IS DISTINCT FROM EXCLUDED.detail
   OR issue_occurrences.occurred_at IS DISTINCT FROM EXCLUDED.occurred_at
RETURNING 1
)
SELECT EXISTS (SELECT 1 FROM up) AS changed,
       (EXISTS (SELECT 1 FROM up) AND NOT COALESCE((SELECT active FROM prior), false)) AS fresh;

-- name: AdvanceIssueEvidence :exec
UPDATE issues SET evidence_revision = evidence_revision + 1
WHERE organisation_id = @organisation_id AND project_id = @project_id AND id = @id;

-- name: DeactivateCallFindings :execrows
UPDATE issue_occurrences SET active = false
WHERE organisation_id = @organisation_id AND project_id = @project_id AND call_id = @call_id
AND source_layer = 'calls' AND active AND NOT (issue_id = ANY(@issue_ids::text[]))
AND (sqlc.narg(evaluated_ids)::text[] IS NULL OR issue_id = ANY(sqlc.narg(evaluated_ids)::text[]));

-- name: ReadIssueMetadata :one
SELECT id, state, decided_at, evidence_revision, reviewed_evidence_revision, metadata_revision, tags, archived
FROM issues WHERE organisation_id = @organisation_id AND project_id = @project_id AND id = @id;

-- name: UpdateIssueMetadata :one
UPDATE issues SET state = COALESCE(sqlc.narg(new_state)::text, state),
    decided_at = CASE WHEN sqlc.narg(new_state)::text IS NULL THEN decided_at ELSE now() END,
    reviewed_evidence_revision = CASE WHEN sqlc.narg(new_state)::text IS NULL THEN reviewed_evidence_revision ELSE @seen_evidence_revision END,
    tags = @tags, archived = @archived, metadata_revision = metadata_revision + 1
WHERE organisation_id = @organisation_id AND project_id = @project_id AND id = @id
AND metadata_revision = @expected_revision
AND (sqlc.narg(new_state)::text IS NULL OR evidence_revision = @seen_evidence_revision)
RETURNING id, state, decided_at, evidence_revision, reviewed_evidence_revision, metadata_revision, tags, archived;

-- name: AdvanceIssueRevision :exec
UPDATE projects SET issue_revision = issue_revision + 1 WHERE organisation_id = @organisation_id AND id = @project_id;

-- name: ReadCallFindingIdentity :one
SELECT room_name, COALESCE(started_at, first_seen_at) AS occurred_at
FROM calls WHERE organisation_id = @organisation_id AND project_id = @project_id AND id = @id
FOR UPDATE;

-- name: ListIssueOccurrences :many
SELECT i.id, i.title, i.kind, i.source, i.severity,
       i.state, i.decided_at, i.evidence_revision, i.reviewed_evidence_revision,
       i.metadata_revision, i.tags, i.archived,
       o.occurred_at, o.detail, o.call_id,
       (CASE WHEN i.kind = 'standing' THEN o.observed_at ELSE NULL END)::timestamptz AS observed_at,
       CASE WHEN i.kind = 'event' THEN 'recorded'
            WHEN src.read_at IS NULL THEN 'unknown'
            WHEN NOT src.ok THEN 'unavailable'
            WHEN src.fresh_until < now() THEN 'stale'
            WHEN o.observed_at < src.good_at THEN 'unknown'
            ELSE 'current' END::text AS evidence_state
FROM issues i
JOIN issue_occurrences o ON o.organisation_id = i.organisation_id
    AND o.project_id = i.project_id AND o.issue_id = i.id
LEFT JOIN call_list c ON c.id = o.call_id AND c.organisation_id = i.organisation_id AND c.project_id = i.project_id
LEFT JOIN issue_sources src ON src.organisation_id = o.organisation_id AND src.project_id = o.project_id AND src.layer = o.source_layer
WHERE i.organisation_id = @organisation_id AND i.project_id = @project_id
  AND o.active AND (o.call_id IS NULL OR c.servable)
  AND NOT (o.source_layer = 'calls' AND o.call_id IS NULL)
  AND (i.kind = 'standing' OR (o.occurred_at >= @from_at AND o.occurred_at < @to_at))
  AND (sqlc.narg(archived)::boolean IS NULL OR i.archived = sqlc.narg(archived))
  AND (cardinality(@tags::text[]) = 0 OR i.tags && @tags::text[])
  AND (@search::text = '' OR strpos(lower(i.title), lower(@search)) > 0
       OR strpos(lower(o.detail->>'where'), lower(@search)) > 0
       OR strpos(lower(o.detail->>'detail'), lower(@search)) > 0)
ORDER BY CASE i.severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1 WHEN 'low' THEN 2 ELSE 3 END,
         i.id, o.occurred_at DESC NULLS LAST, o.source_id;

-- name: PendingCallFindings :many
SELECT c.organisation_id, c.project_id, c.id, c.issue_evaluation_revision
FROM calls c
JOIN call_list l ON l.id = c.id
LEFT JOIN project_settings s ON s.organisation_id = c.organisation_id AND s.project_id = c.project_id
WHERE l.servable AND c.summary_stale_at IS NULL
  AND (c.issue_evaluated_at IS NULL OR c.issue_settings_revision <> COALESCE(s.revision, 0))
  AND (c.issue_retry_at IS NULL OR c.issue_retry_at <= now())
ORDER BY c.issue_retry_at NULLS FIRST, c.id
LIMIT @max_calls;

-- name: RecordSummaryFindings :exec
UPDATE calls SET issue_evaluated_at = @evaluated_at, issue_settings_revision = @settings_revision,
 issue_source_available = @source_available, issue_retry_at = NULL, issue_failures = 0, issue_evaluation_revision = issue_evaluation_revision + 1
WHERE id = @id;

-- name: ClaimBackfillFindings :execrows
UPDATE calls SET issue_evaluated_at = @evaluated_at, issue_settings_revision = @settings_revision,
 issue_source_available = @source_available, issue_retry_at = NULL, issue_failures = 0, issue_evaluation_revision = issue_evaluation_revision + 1
WHERE id = @id AND organisation_id = @organisation_id AND project_id = @project_id
 AND summary_stale_at IS NULL AND issue_evaluation_revision = @expected_revision;

-- name: DeferCallFindings :exec
UPDATE calls SET issue_failures = LEAST(issue_failures + 1, 10),
 issue_retry_at = now() + LEAST(interval '10 seconds' * power(2, LEAST(issue_failures, 9)), interval '1 hour')
WHERE id = @id AND issue_evaluation_revision = @expected_revision;

-- name: ClaimIssueSource :execrows
INSERT INTO issue_sources (organisation_id, project_id, layer, read_at, good_at, fresh_until, ok, error_class, evaluation_complete)
VALUES (@organisation_id, @project_id, @layer, @read_at, CASE WHEN @ok::boolean THEN @read_at::timestamptz ELSE NULL END, @fresh_until, @ok, @error_class, @evaluation_complete)
ON CONFLICT (organisation_id, project_id, layer) DO UPDATE
SET read_at = EXCLUDED.read_at, good_at = CASE WHEN EXCLUDED.ok THEN EXCLUDED.read_at ELSE issue_sources.good_at END,
    fresh_until = EXCLUDED.fresh_until, ok = EXCLUDED.ok, error_class = EXCLUDED.error_class, evaluation_complete = EXCLUDED.evaluation_complete
WHERE issue_sources.read_at < EXCLUDED.read_at;

-- name: EnsureStandingIssue :exec
INSERT INTO issues (organisation_id, project_id, id, title, kind, source, severity)
VALUES (@organisation_id, @project_id, @id, @title, 'standing', @source, @severity)
ON CONFLICT (organisation_id, project_id, id) DO UPDATE SET title = EXCLUDED.title, severity = EXCLUDED.severity;

-- name: UpsertStandingOccurrence :one
-- changed reports any write; fresh only a new or reactivated occurrence, the one thing that is new evidence.
WITH prior AS (
    SELECT p.active FROM issue_occurrences p
    WHERE p.organisation_id = @organisation_id AND p.project_id = @project_id AND p.issue_id = @issue_id AND p.source_id = @source_id
), up AS (
INSERT INTO issue_occurrences (organisation_id, project_id, issue_id, source_id, source_layer, detail)
VALUES (@organisation_id, @project_id, @issue_id, @source_id, @source_layer, @detail)
ON CONFLICT (organisation_id, project_id, issue_id, source_id) DO UPDATE
SET detail = EXCLUDED.detail, active = true, observed_at = now()
WHERE NOT issue_occurrences.active OR issue_occurrences.detail IS DISTINCT FROM EXCLUDED.detail
RETURNING 1
)
SELECT EXISTS (SELECT 1 FROM up) AS changed,
       (EXISTS (SELECT 1 FROM up) AND NOT COALESCE((SELECT active FROM prior), false)) AS fresh;

-- name: DeactivateStandingFindings :execrows
UPDATE issue_occurrences o SET active = false
WHERE o.organisation_id = @organisation_id AND o.project_id = @project_id AND o.source_layer = @source_layer AND o.active
AND (NOT (o.source_id = ANY(@subjects::text[])) OR (
    @covered::jsonb @> jsonb_build_array(jsonb_build_object('issueId', o.issue_id, 'sourceId', o.source_id))
    AND NOT (@present::jsonb @> jsonb_build_array(jsonb_build_object('issueId', o.issue_id, 'sourceId', o.source_id)))
));

-- name: ConfirmStandingFindings :exec
UPDATE issue_occurrences o SET observed_at = @read_at
WHERE o.organisation_id = @organisation_id AND o.project_id = @project_id AND o.source_layer = @source_layer
AND @present::jsonb @> jsonb_build_array(jsonb_build_object('issueId', o.issue_id, 'sourceId', o.source_id));

-- name: UpsertJobObservation :execrows
INSERT INTO issue_job_observations (organisation_id, project_id, job_id, room_sid, dispatch_id, agent_name,
 participant_identity, dispatched_at, status, reported_failed_at, failure_observed_at, observed_at)
VALUES (@organisation_id, @project_id, @job_id, @room_sid, @dispatch_id, sqlc.narg(agent_name),
 sqlc.narg(participant_identity), sqlc.narg(dispatched_at), @status, sqlc.narg(reported_failed_at), sqlc.narg(failure_observed_at), @observed_at)
ON CONFLICT (organisation_id, project_id, job_id) DO UPDATE
SET agent_name = COALESCE(EXCLUDED.agent_name, issue_job_observations.agent_name),
 participant_identity = COALESCE(EXCLUDED.participant_identity, issue_job_observations.participant_identity),
 dispatched_at = COALESCE(EXCLUDED.dispatched_at, issue_job_observations.dispatched_at),
 status = EXCLUDED.status,
 reported_failed_at = COALESCE(issue_job_observations.reported_failed_at, EXCLUDED.reported_failed_at),
 failure_observed_at = COALESCE(issue_job_observations.failure_observed_at, EXCLUDED.failure_observed_at),
 observed_at = EXCLUDED.observed_at,
 observation_revision = issue_job_observations.observation_revision + CASE WHEN
  issue_job_observations.agent_name IS DISTINCT FROM COALESCE(EXCLUDED.agent_name, issue_job_observations.agent_name)
  OR issue_job_observations.participant_identity IS DISTINCT FROM COALESCE(EXCLUDED.participant_identity, issue_job_observations.participant_identity)
  OR issue_job_observations.dispatched_at IS DISTINCT FROM COALESCE(EXCLUDED.dispatched_at, issue_job_observations.dispatched_at)
  OR issue_job_observations.status IS DISTINCT FROM EXCLUDED.status
  OR (issue_job_observations.reported_failed_at IS NULL AND EXCLUDED.reported_failed_at IS NOT NULL)
  OR (issue_job_observations.failure_observed_at IS NULL AND EXCLUDED.failure_observed_at IS NOT NULL)
 THEN 1 ELSE 0 END
WHERE issue_job_observations.observed_at < EXCLUDED.observed_at
 AND issue_job_observations.room_sid = EXCLUDED.room_sid AND issue_job_observations.dispatch_id = EXCLUDED.dispatch_id;

-- name: PendingJobFindings :many
SELECT j.*, c.id AS call_id, p.joined_at
FROM issue_job_observations j
LEFT JOIN calls c ON c.organisation_id = j.organisation_id AND c.project_id = j.project_id AND c.room_sid = j.room_sid
LEFT JOIN LATERAL (
 SELECT min(cp.joined_at)::timestamptz AS joined_at FROM call_participants cp
 WHERE cp.call_id = c.id AND cp.kind = 'agent' AND cp.identity = j.participant_identity AND j.participant_identity <> ''
) p ON true
LEFT JOIN project_settings settings ON settings.organisation_id = j.organisation_id AND settings.project_id = j.project_id
WHERE j.evaluated_observation_revision <> j.observation_revision
 OR j.evaluated_settings_revision <> COALESCE(settings.revision, 0)
 OR j.evaluated_call_id IS DISTINCT FROM c.id OR j.evaluated_joined_at IS DISTINCT FROM p.joined_at
ORDER BY j.observed_at, j.organisation_id, j.project_id, j.job_id
LIMIT @max_jobs;

-- name: ClaimJobFindings :execrows
UPDATE issue_job_observations j SET
 evaluated_observation_revision = @observation_revision,
 evaluated_settings_revision = @settings_revision,
 evaluated_call_id = sqlc.narg(call_id), evaluated_joined_at = sqlc.narg(joined_at),
 evaluation_revision = evaluation_revision + 1
WHERE j.organisation_id = @organisation_id AND j.project_id = @project_id AND j.job_id = @job_id
 AND j.observation_revision = @observation_revision AND evaluation_revision = @expected_revision
 AND @settings_revision::bigint = COALESCE((SELECT s.revision FROM project_settings s WHERE s.organisation_id=j.organisation_id AND s.project_id=j.project_id),0);

-- name: EnsureJobIssue :exec
INSERT INTO issues (organisation_id, project_id, id, title, kind, source, severity)
VALUES (@organisation_id, @project_id, @id, @title, 'event', 'agent_workers', @severity)
ON CONFLICT (organisation_id, project_id, id) DO UPDATE SET title=EXCLUDED.title, severity=EXCLUDED.severity;

-- name: UpsertJobOccurrence :one
-- changed reports any write; fresh only a new or reactivated occurrence, the one thing that is new evidence.
WITH prior AS (
    SELECT p.active FROM issue_occurrences p
    WHERE p.organisation_id = @organisation_id AND p.project_id = @project_id AND p.issue_id = @issue_id AND p.source_id = @source_id
), up AS (
INSERT INTO issue_occurrences (organisation_id, project_id, issue_id, source_id, source_layer, call_id, occurred_at, detail)
VALUES (@organisation_id, @project_id, @issue_id, @source_id, 'jobs', sqlc.narg(call_id), sqlc.narg(occurred_at), @detail)
ON CONFLICT (organisation_id, project_id, issue_id, source_id) DO UPDATE
SET detail=EXCLUDED.detail, call_id=EXCLUDED.call_id, occurred_at=EXCLUDED.occurred_at, active=true, observed_at=now()
WHERE NOT issue_occurrences.active OR issue_occurrences.detail IS DISTINCT FROM EXCLUDED.detail
 OR issue_occurrences.call_id IS DISTINCT FROM EXCLUDED.call_id OR issue_occurrences.occurred_at IS DISTINCT FROM EXCLUDED.occurred_at
RETURNING 1
)
SELECT EXISTS (SELECT 1 FROM up) AS changed,
       (EXISTS (SELECT 1 FROM up) AND NOT COALESCE((SELECT active FROM prior), false)) AS fresh;

-- name: DeactivateJobFindings :execrows
UPDATE issue_occurrences SET active=false
WHERE organisation_id = @organisation_id AND project_id = @project_id AND source_layer='jobs' AND source_id = @source_id
 AND active AND issue_id=ANY(@covered::text[]) AND NOT(issue_id=ANY(@present::text[]));

-- name: IssueEvaluationCoverage :one
SELECT
 (SELECT count(*) FROM calls c JOIN call_list l ON l.id=c.id
  LEFT JOIN project_settings s ON s.organisation_id=c.organisation_id AND s.project_id=c.project_id
  WHERE c.organisation_id = @organisation_id AND c.project_id = @project_id AND l.servable
   AND l.start_at >= @from_at AND l.start_at < @to_at
   AND (c.summary_stale_at IS NOT NULL OR c.issue_evaluated_at IS NULL OR c.issue_settings_revision <> COALESCE(s.revision,0)))::bigint AS pending_calls,
 (SELECT count(*) FROM calls c JOIN call_list l ON l.id=c.id
  LEFT JOIN project_settings s ON s.organisation_id=c.organisation_id AND s.project_id=c.project_id
  WHERE c.organisation_id = @organisation_id AND c.project_id = @project_id AND l.servable
   AND l.start_at >= @from_at AND l.start_at < @to_at
   AND c.summary_stale_at IS NULL AND c.issue_evaluated_at IS NOT NULL
   AND c.issue_settings_revision = COALESCE(s.revision,0) AND NOT c.issue_source_available)::bigint AS unavailable_calls,
 (SELECT count(*) FROM issue_job_observations j
  LEFT JOIN calls c ON c.organisation_id=j.organisation_id AND c.project_id=j.project_id AND c.room_sid=j.room_sid
  LEFT JOIN LATERAL (SELECT min(cp.joined_at)::timestamptz AS joined_at FROM call_participants cp
   WHERE cp.call_id=c.id AND cp.kind='agent' AND cp.identity=j.participant_identity AND j.participant_identity<>'') p ON true
  LEFT JOIN project_settings s ON s.organisation_id=j.organisation_id AND s.project_id=j.project_id
  WHERE j.organisation_id = @organisation_id AND j.project_id = @project_id
   AND (j.evaluated_observation_revision <> j.observation_revision OR j.evaluated_settings_revision <> COALESCE(s.revision,0)
    OR j.evaluated_call_id IS DISTINCT FROM c.id OR j.evaluated_joined_at IS DISTINCT FROM p.joined_at))::bigint AS pending_jobs;

-- name: IssueSourceCoverage :many
SELECT layer, read_at, good_at,
 CASE WHEN NOT ok THEN 'unavailable' WHEN fresh_until < now() THEN 'stale' WHEN NOT evaluation_complete THEN 'partial'
 WHEN layer IN ('egress','ingress') AND NOT EXISTS (
 SELECT 1 FROM issue_media_backfills b WHERE b.organisation_id=issue_sources.organisation_id
 AND b.project_id=issue_sources.project_id AND b.done) THEN 'partial'
 ELSE 'current' END::text AS state
FROM issue_sources WHERE issue_sources.organisation_id = @organisation_id AND issue_sources.project_id = @project_id
ORDER BY layer;


-- name: EnsureMediaIssue :exec
INSERT INTO issues (organisation_id, project_id, id, title, kind, source, severity)
VALUES (@organisation_id, @project_id, @id, @title, 'event', 'delivery', 'high')
ON CONFLICT (organisation_id, project_id, id) DO UPDATE SET title=EXCLUDED.title;

-- name: UpsertMediaOccurrence :one
-- changed reports any write; fresh only a new or reactivated occurrence, the one thing that is new evidence.
WITH prior AS (
    SELECT p.active FROM issue_occurrences p
    WHERE p.organisation_id = @organisation_id AND p.project_id = @project_id AND p.issue_id = @issue_id AND p.source_id = @source_id
), up AS (
INSERT INTO issue_occurrences (organisation_id, project_id, issue_id, source_id, source_layer, call_id, occurred_at, detail)
VALUES (@organisation_id, @project_id, @issue_id, @source_id, @source_layer, sqlc.narg(call_id), sqlc.narg(occurred_at), @detail)
ON CONFLICT (organisation_id, project_id, issue_id, source_id) DO UPDATE
SET call_id=EXCLUDED.call_id
WHERE issue_occurrences.call_id IS NULL AND EXCLUDED.call_id IS NOT NULL
 AND issue_occurrences.detail = EXCLUDED.detail AND issue_occurrences.occurred_at IS NOT DISTINCT FROM EXCLUDED.occurred_at
RETURNING 1
)
SELECT EXISTS (SELECT 1 FROM up) AS changed,
       (EXISTS (SELECT 1 FROM up) AND NOT COALESCE((SELECT active FROM prior), false)) AS fresh;


-- name: InitMediaBackfills :exec
INSERT INTO issue_media_backfills (organisation_id, project_id)
SELECT organisation_id, id FROM projects
ON CONFLICT DO NOTHING;

-- name: PendingMediaBackfills :many
SELECT * FROM issue_media_backfills
WHERE NOT done AND retry_at <= now()
ORDER BY retry_at, organisation_id, project_id LIMIT 10;

-- name: AdvanceMediaBackfill :execrows
UPDATE issue_media_backfills SET cursor_ns= @cursor_ns, cursor_key= @cursor_key,
 done= @done, revision=revision+1, failures=0, retry_at=now()
WHERE organisation_id= @organisation_id AND project_id= @project_id AND revision= @expected_revision;

-- name: DeferMediaBackfill :exec
UPDATE issue_media_backfills SET failures=least(failures+1,12), revision=revision+1,
 retry_at=now() + least(3600, 10 * power(2,least(failures,9))) * interval '1 second'
WHERE organisation_id= @organisation_id AND project_id= @project_id AND revision= @expected_revision;

-- name: RecordConnectionAccepted :execrows
-- Selecting the project inserts nothing for a project that does not exist, so a request addressed
-- to an unknown project records nothing.
INSERT INTO project_connections (organisation_id, project_id, source, last_accepted_at)
SELECT p.organisation_id, p.id, @source, @at
FROM projects p WHERE p.organisation_id = @organisation_id AND p.id = @project_id
ON CONFLICT (organisation_id, project_id, source) DO UPDATE SET
    last_accepted_at = GREATEST(project_connections.last_accepted_at, EXCLUDED.last_accepted_at);

-- name: RecordConnectionRejected :execrows
INSERT INTO project_connections (organisation_id, project_id, source, last_rejected_at, reason, used_key)
SELECT p.organisation_id, p.id, @source, @at, @reason, sqlc.narg(used_key)
FROM projects p WHERE p.organisation_id = @organisation_id AND p.id = @project_id
ON CONFLICT (organisation_id, project_id, source) DO UPDATE SET
    last_rejected_at = EXCLUDED.last_rejected_at,
    reason           = EXCLUDED.reason,
    used_key         = EXCLUDED.used_key;

-- name: ProjectConnections :many
SELECT source, last_accepted_at, last_rejected_at, reason, used_key
FROM project_connections
WHERE organisation_id = @organisation_id AND project_id = @project_id;

-- name: FirstCall :one
-- The earliest call the worker joined that the console can open: the same servable rule as
-- ListCalls and CallByID. calls_start_at serves the order.
SELECT id, start_at::timestamptz AS started_at
FROM call_list
WHERE organisation_id = @organisation_id AND project_id = @project_id AND servable
ORDER BY start_at, id
LIMIT 1;

-- name: MergeCallFields :one
-- An SDK's custom fields onto its call (Postgres 00020); a key already there takes the new value. A
-- call holds at most 64 keys: new keys past that are dropped, in key order, and the caller counts
-- them from the fields returned.
UPDATE calls SET fields = calls.fields || (
    SELECT COALESCE(jsonb_object_agg(f.key, f.value), '{}'::jsonb)
    FROM (
        SELECT key, value FROM jsonb_each(@fields::jsonb) WHERE calls.fields ? key
        UNION ALL
        (SELECT key, value FROM jsonb_each(@fields::jsonb) WHERE NOT calls.fields ? key
         ORDER BY key
         LIMIT GREATEST(0, 64 - (SELECT count(*) FROM jsonb_object_keys(calls.fields))))
    ) f
), updated_at = now()
WHERE id = @id
RETURNING fields;

-- name: UpdateLiveKitCredentials :execrows
-- A project's new LiveKit key and sealed secret; its URL never changes.
UPDATE projects SET livekit_api_key = @livekit_api_key, livekit_api_secret_sealed = @livekit_api_secret_sealed
WHERE organisation_id = @organisation_id AND id = @id;

-- name: UserByIdentity :one
-- The account a provider subject is linked to (decision 0035).
SELECT u.id, u.email, u.password_hash, u.password_version
FROM identities i JOIN users u ON u.id = i.user_id
WHERE i.provider = @provider AND i.subject = @subject;

-- name: InsertIdentity :exec
-- Links a provider subject to an account; a subject already linked keeps its account.
INSERT INTO identities (provider, subject, user_id, email) VALUES (@provider, @subject, @user_id, @email)
ON CONFLICT (provider, subject) DO NOTHING;

-- name: IdentityOwner :one
SELECT user_id FROM identities WHERE provider = @provider AND subject = @subject;

-- name: TouchIdentity :exec
UPDATE identities SET last_used_at = now() WHERE provider = @provider AND subject = @subject;

-- name: SignInMethods :many
-- Whether an account has a password, and its linked providers, for Settings: one row with NULL
-- provider when none is linked, no row for an unknown account.
SELECT (u.password_hash IS NOT NULL)::boolean AS has_password, i.provider, i.created_at
FROM users u LEFT JOIN identities i ON i.user_id = u.id
WHERE u.id = @user_id
ORDER BY i.provider, i.created_at;
