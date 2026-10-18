-- Each project's self-hosted agent worker metrics endpoints (phase 05d), listed with
-- `voicegateway project workers add`. The URL is sealed like the LiveKit secret (AES-256-GCM under
-- VOICEGATEWAY_SECRET_KEY, bound to organisation, project and endpoint id), since its user part,
-- path or query can carry a credential; host keeps only its scheme and host for display.

-- +goose Up
CREATE TABLE project_worker_endpoints (
    organisation_id text        NOT NULL,
    project_id      text        NOT NULL,
    id              text        NOT NULL CHECK (id ~ '^we_[a-z0-9]{12}$'),
    host            text        NOT NULL CHECK (host <> ''),
    url_sealed      bytea       NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (organisation_id, project_id, id),
    FOREIGN KEY (organisation_id, project_id) REFERENCES projects (organisation_id, id) ON DELETE CASCADE
);
