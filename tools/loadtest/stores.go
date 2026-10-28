package main

import (
	"context"
	"errors"
	"fmt"
	"os"
	"time"

	"github.com/ClickHouse/clickhouse-go/v2"
	"github.com/jackc/pgx/v5"
	"github.com/redis/go-redis/v9"
)

// Names the worker reads and writes: internal/stream.Ingest, its dead-letter stream and
// internal/app's consumer group. Copied, not imported, so the check reads what is really there.
const (
	ingestStream = "voicegateway:ingest"
	deadStream   = ingestStream + ":dead"
	workerGroup  = "worker"
)

// stores reads the stack's own databases, from the same VOICEGATEWAY_* URLs the binary uses.
type stores struct {
	ch  clickhouse.Conn
	pg  *pgx.Conn
	rdb *redis.Client
}

func openStores(ctx context.Context) (*stores, error) {
	get := func(name string) (string, error) {
		v := os.Getenv(name)
		if v == "" {
			return "", fmt.Errorf("set %s to the stack's URL, so the stored events can be counted", name)
		}
		return v, nil
	}
	chURL, err := get("VOICEGATEWAY_CLICKHOUSE_URL")
	if err != nil {
		return nil, err
	}
	pgURL, err := get("VOICEGATEWAY_POSTGRES_URL")
	if err != nil {
		return nil, err
	}
	redisURL, err := get("VOICEGATEWAY_REDIS_URL")
	if err != nil {
		return nil, err
	}
	opts, err := clickhouse.ParseDSN(chURL)
	if err != nil {
		return nil, fmt.Errorf("clickhouse url: %w", err)
	}
	ch, err := clickhouse.Open(opts)
	if err != nil {
		return nil, err
	}
	pg, err := pgx.Connect(ctx, pgURL)
	if err != nil {
		return nil, fmt.Errorf("postgres: %w", err)
	}
	ro, err := redis.ParseURL(redisURL)
	if err != nil {
		return nil, fmt.Errorf("redis url: %w", err)
	}
	return &stores{ch: ch, pg: pg, rdb: redis.NewClient(ro)}, nil
}

func (s *stores) close() {
	_ = s.ch.Close()
	_ = s.pg.Close(context.Background())
	_ = s.rdb.Close()
}

func (s *stores) deadLetters(ctx context.Context) (int64, error) {
	return s.rdb.XLen(ctx, deadStream).Result()
}

// backlog is what the worker has yet to store: entries not yet read by its group, plus entries read
// and not yet acknowledged.
func (s *stores) backlog(ctx context.Context) (lag, pending int64, err error) {
	groups, err := s.rdb.XInfoGroups(ctx, ingestStream).Result()
	if err != nil {
		return 0, 0, err
	}
	for _, g := range groups {
		if g.Name == workerGroup {
			return g.Lag, g.Pending, nil
		}
	}
	return 0, 0, fmt.Errorf("consumer group %q not found on %s; is the worker running?", workerGroup, ingestStream)
}

type sample struct {
	At      time.Time `json:"at"`
	Live    int64     `json:"live_calls"`
	Lag     int64     `json:"stream_lag"`
	Pending int64     `json:"stream_pending"`
}

// sample records live calls and the worker's backlog every second until ctx ends.
func (s *stores) sample(ctx context.Context, rec *recorder) []sample {
	var out []sample
	t := time.NewTicker(time.Second)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return out
		case now := <-t.C:
			lag, pending, err := s.backlog(ctx)
			if err != nil {
				lag, pending = -1, -1
			}
			out = append(out, sample{At: now.UTC(), Live: rec.live.Load(), Lag: lag, Pending: pending})
		}
	}
}

// waitDrained waits until the worker has stored and acknowledged everything, and returns when.
func (s *stores) waitDrained(ctx context.Context, limit time.Duration) (time.Time, error) {
	deadline := time.Now().Add(limit)
	for {
		lag, pending, err := s.backlog(ctx)
		if err != nil {
			return time.Time{}, err
		}
		if lag == 0 && pending == 0 {
			return time.Now(), nil
		}
		if time.Now().After(deadline) {
			return time.Time{}, fmt.Errorf("worker still has %d unread and %d unacknowledged entries after %s", lag, pending, limit)
		}
		select {
		case <-ctx.Done():
			return time.Time{}, ctx.Err()
		case <-time.After(500 * time.Millisecond):
		}
	}
}

// waitSummaries waits until the worker has built the summary of every call of the run, which it
// does after the events are stored, and returns when.
func (s *stores) waitSummaries(ctx context.Context, c config, run string, limit time.Duration) (time.Time, error) {
	deadline := time.Now().Add(limit)
	for {
		var built int64
		if err := s.pg.QueryRow(ctx, `
			SELECT count(*) FROM calls c JOIN call_summaries s ON s.call_id = c.id
			WHERE c.organisation_id = $1 AND c.project_id = $2 AND c.room_name LIKE $3 AND c.summary_stale_at IS NULL`,
			c.org, c.project, "vg-load-"+run+"-%").Scan(&built); err != nil {
			return time.Time{}, fmt.Errorf("count summaries: %w", err)
		}
		if built >= int64(c.calls) {
			return time.Now(), nil
		}
		if time.Now().After(deadline) {
			return time.Time{}, fmt.Errorf("%d of %d call summaries built after %s", built, c.calls, limit)
		}
		select {
		case <-ctx.Done():
			return time.Time{}, ctx.Err()
		case <-time.After(time.Second):
		}
	}
}

// stored is what the stack holds for one run, read back from its databases.
type stored struct {
	Webhooks        uint64 `json:"webhook_events"`
	Traces          uint64 `json:"trace_exports"`
	Metrics         uint64 `json:"metric_exports"`
	Reports         uint64 `json:"session_reports"`
	SDK             uint64 `json:"sdk_batches"`
	Spans           uint64 `json:"spans"`
	Unmatched       uint64 `json:"unmatched"`
	DeadLetters     int64  `json:"new_dead_letters"`
	Calls           int64  `json:"calls"`
	CallsObserved   int64  `json:"calls_ended_observed"`
	CallsWithJob    int64  `json:"calls_with_job"`
	Participants    int64  `json:"participants"`
	Summaries       int64  `json:"call_summaries"`
	TurnCounts      int64  `json:"distinct_turn_counts"`
	SpansOnACall    uint64 `json:"spans_joined_to_a_call"`
	ReportsOnACall  uint64 `json:"reports_joined_to_a_call"`
	WebhooksOnACall uint64 `json:"webhooks_joined_to_a_call"`
}

func (s *stores) verify(ctx context.Context, c config, run string, since time.Time, deadBefore int64) (stored, error) {
	var st stored
	room := "vg-load-" + run + "-%"
	// Every row is matched by the ids this run gave it; FINAL folds a redelivered entry into one.
	// Spans are matched by substring, not startsWith or LIKE: on ClickHouse 26.1 a prefix match on
	// the FixedString trace_id key returned 7,632 of 56,000 stored rows.
	err := s.ch.QueryRow(ctx, `
		SELECT
			countIf(kind = 'livekit.webhook' AND startsWith(dedupe_key, {ev:String})),
			countIf(kind = 'livekit.agent.traces' AND received_at >= {since:DateTime64(9)}),
			countIf(kind = 'livekit.agent.metrics' AND received_at >= {since:DateTime64(9)}),
			countIf(kind = 'livekit.agent.session_report' AND startsWith(dedupe_key, {aj:String})),
			countIf(kind = 'voicegateway.sdk.events' AND startsWith(dedupe_key, {sdk:String})),
			countIf(kind = 'livekit.webhook' AND startsWith(dedupe_key, {ev:String}) AND call_id != ''),
			countIf(kind = 'livekit.agent.session_report' AND startsWith(dedupe_key, {aj:String}) AND call_id != '')
		FROM events FINAL
		WHERE organisation_id = {org:String} AND project_id = {project:String}`,
		clickhouse.Named("ev", "EV_"+run), clickhouse.Named("aj", "AJ_"+run), clickhouse.Named("sdk", "sdk:load-"+run+"-"),
		clickhouse.Named("since", since), clickhouse.Named("org", c.org), clickhouse.Named("project", c.project),
	).Scan(&st.Webhooks, &st.Traces, &st.Metrics, &st.Reports, &st.SDK, &st.WebhooksOnACall, &st.ReportsOnACall)
	if err != nil {
		return st, fmt.Errorf("count events: %w", err)
	}
	if err := s.ch.QueryRow(ctx, `
		SELECT count(), countIf(call_id != '') FROM spans FINAL
		WHERE organisation_id = {org:String} AND project_id = {project:String} AND substring(trace_id, 1, 8) = {trace:String}`,
		clickhouse.Named("org", c.org), clickhouse.Named("project", c.project), clickhouse.Named("trace", tracePrefix(run)),
	).Scan(&st.Spans, &st.SpansOnACall); err != nil {
		return st, fmt.Errorf("count spans: %w", err)
	}
	if err := s.ch.QueryRow(ctx, `
		SELECT count() FROM unmatched FINAL
		WHERE organisation_id = {org:String} AND project_id = {project:String} AND inserted_at >= {since:DateTime64(3)}`,
		clickhouse.Named("org", c.org), clickhouse.Named("project", c.project), clickhouse.Named("since", since),
	).Scan(&st.Unmatched); err != nil {
		return st, fmt.Errorf("count unmatched: %w", err)
	}
	if err := s.pg.QueryRow(ctx, `
		SELECT count(*), count(*) FILTER (WHERE end_state = 'observed'), count(job_id),
			(SELECT count(*) FROM call_participants p JOIN calls c2 ON c2.id = p.call_id
			 WHERE c2.organisation_id = $1 AND c2.project_id = $2 AND c2.room_name LIKE $3)
		FROM calls WHERE organisation_id = $1 AND project_id = $2 AND room_name LIKE $3`,
		c.org, c.project, room,
	).Scan(&st.Calls, &st.CallsObserved, &st.CallsWithJob, &st.Participants); err != nil {
		return st, fmt.Errorf("count calls: %w", err)
	}
	// Every replayed call is the same call, so every summary must count the same turns.
	if err := s.pg.QueryRow(ctx, `
		SELECT count(s.call_id), count(DISTINCT s.turn_count) FROM calls c JOIN call_summaries s ON s.call_id = c.id
		WHERE c.organisation_id = $1 AND c.project_id = $2 AND c.room_name LIKE $3`,
		c.org, c.project, room,
	).Scan(&st.Summaries, &st.TurnCounts); err != nil {
		return st, fmt.Errorf("count summaries: %w", err)
	}
	dead, err := s.deadLetters(ctx)
	if err != nil && !errors.Is(err, redis.Nil) {
		return st, err
	}
	st.DeadLetters = dead - deadBefore
	return st, nil
}
