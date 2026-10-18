-- The daily-partitioned read tables of 00004 to 00007 expire rows after 30 days. With
-- ttl_only_drop_parts ClickHouse drops a part once every row in it has expired, instead of
-- rewriting the part to delete the expired rows. A part holds one day, so a row may live up to a
-- day past its 30. spans.name is a few hundred span names repeated millions of times, so
-- LowCardinality stores each once per part and compares dictionary ids; changing the type
-- rewrites the column as a background mutation.

-- +goose Up
ALTER TABLE layer_reads MODIFY SETTING ttl_only_drop_parts = 1;
ALTER TABLE room_samples MODIFY SETTING ttl_only_drop_parts = 1;
ALTER TABLE participant_samples MODIFY SETTING ttl_only_drop_parts = 1;
ALTER TABLE sip_reads MODIFY SETTING ttl_only_drop_parts = 1;
ALTER TABLE egress_samples MODIFY SETTING ttl_only_drop_parts = 1;
ALTER TABLE ingress_samples MODIFY SETTING ttl_only_drop_parts = 1;
ALTER TABLE worker_samples MODIFY SETTING ttl_only_drop_parts = 1;
ALTER TABLE worker_endpoint_reads MODIFY SETTING ttl_only_drop_parts = 1;
ALTER TABLE cloud_agent_reads MODIFY SETTING ttl_only_drop_parts = 1;
ALTER TABLE spans MODIFY COLUMN name LowCardinality(String);
