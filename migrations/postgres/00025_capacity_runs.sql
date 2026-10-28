-- Capacity test runs (decision 0022, docs/specs/capacity-runner.md). web writes a request; a worker
-- claims it, heartbeats it while it runs and writes each step as it lands. A run is open until it
-- ends, and a project has at most one open run. A value the run did not measure stays null: a
-- capacity of null means no step passed, never a capacity of zero.

-- +goose Up
CREATE TABLE capacity_runs (
    id                uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    organisation_id   text        NOT NULL,
    project_id        text        NOT NULL,
    agent             text        NOT NULL CHECK (octet_length(agent) BETWEEN 1 AND 256),
    max_step          integer     NOT NULL CHECK (max_step IN (8, 16, 32, 64)),
    requested_by      text,
    requested_at      timestamptz NOT NULL,
    state             text        NOT NULL DEFAULT 'requested' CHECK (state IN ('requested', 'running', 'ended')),
    runner            text,
    started_at        timestamptz,
    heartbeat_at      timestamptz,
    stop_requested    boolean     NOT NULL DEFAULT false,
    ended_at          timestamptz,
    -- The join threshold the run classified late joins with, read from the project's settings at start.
    join_threshold_ms integer,
    -- Whether the agent is a LiveKit Cloud agent; null when the run could not tell.
    cloud_agent       boolean,
    capacity          integer     CHECK (capacity > 0),
    limited_by        text        CHECK (limited_by IN ('pending', 'job_failed', 'answer', 'tester', 'plan', 'real_call', 'agent_busy', 'stopped', 'runner_lost', 'failed', 'max_reached')),
    -- The class of a LiveKit or storage error that ended the run as failed; never a message body.
    error             text        CHECK (octet_length(error) <= 64),
    CHECK ((state = 'ended') = (ended_at IS NOT NULL AND limited_by IS NOT NULL)),
    FOREIGN KEY (organisation_id, project_id) REFERENCES projects (organisation_id, id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX capacity_runs_one_open ON capacity_runs (organisation_id, project_id) WHERE state <> 'ended';
CREATE INDEX capacity_runs_agent ON capacity_runs (organisation_id, project_id, agent, requested_at DESC);
CREATE INDEX capacity_runs_waiting ON capacity_runs (requested_at) WHERE state = 'requested';

CREATE TABLE capacity_steps (
    run_id             uuid             NOT NULL REFERENCES capacity_runs (id) ON DELETE CASCADE,
    step               integer          NOT NULL CHECK (step > 0),
    started_at         timestamptz      NOT NULL,
    ended_at           timestamptz,
    outcome            text             NOT NULL CHECK (outcome IN ('running', 'passed', 'failed', 'inconclusive')),
    calls              integer          NOT NULL CHECK (calls >= 0),
    joined             integer          NOT NULL CHECK (joined >= 0),
    pending            integer          NOT NULL CHECK (pending >= 0),
    failed             integer          NOT NULL CHECK (failed >= 0),
    late               integer          NOT NULL CHECK (late >= 0),
    join_seconds       double precision[] NOT NULL,
    answer_seconds     double precision[] NOT NULL,
    no_answer          integer          NOT NULL CHECK (no_answer >= 0),
    tester_cpu_percent double precision CHECK (tester_cpu_percent BETWEEN 0 AND 100),
    PRIMARY KEY (run_id, step)
);

-- A capacity test's rooms are joined like any call, so nothing they send is lost, but they are never
-- served: servable is the one rule Calls, call stats, Issues, alert findings and the first call
-- share (decision 0022).
CREATE OR REPLACE VIEW call_list AS
SELECT c.organisation_id, c.project_id, c.id, c.room_name, c.room_sid, c.job_id, c.started_at, c.ended_at,
       c.end_state, c.created_at, c.first_seen_at,
       (COALESCE(c.room_name, '') <> '' AND COALESCE(c.started_at, c.first_seen_at) IS NOT NULL
        AND c.room_name NOT LIKE 'vg-capacity-%') AS servable,
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
DROP TABLE capacity_steps;
DROP TABLE capacity_runs;
