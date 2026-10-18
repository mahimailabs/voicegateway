-- Raw telemetry expires after 90 days: every stored envelope, span, trace link, usage point and
-- unmatched record. Layer samples (00004 to 00007) keep their own 30 days. Postgres keeps the call
-- list and call summaries, so an expired call still lists with its summary, and its detail shows
-- turns and spans as null, never as zero. Each TTL follows the time the table is partitioned by,
-- and the three month-partitioned tables drop a part only once every row in it has expired, so a
-- row may live up to a month past 90 days. trace_calls and unmatched have no partitions and delete
-- expired rows in merges. MODIFY TTL also removes rows that are already older than 90 days, in a
-- background mutation that rewrites the existing parts once.

-- +goose Up
ALTER TABLE events MODIFY TTL toDateTime(received_at) + INTERVAL 90 DAY;
ALTER TABLE events MODIFY SETTING ttl_only_drop_parts = 1;
ALTER TABLE spans MODIFY TTL toDateTime(ifNull(start_time, inserted_at)) + INTERVAL 90 DAY;
ALTER TABLE spans MODIFY SETTING ttl_only_drop_parts = 1;
ALTER TABLE metric_points MODIFY TTL toDateTime(end_time) + INTERVAL 90 DAY;
ALTER TABLE metric_points MODIFY SETTING ttl_only_drop_parts = 1;
ALTER TABLE trace_calls MODIFY TTL toDateTime(inserted_at) + INTERVAL 90 DAY;
ALTER TABLE unmatched MODIFY TTL toDateTime(inserted_at) + INTERVAL 90 DAY;

-- +goose Down
ALTER TABLE unmatched REMOVE TTL;
ALTER TABLE trace_calls REMOVE TTL;
ALTER TABLE metric_points RESET SETTING ttl_only_drop_parts;
ALTER TABLE metric_points REMOVE TTL;
ALTER TABLE spans RESET SETTING ttl_only_drop_parts;
ALTER TABLE spans REMOVE TTL;
ALTER TABLE events RESET SETTING ttl_only_drop_parts;
ALTER TABLE events REMOVE TTL;
