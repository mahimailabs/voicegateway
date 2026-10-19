-- +goose Up
CREATE TABLE organisation_entitlements (
    organisation_id text PRIMARY KEY REFERENCES organisations(id),
    from_at timestamptz NOT NULL,
    to_at timestamptz NOT NULL,
    limit_minutes bigint NOT NULL CHECK (limit_minutes BETWEEN 0 AND 1000000000),
    active boolean NOT NULL,
    revision bigint NOT NULL CHECK (revision > 0),
    CHECK (to_at > from_at AND to_at - from_at <= interval '62 days')
);
-- No call foreign key: telemetry retention must not erase billed usage.
CREATE TABLE profiled_call_usage (
    call_id uuid PRIMARY KEY,
    organisation_id text NOT NULL REFERENCES organisations(id),
    effective_end timestamptz,
    seconds numeric NOT NULL CHECK (seconds >= 0)
);
CREATE INDEX profiled_call_usage_period ON profiled_call_usage(organisation_id, effective_end);
-- The ledger changes in the worker's call transaction, not on an HTTP poll.
-- +goose StatementBegin
CREATE FUNCTION record_profiled_call_usage() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE finish timestamptz;
BEGIN
    IF NEW.end_state = 'open' THEN RETURN NEW; END IF;
    IF EXISTS (SELECT 1 FROM organisation_entitlements WHERE organisation_id = NEW.organisation_id) THEN
        finish := CASE NEW.end_state WHEN 'observed' THEN NEW.ended_at WHEN 'inferred' THEN NEW.last_activity_at END;
        INSERT INTO profiled_call_usage(call_id, organisation_id, effective_end, seconds)
        VALUES (NEW.id, NEW.organisation_id, finish,
            CASE WHEN NEW.started_at IS NOT NULL AND finish >= NEW.started_at
                THEN extract(epoch FROM finish - NEW.started_at) ELSE 0 END)
        ON CONFLICT(call_id) DO UPDATE SET effective_end = excluded.effective_end, seconds = excluded.seconds;
    END IF;
    RETURN NEW;
END;
$$;
-- +goose StatementEnd
CREATE TRIGGER record_profiled_call_usage AFTER INSERT OR UPDATE OF started_at, ended_at, end_state, last_activity_at
ON calls FOR EACH ROW EXECUTE FUNCTION record_profiled_call_usage();

-- Admission survives queue lag and process restarts; it is not a duration reservation.
CREATE TABLE telemetry_admissions (
 organisation_id text NOT NULL REFERENCES organisations(id),
 project_id text NOT NULL,
 identity_kind text NOT NULL CHECK (identity_kind IN ('room', 'job')),
 identity_id text NOT NULL,
 from_at timestamptz NOT NULL,
 to_at timestamptz NOT NULL,
 PRIMARY KEY (organisation_id, project_id, identity_kind, identity_id, from_at)
);
