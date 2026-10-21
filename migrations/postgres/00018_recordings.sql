-- Opt-in call recordings (phase 07a, decisions 0008 and 0015). projects.recordings turns them on
-- per project; off by default, so an upload's audio is discarded until someone turns it on.
-- call_recordings links a call to the audio object web stored in the S3 bucket. The audio itself
-- never enters Postgres. object_key is unique, so a redelivered session report adds no second row.
-- started_at is the recording's wall-clock start from the upload header, null when unreported.
-- stored_at is when web stored the object; the worker deletes rows and objects 90 days after it,
-- the raw call data retention of ClickHouse 00010. call_list gains recorded, the one rule for
-- which calls listCalls and its totals count as recorded.
-- project_buckets holds the S3 bucket a project brought for its recordings, chosen when the project
-- is created; a project without a row uses the instance's bucket (VOICEGATEWAY_S3_*). The row never
-- changes, so a project's recordings never move between buckets: a trigger refuses every update.

-- +goose Up
ALTER TABLE projects ADD COLUMN recordings boolean NOT NULL DEFAULT false;

CREATE TABLE call_recordings (
    id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    call_id     uuid        NOT NULL REFERENCES calls (id) ON DELETE CASCADE,
    object_key  text        NOT NULL UNIQUE CHECK (object_key <> ''),
    size_bytes  bigint      NOT NULL CHECK (size_bytes >= 0),
    started_at  timestamptz,
    stored_at   timestamptz NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX call_recordings_call ON call_recordings (call_id, stored_at);
CREATE INDEX call_recordings_stored ON call_recordings (stored_at);

CREATE TABLE project_buckets (
    organisation_id   text        NOT NULL,
    project_id        text        NOT NULL,
    -- Host and optional port, as minio-go dials it; use_ssl is the scheme the owner gave.
    endpoint          text        NOT NULL CHECK (endpoint <> ''),
    use_ssl           boolean     NOT NULL,
    bucket            text        NOT NULL CHECK (bucket <> ''),
    -- Empty when not given: the S3 client asks the bucket for it.
    region            text        NOT NULL DEFAULT '',
    access_key        text        NOT NULL CHECK (access_key <> ''),
    -- AES-256-GCM under VOICEGATEWAY_SECRET_KEY, bound to organisation and project id. Never read
    -- back to the dashboard.
    secret_key_sealed bytea       NOT NULL,
    created_at        timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (organisation_id, project_id),
    FOREIGN KEY (organisation_id, project_id) REFERENCES projects (organisation_id, id) ON DELETE CASCADE
);

-- +goose StatementBegin
CREATE FUNCTION project_buckets_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'a project''s recording bucket is set when the project is created and never changes';
END
$$;
-- +goose StatementEnd
CREATE TRIGGER project_buckets_immutable BEFORE UPDATE ON project_buckets
    FOR EACH ROW EXECUTE FUNCTION project_buckets_immutable();

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
DROP TABLE project_buckets;
DROP FUNCTION project_buckets_immutable();
DROP TABLE call_recordings;
ALTER TABLE projects DROP COLUMN recordings;
