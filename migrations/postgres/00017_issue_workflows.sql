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
