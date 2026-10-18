-- Project API keys (phase 06c, decision 0027). A key is vgk_<key_id>_<secret>: key_id finds the
-- row and is shown in lists; only the SHA-256 of the secret is stored. A project's keys go with it.

-- +goose Up
CREATE TABLE project_keys (
    key_id          text        PRIMARY KEY CHECK (key_id ~ '^[a-z2-7]{12}$'),
    organisation_id text        NOT NULL,
    project_id      text        NOT NULL,
    name            text        NOT NULL CHECK (char_length(name) BETWEEN 1 AND 64),
    secret_sha256   bytea       NOT NULL CHECK (octet_length(secret_sha256) = 32),
    -- Null when the key was made from the command line.
    created_by      uuid        REFERENCES users (id) ON DELETE SET NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    last_used_at    timestamptz,
    revoked_at      timestamptz,
    FOREIGN KEY (organisation_id, project_id) REFERENCES projects (organisation_id, id) ON DELETE CASCADE
);
CREATE INDEX project_keys_project ON project_keys (organisation_id, project_id, created_at);
