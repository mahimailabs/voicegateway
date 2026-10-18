-- Invitations and password reset links (phase 06d, decision 0027). A link's token is stored only as
-- SHA-256. Its state is derived, never stored: accepted, revoked, expired by expires_at, or pending.
-- One open link per organisation, email and kind: an index cannot compare expires_at with now(), so
-- an expired, unrevoked row still counts as open, and creating a link revokes the open one first.

-- +goose Up
CREATE TABLE invitations (
    id              uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    kind            text        NOT NULL CHECK (kind IN ('invite', 'reset')),
    organisation_id text        NOT NULL REFERENCES organisations (id) ON DELETE CASCADE,
    email           text        NOT NULL CHECK (email = lower(email)),
    -- Null for a reset.
    role            text        CHECK (role IN ('admin', 'member', 'viewer')),
    -- The member a reset is for; null for an invite.
    user_id         uuid        REFERENCES users (id) ON DELETE CASCADE,
    token_sha256    bytea       NOT NULL UNIQUE CHECK (octet_length(token_sha256) = 32),
    -- Null for the instance API and the command line, or once the inviter's account is gone.
    invited_by      uuid        REFERENCES users (id) ON DELETE SET NULL,
    idempotency_key text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    expires_at      timestamptz NOT NULL,
    emailed_at      timestamptz,
    accepted_at     timestamptz,
    revoked_at      timestamptz,
    CHECK ((kind = 'invite' AND role IS NOT NULL AND user_id IS NULL) OR (kind = 'reset' AND role IS NULL AND user_id IS NOT NULL))
);
CREATE UNIQUE INDEX invitations_open ON invitations (organisation_id, email, kind) WHERE accepted_at IS NULL AND revoked_at IS NULL;
CREATE UNIQUE INDEX invitations_idempotency ON invitations (organisation_id, idempotency_key) WHERE idempotency_key IS NOT NULL;
CREATE INDEX invitations_list ON invitations (organisation_id, kind, created_at);
