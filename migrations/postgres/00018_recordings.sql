-- Opt-in call recordings (phase 07a, decisions 0008 and 0015). projects.recordings turns them on
-- per project; off by default, so an upload's audio is discarded until someone turns it on.
-- call_recordings links a call to the audio object web stored in the S3 bucket. The audio itself
-- never enters Postgres. object_key is unique, so a redelivered session report adds no second row.
-- started_at is the recording's wall-clock start from the upload header, null when unreported.
-- stored_at is when web stored the object; the worker deletes rows and objects 90 days after it,
-- the raw call data retention of ClickHouse 00010. call_list gains recorded, the one rule for
-- which calls listCalls and its totals count as recorded.

-- +goose Up
ALTER TABLE projects ADD COLUMN recordings boolean NOT NULL DEFAULT false;

CREATE TABLE call_recordings (
    id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    call_id     uuid        NOT NULL REFERENCES calls (id),
    object_key  text        NOT NULL UNIQUE CHECK (object_key <> ''),
    size_bytes  bigint      NOT NULL CHECK (size_bytes >= 0),
    started_at  timestamptz,
    stored_at   timestamptz NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX call_recordings_call ON call_recordings (call_id, stored_at);
CREATE INDEX call_recordings_stored ON call_recordings (stored_at);

CREATE OR REPLACE VIEW call_list AS
SELECT c.organisation_id, c.project_id, c.id, c.room_name, c.room_sid, c.job_id, c.started_at, c.ended_at,
       c.end_state, c.created_at, c.first_seen_at,
       (COALESCE(c.room_name, '') <> '' AND COALESCE(c.started_at, c.first_seen_at) IS NOT NULL) AS servable,
       (s.call_id IS NOT NULL) AS summarized,
       COALESCE(c.started_at, c.first_seen_at) AS start_at,
       CASE WHEN c.ended_at IS NOT NULL AND c.started_at IS NOT NULL
            THEN EXTRACT(EPOCH FROM c.ended_at - c.started_at)::double precision END AS duration_seconds,
       p.participants,
       CASE WHEN p.sip > 0 THEN 'phone' WHEN p.participants > 0 THEN 'web' ELSE 'unknown' END AS channel,
       s.turn_count, s.answer_ms, s.answer_ms_median,
       COALESCE(s.models, '{}') AS models, COALESCE(s.pipeline, 'unknown') AS pipeline, s.agent_key,
       EXISTS (SELECT 1 FROM call_recordings r WHERE r.call_id = c.id) AS recorded
FROM calls c
CROSS JOIN LATERAL (
    SELECT count(*) AS participants, count(*) FILTER (WHERE kind = 'sip') AS sip
    FROM call_participants WHERE call_id = c.id
) p
LEFT JOIN call_summaries s ON s.call_id = c.id;

-- +goose Down
-- A view cannot drop a column in place.
DROP VIEW call_list;
CREATE VIEW call_list AS
SELECT c.organisation_id, c.project_id, c.id, c.room_name, c.room_sid, c.job_id, c.started_at, c.ended_at,
       c.end_state, c.created_at, c.first_seen_at,
       (COALESCE(c.room_name, '') <> '' AND COALESCE(c.started_at, c.first_seen_at) IS NOT NULL) AS servable,
       (s.call_id IS NOT NULL) AS summarized,
       COALESCE(c.started_at, c.first_seen_at) AS start_at,
       CASE WHEN c.ended_at IS NOT NULL AND c.started_at IS NOT NULL
            THEN EXTRACT(EPOCH FROM c.ended_at - c.started_at)::double precision END AS duration_seconds,
       p.participants,
       CASE WHEN p.sip > 0 THEN 'phone' WHEN p.participants > 0 THEN 'web' ELSE 'unknown' END AS channel,
       s.turn_count, s.answer_ms, s.answer_ms_median,
       COALESCE(s.models, '{}') AS models, COALESCE(s.pipeline, 'unknown') AS pipeline, s.agent_key
FROM calls c
CROSS JOIN LATERAL (
    SELECT count(*) AS participants, count(*) FILTER (WHERE kind = 'sip') AS sip
    FROM call_participants WHERE call_id = c.id
) p
LEFT JOIN call_summaries s ON s.call_id = c.id;
DROP TABLE call_recordings;
ALTER TABLE projects DROP COLUMN recordings;
