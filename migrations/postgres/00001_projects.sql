-- Organisations and projects (decisions 0016, 0017). The open-source edition is one organisation,
-- created here as "default"; a project is one LiveKit project.

-- +goose Up
CREATE TABLE organisations (
    id         text        PRIMARY KEY CHECK (id ~ '^[a-z0-9][a-z0-9-]{0,62}$'),
    name       text        NOT NULL CHECK (name <> ''),
    created_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO organisations (id, name) VALUES ('default', 'Your organization');

CREATE TABLE projects (
    organisation_id           text        NOT NULL REFERENCES organisations (id),
    id                        text        NOT NULL CHECK (id ~ '^[a-z0-9][a-z0-9-]{0,62}$'),
    name                      text        NOT NULL CHECK (name <> ''),
    livekit_url               text        NOT NULL CHECK (livekit_url <> ''),
    -- Ingest finds the project by the LiveKit key that signed a webhook or upload, so it is unique.
    livekit_api_key           text        NOT NULL UNIQUE CHECK (livekit_api_key <> ''),
    -- AES-256-GCM under VOICEGATEWAY_SECRET_KEY, bound to organisation and project id. Never read
    -- back to the dashboard.
    livekit_api_secret_sealed bytea       NOT NULL,
    created_at                timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (organisation_id, id)
);
