-- +goose Up
CREATE TABLE issues (
    organisation_id text NOT NULL,
    project_id text NOT NULL,
    id text NOT NULL,
    title text NOT NULL,
    kind text NOT NULL CHECK (kind IN ('event', 'standing')),
    source text NOT NULL CHECK (source IN ('calls', 'agent_workers', 'phone_bridge', 'delivery')),
    severity text NOT NULL CHECK (severity IN ('high', 'medium', 'low', 'info')),
    evidence_revision bigint NOT NULL DEFAULT 0 CHECK (evidence_revision BETWEEN 0 AND 9007199254740991),
    metadata_revision bigint NOT NULL DEFAULT 0 CHECK (metadata_revision BETWEEN 0 AND 9007199254740991),
    state text NOT NULL DEFAULT 'open' CHECK (state IN ('open', 'resolved', 'ignored')),
    reviewed_evidence_revision bigint NOT NULL DEFAULT 0 CHECK (reviewed_evidence_revision BETWEEN 0 AND 9007199254740991),
    decided_at timestamptz,
    tags text[] NOT NULL DEFAULT '{}' CHECK (cardinality(tags) <= 32),
    archived boolean NOT NULL DEFAULT false,
    PRIMARY KEY (organisation_id, project_id, id),
    FOREIGN KEY (organisation_id, project_id) REFERENCES projects (organisation_id, id) ON DELETE CASCADE
);
CREATE TABLE issue_occurrences (
    organisation_id text NOT NULL,
    project_id text NOT NULL,
    issue_id text NOT NULL,
    source_id text NOT NULL,
    call_id uuid REFERENCES calls (id) ON DELETE CASCADE,
    occurred_at timestamptz,
    detail jsonb NOT NULL,
    active boolean NOT NULL DEFAULT true,
    observed_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (organisation_id, project_id, issue_id, source_id),
    FOREIGN KEY (organisation_id, project_id, issue_id) REFERENCES issues (organisation_id, project_id, id) ON DELETE CASCADE
);
CREATE INDEX issue_occurrences_call ON issue_occurrences (call_id);
CREATE INDEX issue_occurrences_scope_time ON issue_occurrences (organisation_id, project_id, occurred_at DESC) WHERE active;
CREATE INDEX issues_tags ON issues USING gin (tags);
ALTER TABLE projects ADD COLUMN issue_revision bigint NOT NULL DEFAULT 0;
ALTER TABLE calls ADD COLUMN issue_evaluated_at timestamptz;
ALTER TABLE calls ADD COLUMN issue_settings_revision bigint NOT NULL DEFAULT -1;
ALTER TABLE calls ADD COLUMN issue_source_available boolean NOT NULL DEFAULT false;
ALTER TABLE calls ADD COLUMN issue_retry_at timestamptz;
ALTER TABLE calls ADD COLUMN issue_failures integer NOT NULL DEFAULT 0;
ALTER TABLE calls ADD COLUMN issue_evaluation_revision bigint NOT NULL DEFAULT 0;
ALTER TABLE issue_occurrences ADD COLUMN source_layer text NOT NULL DEFAULT 'calls';
CREATE TABLE issue_sources (
    organisation_id text NOT NULL,
    project_id text NOT NULL,
    layer text NOT NULL,
    read_at timestamptz NOT NULL,
    good_at timestamptz,
    fresh_until timestamptz NOT NULL,
    evaluation_complete boolean NOT NULL DEFAULT false,
    ok boolean NOT NULL,
    error_class text NOT NULL,
    PRIMARY KEY (organisation_id, project_id, layer),
    FOREIGN KEY (organisation_id, project_id) REFERENCES projects (organisation_id, id) ON DELETE CASCADE
);

-- Job observations survive room closure so a late participant webhook can still establish the
-- exact dispatch-to-join interval. Identity is server-side correlation data, never API output.
CREATE TABLE issue_job_observations (
    organisation_id text NOT NULL,
    project_id text NOT NULL,
    job_id text NOT NULL,
    room_sid text NOT NULL,
    dispatch_id text NOT NULL,
    agent_name text,
    participant_identity text,
    dispatched_at timestamptz,
    status text NOT NULL,
    reported_failed_at timestamptz,
    failure_observed_at timestamptz,
    observed_at timestamptz NOT NULL,
    observation_revision bigint NOT NULL DEFAULT 0,
    evaluated_observation_revision bigint NOT NULL DEFAULT -1,
    evaluation_revision bigint NOT NULL DEFAULT 0,
    evaluated_settings_revision bigint NOT NULL DEFAULT -1,
    evaluated_call_id uuid,
    evaluated_joined_at timestamptz,
    PRIMARY KEY (organisation_id, project_id, job_id),
    FOREIGN KEY (organisation_id, project_id) REFERENCES projects (organisation_id, id) ON DELETE CASCADE
);
CREATE INDEX issue_job_observations_room ON issue_job_observations (organisation_id, project_id, room_sid);

-- Nanosecond cursor matches ClickHouse DateTime64(9), without Postgres timestamp truncation.
CREATE TABLE issue_recording_backfills (
 organisation_id text NOT NULL,
 project_id text NOT NULL,
 cursor_ns bigint NOT NULL DEFAULT 0,
 cursor_key text NOT NULL DEFAULT '',
 revision bigint NOT NULL DEFAULT 0,
 done boolean NOT NULL DEFAULT false,
 retry_at timestamptz NOT NULL DEFAULT now(),
 failures integer NOT NULL DEFAULT 0,
 PRIMARY KEY (organisation_id, project_id),
 FOREIGN KEY (organisation_id, project_id) REFERENCES projects (organisation_id, id) ON DELETE CASCADE
);
