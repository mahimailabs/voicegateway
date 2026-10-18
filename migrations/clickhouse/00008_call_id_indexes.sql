-- Per-call reads (getCall, the call summary rebuild) find a call's rows by call_id, which is in no
-- sorting key: without these indexes each read scans every span, metric point and event of the
-- project. A bloom filter per granule lets ClickHouse skip the granules that cannot hold the call,
-- also under FINAL (use_skip_indexes_if_final, on by default in ClickHouse 26.1). Its false
-- positive rate is 0.1%, so a read touches about one extra granule in a thousand. MATERIALIZE
-- builds the index for parts written before this migration, as a background mutation.

-- +goose Up
ALTER TABLE spans ADD INDEX IF NOT EXISTS spans_call_id call_id TYPE bloom_filter(0.001) GRANULARITY 1;
ALTER TABLE spans MATERIALIZE INDEX spans_call_id;
ALTER TABLE trace_calls ADD INDEX IF NOT EXISTS trace_calls_call_id call_id TYPE bloom_filter(0.001) GRANULARITY 1;
ALTER TABLE trace_calls MATERIALIZE INDEX trace_calls_call_id;
ALTER TABLE metric_points ADD INDEX IF NOT EXISTS metric_points_call_id call_id TYPE bloom_filter(0.001) GRANULARITY 1;
ALTER TABLE metric_points MATERIALIZE INDEX metric_points_call_id;
ALTER TABLE events ADD INDEX IF NOT EXISTS events_call_id call_id TYPE bloom_filter(0.001) GRANULARITY 1;
ALTER TABLE events MATERIALIZE INDEX events_call_id;
