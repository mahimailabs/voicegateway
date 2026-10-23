-- Google, GitHub and Auth0 sign-in (phase 06h, decisions 0035 and 0036). identities links a
-- provider's stable subject (Google's sub, GitHub's numeric user id, Auth0's user id) to an
-- account, so once linked the subject signs the person in, never the email the provider reports
-- later; email is the verified one at link time, kept for Settings. An account made by accepting an
-- invitation with a provider has no password: users.password_hash is NULL until a reset link sets
-- one.

-- +goose Up
CREATE TABLE identities (
    provider     text        NOT NULL CHECK (provider IN ('google', 'github', 'auth0')),
    subject      text        NOT NULL CHECK (subject <> ''),
    user_id      uuid        NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    email        text        NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    last_used_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (provider, subject)
);
CREATE INDEX identities_user ON identities (user_id);

ALTER TABLE users ALTER COLUMN password_hash DROP NOT NULL;

-- +goose Down
-- An account with no password cannot be kept under NOT NULL: Down refuses rather than invent one.
ALTER TABLE users ALTER COLUMN password_hash SET NOT NULL;
DROP TABLE identities;
