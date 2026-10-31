-- +goose Up
-- Alert channels beyond email and webhook (docs/specs/alert-channels.md): Slack, Microsoft Teams and
-- Discord incoming webhooks, PagerDuty Events API v2 and Opsgenie. A delivery still queued when its
-- channel is removed is cancelled, not failed: nothing is wrong with a channel an Admin took away.
ALTER TABLE alert_rules DROP CONSTRAINT alert_rules_channels_check,
    ADD CONSTRAINT alert_rules_channels_check CHECK (channels <@ ARRAY['email', 'webhook', 'slack', 'teams', 'discord', 'pagerduty', 'opsgenie']::text[]);
ALTER TABLE alert_channels DROP CONSTRAINT alert_channels_kind_check,
    ADD CONSTRAINT alert_channels_kind_check CHECK (kind IN ('email', 'webhook', 'slack', 'teams', 'discord', 'pagerduty', 'opsgenie'));
ALTER TABLE alert_deliveries DROP CONSTRAINT alert_deliveries_channel_check,
    ADD CONSTRAINT alert_deliveries_channel_check CHECK (channel IN ('email', 'webhook', 'slack', 'teams', 'discord', 'pagerduty', 'opsgenie'));
ALTER TABLE alert_deliveries DROP CONSTRAINT alert_deliveries_state_check,
    ADD CONSTRAINT alert_deliveries_state_check CHECK (state IN ('pending', 'retrying', 'delivered', 'failed', 'cancelled'));

-- +goose Down
DELETE FROM alert_deliveries WHERE channel NOT IN ('email', 'webhook');
UPDATE alert_deliveries SET state = 'failed' WHERE state = 'cancelled';
DELETE FROM alert_channels WHERE kind NOT IN ('email', 'webhook');
UPDATE alert_rules SET channels = ARRAY(SELECT unnest(channels) INTERSECT SELECT unnest(ARRAY['email', 'webhook']) ORDER BY 1);
ALTER TABLE alert_deliveries DROP CONSTRAINT alert_deliveries_state_check,
    ADD CONSTRAINT alert_deliveries_state_check CHECK (state IN ('pending', 'retrying', 'delivered', 'failed'));
ALTER TABLE alert_deliveries DROP CONSTRAINT alert_deliveries_channel_check,
    ADD CONSTRAINT alert_deliveries_channel_check CHECK (channel IN ('email', 'webhook'));
ALTER TABLE alert_channels DROP CONSTRAINT alert_channels_kind_check,
    ADD CONSTRAINT alert_channels_kind_check CHECK (kind IN ('email', 'webhook'));
ALTER TABLE alert_rules DROP CONSTRAINT alert_rules_channels_check,
    ADD CONSTRAINT alert_rules_channels_check CHECK (channels <@ ARRAY['email', 'webhook']::text[]);
