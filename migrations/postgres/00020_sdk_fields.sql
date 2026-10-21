-- Optional SDK (decision 0033, docs/specs/python-sdk.md). project_connections gains the sdk source
-- for the Connect panel. calls.fields holds the custom fields an SDK attached to a call: string
-- keys and values, merged by the worker only (later values win), so it is apart from the user's
-- tags and their metadata revision. The GIN index serves the call list's field filter (fields @>).
-- call_list exposes it for listCalls.

-- +goose Up
ALTER TABLE project_connections DROP CONSTRAINT project_connections_source_check;
ALTER TABLE project_connections ADD CONSTRAINT project_connections_source_check CHECK (source IN ('webhook', 'agent', 'sdk'));

ALTER TABLE calls ADD COLUMN fields jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(fields) = 'object');
CREATE INDEX calls_fields ON calls USING gin (fields jsonb_path_ops);

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
       EXISTS (SELECT 1 FROM call_recordings r WHERE r.call_id = c.id) AS recorded,
       c.fields
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
       COALESCE(s.models, '{}') AS models, COALESCE(s.pipeline, 'unknown') AS pipeline, s.agent_key,
       EXISTS (SELECT 1 FROM call_recordings r WHERE r.call_id = c.id) AS recorded
FROM calls c
CROSS JOIN LATERAL (
    SELECT count(*) AS participants, count(*) FILTER (WHERE kind = 'sip') AS sip
    FROM call_participants WHERE call_id = c.id
) p
LEFT JOIN call_summaries s ON s.call_id = c.id;
DROP INDEX calls_fields;
ALTER TABLE calls DROP COLUMN fields;
DELETE FROM project_connections WHERE source = 'sdk';
ALTER TABLE project_connections DROP CONSTRAINT project_connections_source_check;
ALTER TABLE project_connections ADD CONSTRAINT project_connections_source_check CHECK (source IN ('webhook', 'agent'));
