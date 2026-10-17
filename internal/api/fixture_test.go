package api

import (
	"bufio"
	"bytes"
	"context"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"io/fs"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"os"
	"regexp"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/pb33f/libopenapi"
	validator "github.com/pb33f/libopenapi-validator"
	"github.com/redis/go-redis/v9"
	"github.com/testcontainers/testcontainers-go"
	tcclickhouse "github.com/testcontainers/testcontainers-go/modules/clickhouse"
	tcpostgres "github.com/testcontainers/testcontainers-go/modules/postgres"
	tcredis "github.com/testcontainers/testcontainers-go/modules/redis"
	"go.opentelemetry.io/collector/pdata/pcommon"
	"go.opentelemetry.io/collector/pdata/ptrace"
	"go.opentelemetry.io/collector/pdata/ptrace/ptraceotlp"

	"github.com/mahimailabs/voicegateway/internal/event"
	"github.com/mahimailabs/voicegateway/internal/join"
	"github.com/mahimailabs/voicegateway/internal/seal"
	"github.com/mahimailabs/voicegateway/internal/sink"
	"github.com/mahimailabs/voicegateway/internal/store"
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

// stack is one Postgres, ClickHouse and Redis shared by the package's tests; each test stores into
// a project of its own.
type stack struct {
	pool   *pgxpool.Pool
	pg     *pgstore.Store
	events *chstore.Store
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

type fixture struct {
	*stack
	t       *testing.T
	project string
	log     *slog.Logger
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
	f := &fixture{stack: shared, t: t, log: slog.New(slog.NewTextHandler(io.Discard, nil))}
	f.project = f.addProject(t.Name())
	return f
}

// addProject creates a project named after s and returns its id.
func (f *fixture) addProject(s string) string {
	f.t.Helper()
	id := strings.ToLower(regexp.MustCompile(`[^A-Za-z0-9]+`).ReplaceAllString(s, "-"))
	if _, err := f.pg.CreateProject(context.Background(), pgstore.NewProject{
		OrganisationID: "default", ID: id, Name: id, LiveKitURL: "wss://example.livekit.cloud",
		LiveKitAPIKey: "API" + id, LiveKitAPISecret: "secret",
	}); err != nil {
		f.t.Fatal(err)
	}
	return id
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
	return &stack{pool: pool, pg: pgstore.NewStore(pool, sealer), events: events, rdb: redis.NewClient(ropts)}, nil
}

// append adds an envelope for the test's project to the ingest stream.
func (f *fixture) append(kind event.Kind, id string, payload []byte) {
	f.t.Helper()
	if _, err := stream.NewProducer(f.rdb, stream.Ingest).Append(context.Background(), event.Envelope{
		ID: id, Kind: kind, OrganisationID: "default", ProjectID: f.project, ReceivedAt: time.Now(), Payload: payload,
	}); err != nil {
		f.t.Fatal(err)
	}
}

// store runs the worker's sink and joiner until the stream is drained.
func (f *fixture) store() {
	f.t.Helper()
	s := &sink.Sink{
		Stream:    stream.NewConsumer(f.rdb, stream.Ingest, "worker", "w1"),
		Events:    f.events,
		Join:      &join.Joiner{Calls: f.pg, Traces: f.events, Log: f.log, Timeout: 10 * time.Second},
		Log:       f.log,
		Batch:     100,
		Block:     100 * time.Millisecond,
		ClaimIdle: 200 * time.Millisecond,
		Backoff:   20 * time.Millisecond, MaxBackoff: 100 * time.Millisecond,
		Timeout: 10 * time.Second,
	}
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() { done <- s.Run(ctx) }()
	drained := func() bool {
		// Until the sink creates the consumer group, there is no pending count to read.
		bg := context.Background() // read after the sink's context is cancelled, too
		pending, err := f.rdb.XPending(bg, stream.Ingest, "worker").Result()
		return err == nil && f.rdb.XLen(bg, stream.Ingest).Val() == 0 && pending.Count == 0
	}
	deadline := time.Now().Add(30 * time.Second)
	for !drained() && time.Now().Before(deadline) {
		time.Sleep(20 * time.Millisecond)
	}
	cancel()
	if err := <-done; err != nil {
		f.t.Fatal(err)
	}
	if !drained() {
		f.t.Fatal("stream not drained before the deadline")
	}
}

// storeRealCall appends one 2026-10-17 call's uploads: its webhooks (when given), its spans as one
// OTLP trace export, its session report and its metric exports.
func (f *fixture) storeRealCall(webhooks, spans, report string, metrics ...string) {
	f.t.Helper()
	if webhooks != "" {
		for _, line := range lines(f.t, webhooks) {
			var w struct {
				EventID string `json:"event_id"`
				Payload string `json:"payload"`
			}
			if err := json.Unmarshal(line, &w); err != nil {
				f.t.Fatal(err)
			}
			f.append(event.KindLiveKitWebhook, w.EventID, []byte(w.Payload))
		}
	}
	f.append(event.KindAgentTraces, "", f.export(f.readSpans(spans)))
	b, err := fs.ReadFile(fixtures, report)
	if err != nil {
		f.t.Fatal(err)
	}
	f.append(event.KindAgentSessionReport, "", b)
	for _, m := range metrics {
		b, err := fs.ReadFile(fixtures, m)
		if err != nil {
			f.t.Fatal(err)
		}
		f.append(event.KindAgentMetrics, "", b)
	}
	f.store()
}

// fixtures holds the test data of internal/ packages, read by paths such as "api/testdata/x".
var fixtures = os.DirFS("..")

func lines(t *testing.T, path string) [][]byte {
	t.Helper()
	file, err := fixtures.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = file.Close() }()
	var out [][]byte
	sc := bufio.NewScanner(file)
	sc.Buffer(make([]byte, 1<<20), 1<<24)
	for sc.Scan() {
		out = append(out, append([]byte(nil), sc.Bytes()...))
	}
	if err := sc.Err(); err != nil {
		t.Fatal(err)
	}
	return out
}

// readSpans reads spans stored as JSON lines, the shape the fixtures were exported in.
func (f *fixture) readSpans(path string) []store.Span {
	f.t.Helper()
	var out []store.Span
	for _, line := range lines(f.t, path) {
		var row struct {
			TraceID    string            `json:"trace_id"`
			SpanID     string            `json:"span_id"`
			ParentID   string            `json:"parent_span_id"`
			Name       string            `json:"name"`
			Start      time.Time         `json:"start"`
			End        time.Time         `json:"end"`
			Attributes map[string]string `json:"attributes"`
		}
		if err := json.Unmarshal(line, &row); err != nil {
			f.t.Fatal(err)
		}
		out = append(out, store.Span{TraceID: row.TraceID, SpanID: row.SpanID, ParentSpanID: row.ParentID, Name: row.Name, Start: row.Start, End: row.End, Attributes: row.Attributes})
	}
	return out
}

// export encodes spans as one OTLP trace export, as the agent uploads them.
func (f *fixture) export(spans []store.Span) []byte {
	f.t.Helper()
	td := ptrace.NewTraces()
	ss := td.ResourceSpans().AppendEmpty().ScopeSpans().AppendEmpty()
	ss.Scope().SetName("livekit-agents")
	for _, s := range spans {
		sp := ss.Spans().AppendEmpty()
		sp.SetTraceID(traceID(f.t, s.TraceID))
		sp.SetSpanID(spanID(f.t, s.SpanID))
		if s.ParentSpanID != "" {
			sp.SetParentSpanID(spanID(f.t, s.ParentSpanID))
		}
		sp.SetName(s.Name)
		if !s.Start.IsZero() {
			sp.SetStartTimestamp(pcommon.NewTimestampFromTime(s.Start))
		}
		if !s.End.IsZero() {
			sp.SetEndTimestamp(pcommon.NewTimestampFromTime(s.End))
		}
		for k, v := range s.Attributes {
			sp.Attributes().PutStr(k, v)
		}
	}
	b, err := ptraceotlp.NewExportRequestFromTraces(td).MarshalProto()
	if err != nil {
		f.t.Fatal(err)
	}
	return b
}

func traceID(t *testing.T, s string) pcommon.TraceID {
	t.Helper()
	var id pcommon.TraceID
	b, err := hex.DecodeString(s)
	if err != nil || len(b) != len(id) {
		t.Fatalf("trace id %q: %v", s, err)
	}
	copy(id[:], b)
	return id
}

func spanID(t *testing.T, s string) pcommon.SpanID {
	t.Helper()
	var id pcommon.SpanID
	b, err := hex.DecodeString(s)
	if err != nil || len(b) != len(id) {
		t.Fatalf("span id %q: %v", s, err)
	}
	copy(id[:], b)
	return id
}

// api returns the API over the real stores; traces overrides the ClickHouse reads when given.
func (f *fixture) api(traces Traces) *API {
	if traces == nil {
		traces = f.events
	}
	return &API{Projects: f.pg, Calls: f.pg, Traces: traces, Log: f.log, Now: time.Now}
}

// callID returns the id of the test project's call in a room.
func (f *fixture) callID(roomSID string) string {
	f.t.Helper()
	var id string
	if err := f.pool.QueryRow(context.Background(),
		`SELECT id::text FROM calls WHERE organisation_id = 'default' AND project_id = $1 AND room_sid = $2`, f.project, roomSID).Scan(&id); err != nil {
		f.t.Fatalf("call for %s: %v", roomSID, err)
	}
	return id
}

var (
	contract     validator.Validator
	contractErr  error
	contractOnce sync.Once
)

// get requests a path from a, checks the response against docs/api/openapi.yaml, and decodes it.
// The contract is OpenAPI 3.1.1, so it is read with libopenapi-validator: kin-openapi does not read
// 3.1 documents in full.
func (f *fixture) get(a *API, path string, out any) int {
	f.t.Helper()
	return f.getAuth(a, path, "", out)
}

// getAuth is get with an Authorization header, sent when auth is not empty.
func (f *fixture) getAuth(a *API, path, auth string, out any) int {
	f.t.Helper()
	contractOnce.Do(func() {
		spec, err := os.ReadFile("../../docs/api/openapi.yaml")
		if err != nil {
			contractErr = err
			return
		}
		doc, err := libopenapi.NewDocument(spec)
		if err != nil {
			contractErr = err
			return
		}
		v, errs := validator.NewValidator(doc)
		if len(errs) > 0 {
			contractErr = fmt.Errorf("validator: %v", errs)
			return
		}
		contract = v
	})
	if contractErr != nil {
		f.t.Fatal(contractErr)
	}
	req := httptest.NewRequest(http.MethodGet, path, nil)
	if auth != "" {
		req.Header.Set("Authorization", auth)
	}
	rec := httptest.NewRecorder()
	a.Routes().ServeHTTP(rec, req)
	body := rec.Body.Bytes()
	res := rec.Result()
	res.Body = io.NopCloser(bytes.NewReader(body))
	if ok, errs := contract.ValidateHttpResponse(req, res); !ok {
		for _, e := range errs {
			f.t.Errorf("contract: %s: %s", e.Message, e.Reason)
			for _, se := range e.SchemaValidationErrors {
				f.t.Errorf("  at %s: %s", se.FieldPath, se.Reason)
			}
		}
	}
	if out != nil {
		if err := json.Unmarshal(body, out); err != nil {
			f.t.Fatalf("decode %s: %v", body, err)
		}
	}
	return res.StatusCode
}
