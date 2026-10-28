-- Whether a link's email went out, kept so a repeat of an idempotent request answers with the truth
-- (phase 06e). email_started_at is set with the row when SMTP is set, before the send; emailed_at
-- or email_failed_at records the outcome. A row with only email_started_at is a send in flight, or
-- one whose outcome was lost: a repeat waits out the first, and reports the second as unknown,
-- never as not sent, so a caller does not send the same invitation twice.

-- +goose Up
ALTER TABLE invitations
    ADD COLUMN email_started_at timestamptz,
    ADD COLUMN email_failed_at  timestamptz,
    ADD CHECK (emailed_at IS NULL OR email_failed_at IS NULL);
