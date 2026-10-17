-- What listCalls reads without ClickHouse (phase 04d). first_seen_at is the earliest time any joined
-- source reported for the call; created_at is when the row was written, used only to hold a list
-- snapshot; summary_stale_at marks a call whose summary needs rebuilding. call_summaries holds the
-- per-call figures the worker builds from the stored spans with turn.Build.

-- +goose Up
ALTER TABLE calls
    ADD COLUMN first_seen_at    timestamptz,
    ADD COLUMN created_at       timestamptz NOT NULL DEFAULT now(),
    ADD COLUMN summary_stale_at timestamptz;

-- Every call stored before this migration needs a summary.
UPDATE calls SET summary_stale_at = now();

CREATE INDEX calls_summary_stale ON calls (summary_stale_at) WHERE summary_stale_at IS NOT NULL;
CREATE INDEX calls_list ON calls (organisation_id, project_id, created_at);

CREATE TABLE call_summaries (
    call_id          uuid        PRIMARY KEY REFERENCES calls (id),
    -- Null when the call has no agent_turn span: unknown, not zero.
    turn_count       integer,
    -- Measured answer times in turn order; null when no turn measured one.
    answer_ms        double precision[],
    answer_ms_median double precision,
    models           text[]      NOT NULL DEFAULT '{}',
    pipeline         text        NOT NULL DEFAULT 'unknown' CHECK (pipeline IN ('cascaded', 'realtime', 'text', 'unknown')),
    agent_key        text,
    computed_at      timestamptz NOT NULL
);

-- One row per call a list can serve: room and start known after the fallbacks, the rule getCall's
-- 404 shares. channel is phone with a SIP participant, web with webhook participants and no SIP,
-- unknown without any; durationSeconds needs an observed start.
CREATE VIEW call_list AS
SELECT c.organisation_id, c.project_id, c.id, c.room_name, c.room_sid, c.started_at, c.ended_at,
       c.end_state, c.created_at,
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
LEFT JOIN call_summaries s ON s.call_id = c.id
WHERE COALESCE(c.room_name, '') <> '' AND COALESCE(c.started_at, c.first_seen_at) IS NOT NULL;
