-- Migration 0004: add billing columns to telemetry.requests
-- VoiceGateway rates each request at write time (the collector re-rates fleet
-- rows on ingest). rated_price_usd is the billable price. rate_rule is the
-- audit token for the applied rule (cost_plus:1.3, fixed:0.006/minute,
-- default:1). ADD COLUMN IF NOT EXISTS keeps this idempotent, matching the
-- CREATE ... IF NOT EXISTS style of 0001.
-- The migration runner splits on the bare statement terminator, so no comment
-- here may contain that character.

ALTER TABLE telemetry.requests
  ADD COLUMN IF NOT EXISTS rated_price_usd Float64 DEFAULT 0

;

ALTER TABLE telemetry.requests
  ADD COLUMN IF NOT EXISTS rate_rule LowCardinality(String) DEFAULT ''

;
