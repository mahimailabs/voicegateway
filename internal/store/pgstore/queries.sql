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

-- name: ListProjects :many
SELECT organisation_id, id, name, livekit_url, livekit_api_key, created_at
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
SELECT organisation_id, id, name, livekit_url, livekit_api_key, created_at
FROM projects
WHERE organisation_id = $1 AND id = $2;

-- name: CallByID :one
-- One call of a project as the API reads it, with the servable rule listCalls shares.
SELECT l.id, l.room_name, l.room_sid, l.job_id, l.started_at, l.ended_at, l.end_state, l.first_seen_at,
       l.participants, l.servable, l.summarized::boolean AS summarized
FROM call_list l
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
    SELECT l.*,
        CASE @sort_by::text
            WHEN 'startedAt'       THEN EXTRACT(EPOCH FROM l.start_at)::double precision
            WHEN 'durationSeconds' THEN l.duration_seconds
            WHEN 'turnCount'       THEN l.turn_count::double precision
            WHEN 'answerMs'        THEN l.answer_ms_median
        END * CASE WHEN @descending::bool THEN -1 ELSE 1 END AS sort_key
    FROM call_list l
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
      AND NOT @match_none::bool
)
SELECT f.id, f.room_name, f.room_sid, f.start_at, f.started_at, f.ended_at, f.end_state, f.duration_seconds,
       f.participants, f.channel, f.turn_count, f.answer_ms, f.answer_ms_median, f.models, f.pipeline, f.agent_key,
       (f.sort_key IS NULL) AS key_unknown, COALESCE(f.sort_key, 0)::double precision AS sort_value
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
    FROM call_list l
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
SELECT id, email, password_hash FROM users WHERE email = @email;

-- name: UserByID :one
SELECT id, email FROM users WHERE id = @id;

-- name: Memberships :many
SELECT organisation_id, role FROM memberships WHERE user_id = @user_id ORDER BY organisation_id;

-- name: Membership :one
SELECT role FROM memberships WHERE user_id = @user_id AND organisation_id = @organisation_id;

-- name: RecordFailedSignIn :exec
INSERT INTO sign_in_attempts (kind, key) VALUES (@kind, @key);

-- name: FailedSignIns :one
-- Failures for one key since a time, and the oldest of them, for Retry-After.
SELECT count(*) AS failures, min(at)::timestamptz AS oldest
FROM sign_in_attempts WHERE kind = @kind AND key = @key AND at >= @since;

-- name: ForgetSignIns :exec
DELETE FROM sign_in_attempts WHERE at < @before;

-- name: WorkspaceProjects :many
-- The projects of the organisations a user belongs to.
SELECT o.id AS organisation_id, o.name AS organisation_name, m.role, p.id AS project_id, p.name AS project_name
FROM memberships m
JOIN organisations o ON o.id = m.organisation_id
LEFT JOIN projects p ON p.organisation_id = o.id
WHERE m.user_id = @user_id
ORDER BY o.id, p.id;
