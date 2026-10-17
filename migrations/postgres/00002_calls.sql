-- Calls and their participants (phase 04a, decision 0012). A call is one room instance, keyed by
-- its room SID; the job id joins a source that names no SID. Every time is nullable: a time no
-- source reported stays null, never the time the worker processed it.

-- +goose Up
CREATE TABLE calls (
    id               uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    organisation_id  text        NOT NULL,
    project_id       text        NOT NULL,
    room_name        text,
    room_sid         text,
    job_id           text,
    started_at       timestamptz,
    ended_at         timestamptz,
    end_state        text        NOT NULL DEFAULT 'open' CHECK (end_state IN ('open', 'observed', 'inferred')),
    -- The latest time any joined source reported for the call; the end of an inferred call.
    last_activity_at timestamptz,
    -- When the worker last joined anything to the call. Only the 2-hour inferred-end timer reads it.
    updated_at       timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (organisation_id, project_id) REFERENCES projects (organisation_id, id)
);

CREATE UNIQUE INDEX calls_room_sid ON calls (organisation_id, project_id, room_sid) WHERE room_sid IS NOT NULL;
CREATE INDEX calls_job_id ON calls (organisation_id, project_id, job_id) WHERE job_id IS NOT NULL;
-- A call a span opened by its job id alone, before any source named the room SID: one per job.
CREATE UNIQUE INDEX calls_job_only ON calls (organisation_id, project_id, job_id) WHERE room_sid IS NULL AND job_id IS NOT NULL;
CREATE INDEX calls_room_name ON calls (organisation_id, project_id, room_name, started_at);
CREATE INDEX calls_open ON calls (updated_at) WHERE end_state = 'open';

CREATE TABLE call_participants (
    call_id         uuid        NOT NULL REFERENCES calls (id),
    participant_sid text        NOT NULL,
    identity        text        NOT NULL DEFAULT '',
    -- LiveKit's ParticipantInfo.Kind, lower case. proto3 omits the default, STANDARD.
    kind            text        NOT NULL CHECK (kind IN ('standard', 'ingress', 'egress', 'sip', 'agent', 'connector', 'bridge')),
    joined_at       timestamptz,
    left_at         timestamptz,
    PRIMARY KEY (call_id, participant_sid)
);
