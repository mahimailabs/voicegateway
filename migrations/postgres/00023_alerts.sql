-- +goose Up
-- Alert delivery (docs/specs/alerts-delivery.md, behaviour in alerts-behaviour.md). The worker turns
-- the Issues page's occurrences into incidents per rule and subject, and sends each incident change
-- to the project's channels through durable deliveries. web stores rule and channel setup only.

-- Which rules send, and to which channel kinds. No row: on, sending nowhere.
CREATE TABLE alert_rules (
    organisation_id text NOT NULL,
    project_id text NOT NULL,
    rule_id text NOT NULL CHECK (rule_id IN ('slow-answer', 'cut-off', 'job-failed', 'slow-join', 'plain-phone', 'recording-lost', 'ingress-error')),
    enabled boolean NOT NULL DEFAULT true,
    channels text[] NOT NULL DEFAULT '{}' CHECK (channels <@ ARRAY['email', 'webhook']::text[]),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (organisation_id, project_id, rule_id),
    FOREIGN KEY (organisation_id, project_id) REFERENCES projects (organisation_id, id) ON DELETE CASCADE
);

-- One channel per kind per project. config is sealed JSON (webhook URL and signing secret, email
-- recipients) and never leaves the server; target is what the console may show: the webhook's host,
-- or the recipient count.
CREATE TABLE alert_channels (
    organisation_id text NOT NULL,
    project_id text NOT NULL,
    kind text NOT NULL CHECK (kind IN ('email', 'webhook')),
    config_sealed bytea NOT NULL,
    target text NOT NULL,
    -- A delivery refused with a non-retryable answer marks the channel broken until one succeeds
    -- or an Admin saves the channel again.
    broken boolean NOT NULL DEFAULT false,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (organisation_id, project_id, kind),
    FOREIGN KEY (organisation_id, project_id) REFERENCES projects (organisation_id, id) ON DELETE CASCADE
);

-- An incident groups occurrences of one rule on one subject. At most one is firing per subject.
CREATE TABLE alert_incidents (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organisation_id text NOT NULL,
    project_id text NOT NULL,
    rule_id text NOT NULL,
    subject text NOT NULL,
    subject_label text NOT NULL,
    state text NOT NULL DEFAULT 'firing' CHECK (state IN ('firing', 'resolved')),
    opened_at timestamptz NOT NULL DEFAULT now(),
    resolved_at timestamptz,
    -- Null while firing; 'condition' when the resolve rule held, else the user id that resolved it.
    resolved_by text,
    occurrences integer NOT NULL DEFAULT 0,
    -- The occurrence count the last firing or reminder message carried, and when it went.
    notified_occurrences integer NOT NULL DEFAULT 0,
    notified_at timestamptz,
    -- Each change (firing, reminder, resolved) takes the next number, part of its delivery key.
    changes integer NOT NULL DEFAULT 0,
    last_occurrence_at timestamptz,
    -- The latest occurrence as the Issues page shows it: where, detail, callId.
    last_detail jsonb NOT NULL DEFAULT '{}',
    -- Why a firing incident has not resolved, when the data cannot decide: too few calls, or the
    -- source stopped reporting (silent_since). Never a reason to resolve.
    hold_reason text,
    silent_since timestamptz,
    FOREIGN KEY (organisation_id, project_id) REFERENCES projects (organisation_id, id) ON DELETE CASCADE
);
CREATE UNIQUE INDEX alert_incidents_firing ON alert_incidents (organisation_id, project_id, rule_id, subject) WHERE state = 'firing';
CREATE INDEX alert_incidents_recent ON alert_incidents (organisation_id, project_id, opened_at DESC);
CREATE INDEX alert_incidents_resolved ON alert_incidents (resolved_at) WHERE state = 'resolved';

-- Every occurrence an incident counted, once per rule (alerts-behaviour.md, Grouping).
CREATE TABLE alert_occurrences (
    organisation_id text NOT NULL,
    project_id text NOT NULL,
    rule_id text NOT NULL,
    occurrence_id text NOT NULL,
    incident_id uuid REFERENCES alert_incidents (id) ON DELETE SET NULL,
    seen_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (organisation_id, project_id, rule_id, occurrence_id),
    FOREIGN KEY (organisation_id, project_id) REFERENCES projects (organisation_id, id) ON DELETE CASCADE
);
CREATE INDEX alert_occurrences_seen ON alert_occurrences (seen_at);

-- One delivery per incident change per channel, kept across worker restarts. A test delivery has no
-- incident. idempotency_key is incident/change-number/channel, sent as the webhook id.
CREATE TABLE alert_deliveries (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organisation_id text NOT NULL,
    project_id text NOT NULL,
    incident_id uuid REFERENCES alert_incidents (id) ON DELETE CASCADE,
    channel text NOT NULL CHECK (channel IN ('email', 'webhook')),
    change text NOT NULL CHECK (change IN ('firing', 'reminder', 'resolved', 'test')),
    idempotency_key text NOT NULL UNIQUE,
    -- The message as it was when the change happened, so a retry sends the same thing.
    message jsonb NOT NULL,
    state text NOT NULL DEFAULT 'pending' CHECK (state IN ('pending', 'retrying', 'delivered', 'failed')),
    attempts integer NOT NULL DEFAULT 0,
    next_attempt_at timestamptz NOT NULL DEFAULT now(),
    lease_until timestamptz,
    -- The last answer: an HTTP status or SMTP class, and a provider message with no secret in it.
    last_status text,
    last_error text,
    delivered_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (organisation_id, project_id) REFERENCES projects (organisation_id, id) ON DELETE CASCADE
);
CREATE INDEX alert_deliveries_due ON alert_deliveries (next_attempt_at) WHERE state IN ('pending', 'retrying');
CREATE INDEX alert_deliveries_incident ON alert_deliveries (incident_id);
CREATE INDEX alert_deliveries_channel ON alert_deliveries (organisation_id, project_id, channel, created_at DESC);

CREATE TABLE alert_delivery_attempts (
    delivery_id uuid NOT NULL REFERENCES alert_deliveries (id) ON DELETE CASCADE,
    attempt integer NOT NULL,
    at timestamptz NOT NULL,
    status text NOT NULL,
    error text,
    PRIMARY KEY (delivery_id, attempt)
);

-- The evaluator reads occurrences by when they were stored.
CREATE INDEX issue_occurrences_observed ON issue_occurrences (observed_at) WHERE active;

-- A standing condition's observed_at moves on every source read that still sees it; changed_at
-- moves only when it turns on again or its detail changes, which is what makes a new plain-phone
-- occurrence.
ALTER TABLE issue_occurrences ADD COLUMN changed_at timestamptz NOT NULL DEFAULT now();
UPDATE issue_occurrences SET changed_at = observed_at;

-- Occurrences stored before alerts existed are history, not news: count them as seen so the first
-- run does not page anyone for last week. Standing conditions (plain-phone) are evaluated as they
-- stand, so they are not seeded.
INSERT INTO alert_occurrences (organisation_id, project_id, rule_id, occurrence_id)
SELECT organisation_id, project_id,
       CASE issue_id WHEN 'slow' THEN 'slow-answer' WHEN 'cut' THEN 'cut-off' ELSE issue_id END,
       source_id
FROM issue_occurrences
WHERE issue_id IN ('slow', 'cut', 'job-failed', 'slow-join', 'recording-lost', 'ingress-error')
ON CONFLICT DO NOTHING;

-- +goose Down
ALTER TABLE issue_occurrences DROP COLUMN changed_at;
DROP INDEX issue_occurrences_observed;
DROP TABLE alert_delivery_attempts;
DROP TABLE alert_deliveries;
DROP TABLE alert_occurrences;
DROP TABLE alert_incidents;
DROP TABLE alert_channels;
DROP TABLE alert_rules;
