package join_test

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"os"
	"regexp"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/ClickHouse/clickhouse-go/v2"
	"github.com/ClickHouse/clickhouse-go/v2/lib/driver"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/livekit/protocol/livekit"
	"github.com/redis/go-redis/v9"
	"github.com/testcontainers/testcontainers-go"
	tcclickhouse "github.com/testcontainers/testcontainers-go/modules/clickhouse"
	tcpostgres "github.com/testcontainers/testcontainers-go/modules/postgres"
	tcredis "github.com/testcontainers/testcontainers-go/modules/redis"
	"go.opentelemetry.io/collector/pdata/pcommon"
	"go.opentelemetry.io/collector/pdata/ptrace"
	"go.opentelemetry.io/collector/pdata/ptrace/ptraceotlp"
	"google.golang.org/protobuf/encoding/protojson"
	"google.golang.org/protobuf/types/known/timestamppb"

	"github.com/mahimailabs/voicegateway/internal/event"
	"github.com/mahimailabs/voicegateway/internal/join"
	"github.com/mahimailabs/voicegateway/internal/seal"
	"github.com/mahimailabs/voicegateway/internal/sink"
	"github.com/mahimailabs/voicegateway/internal/store/chstore"
	"github.com/mahimailabs/voicegateway/internal/store/pgstore"
	"github.com/mahimailabs/voicegateway/internal/stream"
)

// The same images, tags and digests as deploy/compose.yaml.
const (
	postgresImage   = "postgres:16.15-alpine@sha256:721873c34ceb9f8d8fc265984940dc982404c105f19ad51be9fdc5970a6080ea"
	clickhouseImage = "clickhouse/clickhouse-server:26.1.12.23-alpine@sha256:369bd68942569e0effdf0aa68b4f844a74d97b73e281cf8012ce90e9865cc411"
	redisImage      = "redis:8.10.2-alpine@sha256:3811787313eba226a2ef38658c6ccb91cd5e110edc89c37767de373120a0e5a0"
)

// stack is one Postgres, ClickHouse and Redis shared by every test in the package; each test works
// in a project of its own.
type stack struct {
	pool   *pgxpool.Pool
	calls  *pgstore.Store
	events *chstore.Store
	ch     driver.Conn
	rdb    *redis.Client
}

var (
	shared     *stack
	sharedErr  error
	sharedOnce sync.Once
	containers []testcontainers.Container
)

func TestMain(m *testing.M) {
	code := m.Run()
	for _, c := range containers {
		_ = c.Terminate(context.Background())
	}
	os.Exit(code)
}

// fixture is one test's project in the shared stack.
type fixture struct {
	*stack
	t       *testing.T
	project string
	joiner  *join.Joiner
}

func newFixture(t *testing.T) *fixture {
	t.Helper()
	if os.Getenv("CI") == "" {
		if testing.Short() {
			t.Skip("needs Docker; runs in CI")
		}
		testcontainers.SkipIfProviderIsNotHealthy(t)
	}
	sharedOnce.Do(func() { shared, sharedErr = start() })
	if sharedErr != nil {
		t.Fatal(sharedErr)
	}
	project := strings.ToLower(regexp.MustCompile(`[^A-Za-z0-9]+`).ReplaceAllString(t.Name(), "-"))
	ctx := context.Background()
	if _, err := shared.calls.CreateProject(ctx, pgstore.NewProject{
		OrganisationID: "default", ID: project, Name: project, LiveKitURL: "wss://example.livekit.cloud",
		LiveKitAPIKey: "API" + project, LiveKitAPISecret: "secret",
	}); err != nil {
		t.Fatal(err)
	}
	log := slog.New(slog.NewTextHandler(io.Discard, nil))
	return &fixture{stack: shared, t: t, project: project, joiner: &join.Joiner{Calls: shared.calls, Traces: shared.events, Log: log, Timeout: 10 * time.Second}}
}

func start() (*stack, error) {
	ctx := context.Background()
	pg, err := tcpostgres.Run(ctx, postgresImage,
		tcpostgres.WithDatabase("voicegateway"), tcpostgres.WithUsername("voicegateway"), tcpostgres.WithPassword("pg-test"),
		tcpostgres.BasicWaitStrategies())
	if pg != nil {
		containers = append(containers, pg)
	}
	if err != nil {
		return nil, fmt.Errorf("start postgres: %w", err)
	}
	pgURL, err := pg.ConnectionString(ctx, "sslmode=disable")
	if err != nil {
		return nil, err
	}
	pool, err := pgxpool.New(ctx, pgURL)
	if err != nil {
		return nil, err
	}
	if _, err := pgstore.Migrate(ctx, pool); err != nil {
		return nil, err
	}
	sealer, err := seal.New(bytes.Repeat([]byte{7}, seal.KeySize))
	if err != nil {
		return nil, err
	}

	ch, err := tcclickhouse.Run(ctx, clickhouseImage,
		tcclickhouse.WithDatabase("voicegateway"), tcclickhouse.WithUsername("voicegateway"), tcclickhouse.WithPassword("ch-test"))
	if ch != nil {
		containers = append(containers, ch)
	}
	if err != nil {
		return nil, fmt.Errorf("start clickhouse: %w", err)
	}
	chURL, err := ch.ConnectionString(ctx)
	if err != nil {
		return nil, err
	}
	if _, err := chstore.Migrate(ctx, chURL); err != nil {
		return nil, err
	}
	events, err := chstore.Open(chURL)
	if err != nil {
		return nil, err
	}
	opts, err := clickhouse.ParseDSN(chURL)
	if err != nil {
		return nil, err
	}
	conn, err := clickhouse.Open(opts)
	if err != nil {
		return nil, err
	}

	rd, err := tcredis.Run(ctx, redisImage)
	if rd != nil {
		containers = append(containers, rd)
	}
	if err != nil {
		return nil, fmt.Errorf("start redis: %w", err)
	}
	rdURL, err := rd.ConnectionString(ctx)
	if err != nil {
		return nil, err
	}
	ropts, err := redis.ParseURL(rdURL)
	if err != nil {
		return nil, err
	}
	return &stack{pool: pool, calls: pgstore.NewStore(pool, sealer), events: events, ch: conn, rdb: redis.NewClient(ropts)}, nil
}

// append adds an envelope for this test's project to the ingest stream.
func (f *fixture) append(kind event.Kind, id string, payload []byte) {
	f.t.Helper()
	_, err := stream.NewProducer(f.rdb, stream.Ingest).Append(context.Background(), event.Envelope{
		ID: id, Kind: kind, OrganisationID: "default", ProjectID: f.project, ReceivedAt: time.Now(), Payload: payload,
	})
	if err != nil {
		f.t.Fatal(err)
	}
}

func (f *fixture) sink(consumer string, j sink.Joiner) *sink.Sink {
	return &sink.Sink{
		Stream:    stream.NewConsumer(f.rdb, stream.Ingest, "worker", consumer),
		Events:    f.events,
		Join:      j,
		Log:       slog.New(slog.NewTextHandler(io.Discard, nil)),
		Batch:     100,
		Block:     100 * time.Millisecond,
		ClaimIdle: 200 * time.Millisecond,
		Backoff:   20 * time.Millisecond, MaxBackoff: 100 * time.Millisecond,
		Timeout: 10 * time.Second,
	}
}

// store runs a worker until the stream is empty and nothing is pending.
func (f *fixture) store(consumer string) {
	f.t.Helper()
	f.runUntil(f.sink(consumer, f.joiner), f.drained)
}

func (f *fixture) drained() bool {
	ctx := context.Background()
	return f.rdb.XLen(ctx, stream.Ingest).Val() == 0 && f.rdb.XPending(ctx, stream.Ingest, "worker").Val().Count == 0
}

func (f *fixture) runUntil(s *sink.Sink, cond func() bool) {
	f.t.Helper()
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() { done <- s.Run(ctx) }()
	deadline := time.Now().Add(30 * time.Second)
	for !cond() && time.Now().Before(deadline) {
		time.Sleep(20 * time.Millisecond)
	}
	cancel()
	if err := <-done; err != nil {
		f.t.Errorf("Run = %v", err)
	}
	if !cond() {
		f.t.Fatal("condition not met before the deadline")
	}
}

// call is a calls row as the tests read it.
type call struct {
	ID, RoomSID, JobID, EndState string
	StartedAt, EndedAt           *time.Time
}

// calls returns this project's calls by room SID; a call with no SID is keyed by its job id.
func (f *fixture) calls() map[string]call {
	f.t.Helper()
	rows, err := f.pool.Query(context.Background(), `
		SELECT id::text, coalesce(room_sid, ''), coalesce(job_id, ''), end_state, started_at, ended_at
		FROM calls WHERE organisation_id = 'default' AND project_id = $1`, f.project)
	if err != nil {
		f.t.Fatal(err)
	}
	defer rows.Close()
	out := map[string]call{}
	for rows.Next() {
		var c call
		if err := rows.Scan(&c.ID, &c.RoomSID, &c.JobID, &c.EndState, &c.StartedAt, &c.EndedAt); err != nil {
			f.t.Fatal(err)
		}
		key := c.RoomSID
		if key == "" {
			key = "job:" + c.JobID
		}
		out[key] = c
	}
	if err := rows.Err(); err != nil {
		f.t.Fatal(err)
	}
	return out
}

// participant is a call_participants row.
type participant struct {
	Kind             string
	JoinedAt, LeftAt *time.Time
}

func (f *fixture) participants(callID string) map[string]participant {
	f.t.Helper()
	rows, err := f.pool.Query(context.Background(),
		`SELECT participant_sid, kind, joined_at, left_at FROM call_participants WHERE call_id = $1::uuid`, callID)
	if err != nil {
		f.t.Fatal(err)
	}
	defer rows.Close()
	out := map[string]participant{}
	for rows.Next() {
		var sid string
		var p participant
		if err := rows.Scan(&sid, &p.Kind, &p.JoinedAt, &p.LeftAt); err != nil {
			f.t.Fatal(err)
		}
		out[sid] = p
	}
	return out
}

// eventCalls returns each stored event's call id by dedupe key, counting rows as stored.
func (f *fixture) eventCalls() (map[string]string, int) {
	f.t.Helper()
	rows, err := f.ch.Query(context.Background(),
		`SELECT dedupe_key, call_id FROM events FINAL WHERE project_id = ?`, f.project)
	if err != nil {
		f.t.Fatal(err)
	}
	defer func() { _ = rows.Close() }()
	out, n := map[string]string{}, 0
	for rows.Next() {
		var key, id string
		if err := rows.Scan(&key, &id); err != nil {
			f.t.Fatal(err)
		}
		out[key] = id
		n++
	}
	return out, n
}

// spanCalls returns each stored span's own call id by span id, and the row count.
func (f *fixture) spanCalls() (map[string]string, int) {
	f.t.Helper()
	rows, err := f.ch.Query(context.Background(), `SELECT span_id, call_id FROM spans FINAL WHERE project_id = ?`, f.project)
	if err != nil {
		f.t.Fatal(err)
	}
	defer func() { _ = rows.Close() }()
	out, n := map[string]string{}, 0
	for rows.Next() {
		var span, id string
		if err := rows.Scan(&span, &id); err != nil {
			f.t.Fatal(err)
		}
		out[span] = id
		n++
	}
	return out, n
}

// unmatched counts this project's unmatched rows of a kind, as stored, not folded.
func (f *fixture) unmatched(kind string) int {
	f.t.Helper()
	var n uint64
	if err := f.ch.QueryRow(context.Background(),
		`SELECT count() FROM unmatched WHERE project_id = ? AND kind = ?`, f.project, kind).Scan(&n); err != nil {
		f.t.Fatal(err)
	}
	return int(n)
}

// webhook encodes a WebhookEvent as LiveKit posts it.
func (f *fixture) webhook(ev *livekit.WebhookEvent) []byte {
	f.t.Helper()
	b, err := protojson.Marshal(ev)
	if err != nil {
		f.t.Fatal(err)
	}
	return b
}

// appendWebhook appends a webhook under its own id.
func (f *fixture) appendWebhook(ev *livekit.WebhookEvent) {
	f.t.Helper()
	f.append(event.KindLiveKitWebhook, ev.GetId(), f.webhook(ev))
}

// sessionReport encodes a session report as web appends it.
func (f *fixture) sessionReport(h *livekit.MetricsRecordingHeader) []byte {
	f.t.Helper()
	header, err := protojson.Marshal(h)
	if err != nil {
		f.t.Fatal(err)
	}
	b, err := json.Marshal(event.SessionReport{Header: header})
	if err != nil {
		f.t.Fatal(err)
	}
	return b
}

// span is one span of a test export.
type span struct {
	trace, id, parent string
	start, end        time.Time
	attrs             map[string]string
	resource          map[string]string
}

// export encodes spans as one OTLP trace export, one resource per span.
func (f *fixture) export(spans ...span) []byte {
	f.t.Helper()
	td := ptrace.NewTraces()
	for _, s := range spans {
		rs := td.ResourceSpans().AppendEmpty()
		for k, v := range s.resource {
			rs.Resource().Attributes().PutStr(k, v)
		}
		ss := rs.ScopeSpans().AppendEmpty()
		ss.Scope().SetName("livekit-agents")
		sp := ss.Spans().AppendEmpty()
		sp.SetTraceID(traceID(s.trace))
		sp.SetSpanID(spanID(s.id))
		if s.parent != "" {
			sp.SetParentSpanID(spanID(s.parent))
		}
		sp.SetName("span-" + s.id)
		if !s.start.IsZero() {
			sp.SetStartTimestamp(pcommon.NewTimestampFromTime(s.start))
		}
		if !s.end.IsZero() {
			sp.SetEndTimestamp(pcommon.NewTimestampFromTime(s.end))
		}
		for k, v := range s.attrs {
			sp.Attributes().PutStr(k, v)
		}
	}
	b, err := ptraceotlp.NewExportRequestFromTraces(td).MarshalProto()
	if err != nil {
		f.t.Fatal(err)
	}
	return b
}

func traceID(s string) pcommon.TraceID {
	var id pcommon.TraceID
	copy(id[:], s)
	return id
}

func spanID(s string) pcommon.SpanID {
	var id pcommon.SpanID
	copy(id[:], s)
	return id
}

// hexSpan is a span id as stored: the hex of its bytes.
func hexSpan(s string) string {
	id := spanID(s)
	return id.String()
}

func hexTrace(s string) string {
	id := traceID(s)
	return id.String()
}

func ts(t time.Time) *timestamppb.Timestamp { return timestamppb.New(t) }
