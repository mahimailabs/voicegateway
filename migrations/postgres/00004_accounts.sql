-- Built-in accounts (phase 06a, decision 0027). users holds people and their argon2id password
-- hashes as PHC strings; memberships their organisation roles (0019); sessions is the table
-- scs/pgxstore reads and writes; sign_in_attempts counts failures per email and per client address.

-- +goose Up
CREATE TABLE users (
    id            uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    -- Case-folded on write, so one address is one account.
    email         text        NOT NULL UNIQUE CHECK (email = lower(email)),
    password_hash text        NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE memberships (
    user_id         uuid NOT NULL REFERENCES users (id),
    organisation_id text NOT NULL REFERENCES organisations (id),
    role            text NOT NULL CHECK (role IN ('admin', 'member', 'viewer')),
    PRIMARY KEY (user_id, organisation_id)
);

-- The shape scs/pgxstore expects (its README); its own cleanup deletes expired rows.
CREATE TABLE sessions (
    token  text        PRIMARY KEY,
    data   bytea       NOT NULL,
    expiry timestamptz NOT NULL
);
CREATE INDEX sessions_expiry_idx ON sessions (expiry);

-- One row per sign-in attempt, reserved before the password is checked so a burst cannot pass
-- the limit together; a successful sign-in deletes its own rows. kind is email or address; key the
-- case-folded email or the client address (an IPv6 address by its /64). Old rows are deleted as
-- the window moves.
CREATE TABLE sign_in_attempts (
    id   bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kind text        NOT NULL CHECK (kind IN ('email', 'address')),
    key  text        NOT NULL,
    at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX sign_in_attempts_key ON sign_in_attempts (kind, key, at);
-- For deleting attempts past the window.
CREATE INDEX sign_in_attempts_at ON sign_in_attempts (at);
