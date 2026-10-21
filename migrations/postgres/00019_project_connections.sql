-- How each project's ingestion last went, per source (docs/specs/first-call-setup.md). web writes
-- it as request state when it accepts or refuses a request addressed to the project, at most once
-- per outcome every 30 s. A refusal keeps its reason code and, for key_mismatch only, the LiveKit
-- API key the token named (keys are not secret); no secret, token or body is ever stored. Times
-- stay null until that outcome happens.

-- +goose Up
CREATE TABLE project_connections (
    organisation_id  text        NOT NULL,
    project_id       text        NOT NULL,
    source           text        NOT NULL CHECK (source IN ('webhook', 'agent')),
    last_accepted_at timestamptz,
    last_rejected_at timestamptz,
    reason           text        CHECK (reason IN ('token_missing', 'key_mismatch', 'signature_invalid', 'grant_missing', 'body_changed', 'plan_inactive', 'plan_cap', 'too_large')),
    used_key         text        CHECK (octet_length(used_key) <= 64),
    PRIMARY KEY (organisation_id, project_id, source),
    FOREIGN KEY (organisation_id, project_id) REFERENCES projects (organisation_id, id) ON DELETE CASCADE
);

-- +goose Down
DROP TABLE project_connections;
