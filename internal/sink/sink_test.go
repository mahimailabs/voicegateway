package sink

import (
	"context"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"os"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/ClickHouse/clickhouse-go/v2"
	"github.com/ClickHouse/clickhouse-go/v2/lib/driver"
	"github.com/redis/go-redis/v9"
	"github.com/testcontainers/testcontainers-go"
	tcclickhouse "github.com/testcontainers/testcontainers-go/modules/clickhouse"
	tcredis "github.com/testcontainers/testcontainers-go/modules/redis"

	"github.com/mahimailabs/voicegateway/internal/event"
	"github.com/mahimailabs/voicegateway/internal/store"
	"github.com/mahimailabs/voicegateway/internal/store/chstore"
	"github.com/mahimailabs/voicegateway/internal/stream"
)

// The same images, tags and digests as deploy/compose.yaml.
const (
	clickhouseImage = "clickhouse/clickhouse-server:26.1.12.23-alpine@sha256:369bd68942569e0effdf0aa68b4f844a74d97b73e281cf8012ce90e9865cc411"
	redisImage      = "redis:8.10.2-alpine@sha256:3811787313eba226a2ef38658c6ccb91cd5e110edc89c37767de373120a0e5a0"
)

// Shaped like LiveKit webhooks, with only the fields read here.
const (
	roomStarted  = `{"event":"room_started","room":{"sid":"RM_1","name":"call-1","turnPassword":"turn-secret"},"id":"EV_1"}`
	roomFinished = `{"event":"room_finished","room":{"sid":"RM_1","name":"call-1"},"id":"EV_2"}`
)

type fixture struct {
	rdb    *redis.Client
	events *chstore.Store
	ch     driver.Conn
}

func newFixture(t *testing.T) fixture {
	t.Helper()
	if os.Getenv("CI") == "" {
		if testing.Short() {
			t.Skip("needs Docker; runs in CI")
		}
		testcontainers.SkipIfProviderIsNotHealthy(t)
	}
	ctx := context.Background()

	ch, err := tcclickhouse.Run(ctx, clickhouseImage,
		tcclickhouse.WithDatabase("voicegateway"), tcclickhouse.WithUsername("voicegateway"), tcclickhouse.WithPassword("ch-test"))
	testcontainers.CleanupContainer(t, ch)
	if err != nil {
		t.Fatalf("start clickhouse: %v", err)
	}
	chURL, err := ch.ConnectionString(ctx)
	if err != nil {
		t.Fatal(err)
	}
	for range 2 { // the second run applies nothing and is not an error
		if _, err := chstore.Migrate(ctx, chURL); err != nil {
			t.Fatal(err)
		}
	}
	events, err := chstore.Open(chURL)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = events.Close() })
	opts, err := clickhouse.ParseDSN(chURL)
	if err != nil {
		t.Fatal(err)
	}
	conn, err := clickhouse.Open(opts)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = conn.Close() })

	rd, err := tcredis.Run(ctx, redisImage)
	testcontainers.CleanupContainer(t, rd)
	if err != nil {
		t.Fatalf("start redis: %v", err)
	}
	rdURL, err := rd.ConnectionString(ctx)
	if err != nil {
		t.Fatal(err)
	}
	ropts, err := redis.ParseURL(rdURL)
	if err != nil {
		t.Fatal(err)
	}
	rdb := redis.NewClient(ropts)
	t.Cleanup(func() { _ = rdb.Close() })
	return fixture{rdb: rdb, events: events, ch: conn}
}

func (f fixture) sink(name string, events Events) *Sink {
	return &Sink{
		Stream:    stream.NewConsumer(f.rdb, stream.Ingest, "worker", name),
		Events:    events,
		Join:      noJoin{},
		Log:       slog.New(slog.NewTextHandler(io.Discard, nil)),
		Batch:     100,
		Block:     100 * time.Millisecond,
		ClaimIdle: 200 * time.Millisecond,
		Backoff:   20 * time.Millisecond, MaxBackoff: 100 * time.Millisecond,
		Timeout: 10 * time.Second,
	}
}

func (f fixture) append(t *testing.T, kind event.Kind, id, payload string) {
	t.Helper()
	_, err := stream.NewProducer(f.rdb, stream.Ingest).Append(context.Background(), event.Envelope{
		ID: id, Kind: kind, OrganisationID: "default", ProjectID: "production", ReceivedAt: time.Now(), Payload: []byte(payload),
	})
	if err != nil {
		t.Fatal(err)
	}
}

// noJoin leaves rows unjoined; joining is tested in internal/join.
type noJoin struct{}

func (noJoin) Join(context.Context, []store.Event) error { return nil }

// run runs a sink until cond holds or the deadline passes, then stops it.
func run(t *testing.T, s *Sink, cond func() bool) {
	t.Helper()
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() { done <- s.Run(ctx) }()
	deadline := time.Now().Add(20 * time.Second)
	for !cond() {
		if time.Now().After(deadline) {
			cancel()
			if err := <-done; err != nil {
				t.Errorf("Run = %v", err)
			}
			t.Fatal("condition not met before the deadline")
		}
		time.Sleep(20 * time.Millisecond)
	}
	cancel()
	if err := <-done; err != nil {
		t.Fatalf("Run = %v", err)
	}
}

func (f fixture) rows(t *testing.T) int {
	t.Helper()
	var n uint64
	if err := f.ch.QueryRow(context.Background(), "SELECT count() FROM events FINAL").Scan(&n); err != nil {
		t.Fatal(err)
	}
	return int(n)
}

func (f fixture) streamLen() int64 { return f.rdb.XLen(context.Background(), stream.Ingest).Val() }

// Every envelope lands as one row, attributed, with an unknown event id left empty, and the stream
// is empty once they are stored.
func TestSinkStoresAndAcks(t *testing.T) {
	f := newFixture(t)
	f.append(t, event.KindLiveKitWebhook, "EV_1", roomStarted)
	f.append(t, event.KindLiveKitWebhook, "EV_2", roomFinished)
	f.append(t, event.KindAgentTraces, "", "\x0a\x00")

	run(t, f.sink("w1", f.events), func() bool { return f.streamLen() == 0 })
	if n := f.rows(t); n != 3 {
		t.Fatalf("rows = %d, want 3", n)
	}
	var unknown uint64
	if err := f.ch.QueryRow(context.Background(), `SELECT count() FROM events WHERE kind = 'livekit.agent.traces' AND event_id = '' AND project_id = 'production'`).Scan(&unknown); err != nil || unknown != 1 {
		t.Fatalf("traces row with unknown id = %d, %v; want 1", unknown, err)
	}
	if p := f.rdb.XPending(context.Background(), stream.Ingest, "worker").Val().Count; p != 0 {
		t.Fatalf("pending = %d, want 0", p)
	}
}

// A webhook LiveKit delivers twice, before or after a restart, is stored once.
func TestSinkSkipsStoredWebhook(t *testing.T) {
	f := newFixture(t)
	f.append(t, event.KindLiveKitWebhook, "EV_1", roomStarted)
	f.append(t, event.KindLiveKitWebhook, "EV_1", roomStarted) // same batch
	run(t, f.sink("w1", f.events), func() bool { return f.streamLen() == 0 })

	f.append(t, event.KindLiveKitWebhook, "EV_1", roomStarted) // after a restart
	run(t, f.sink("w2", f.events), func() bool { return f.streamLen() == 0 })

	var n uint64
	if err := f.ch.QueryRow(context.Background(), "SELECT count() FROM events WHERE event_id = 'EV_1'").Scan(&n); err != nil || n != 1 {
		t.Fatalf("rows for EV_1 = %d, %v; want 1", n, err)
	}
}

// The TURN password LiveKit puts in room webhooks is never stored; the rest of the body is.
func TestSinkStripsTURNPassword(t *testing.T) {
	f := newFixture(t)
	f.append(t, event.KindLiveKitWebhook, "EV_1", roomStarted)
	run(t, f.sink("w1", f.events), func() bool { return f.streamLen() == 0 })
	var payload string
	if err := f.ch.QueryRow(context.Background(), "SELECT payload FROM events WHERE event_id = 'EV_1'").Scan(&payload); err != nil {
		t.Fatal(err)
	}
	if strings.Contains(payload, "turn-secret") || strings.Contains(payload, "turnPassword") || !strings.Contains(payload, `"sid":"RM_1"`) {
		t.Fatalf("stored payload = %s", payload)
	}
}

// Entries a worker read and never acknowledged, because it died, are claimed and stored by the
// next one, once.
func TestSinkClaimsFromDeadWorker(t *testing.T) {
	f := newFixture(t)
	f.append(t, event.KindAgentLogs, "", "a")
	f.append(t, event.KindAgentLogs, "", "b")
	dead := stream.NewConsumer(f.rdb, stream.Ingest, "worker", "dead")
	if err := dead.Ensure(context.Background()); err != nil {
		t.Fatal(err)
	}
	if msgs, err := dead.Read(context.Background(), 10, time.Second); err != nil || len(msgs) != 2 {
		t.Fatalf("dead worker read %d, %v", len(msgs), err)
	}
	run(t, f.sink("w1", f.events), func() bool { return f.streamLen() == 0 })
	if n := f.rows(t); n != 2 {
		t.Fatalf("rows = %d, want 2", n)
	}
}

// An entry that cannot be decoded goes to the dead-letter stream with its fields; it is not lost
// and does not hold up the rest.
func TestSinkDeadLettersUndecodable(t *testing.T) {
	f := newFixture(t)
	f.rdb.XAdd(context.Background(), &redis.XAddArgs{Stream: stream.Ingest, Values: []any{"v", "9", "payload", "from the future"}})
	f.append(t, event.KindAgentLogs, "", "a")
	run(t, f.sink("w1", f.events), func() bool { return f.streamLen() == 0 })
	if n := f.rows(t); n != 1 {
		t.Fatalf("rows = %d, want 1", n)
	}
	dead := f.rdb.XRange(context.Background(), stream.DeadLetters(stream.Ingest), "-", "+").Val()
	if len(dead) != 1 || dead[0].Values["payload"] != "from the future" {
		t.Fatalf("dead letters = %+v", dead)
	}
}

// down is ClickHouse unreachable for its first calls: inserts fail with a transient error.
type down struct {
	Events
	failures atomic.Int32
}

func (d *down) Insert(ctx context.Context, rows []store.Event) error {
	if d.failures.Add(-1) >= 0 {
		return errors.New("dial tcp: connection refused")
	}
	return d.Events.Insert(ctx, rows)
}

// While ClickHouse is down nothing is acknowledged or dead-lettered; once it answers, every entry
// is stored.
func TestSinkAcksOnlyAfterInsert(t *testing.T) {
	f := newFixture(t)
	f.append(t, event.KindAgentMetrics, "", "a")
	f.append(t, event.KindAgentMetrics, "", "b")
	outage := &down{Events: f.events}
	outage.failures.Store(1 << 20)

	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() { done <- f.sink("w1", outage).Run(ctx) }()
	time.Sleep(500 * time.Millisecond)
	if n := f.streamLen(); n != 2 {
		t.Fatalf("stream length during outage = %d, want 2", n)
	}
	if n := f.rdb.XLen(ctx, stream.DeadLetters(stream.Ingest)).Val(); n != 0 {
		t.Fatalf("dead letters during outage = %d, want 0", n)
	}
	outage.failures.Store(0)
	deadline := time.Now().Add(20 * time.Second)
	for f.streamLen() != 0 && time.Now().Before(deadline) {
		time.Sleep(20 * time.Millisecond)
	}
	cancel()
	if err := <-done; err != nil {
		t.Errorf("Run = %v", err)
	}
	if n := f.rows(t); n != 2 {
		t.Fatalf("rows after recovery = %d, want 2", n)
	}
}

// poisoned refuses, as a data error, any batch holding a row whose payload is "poison".
type poisoned struct{ Events }

func (p poisoned) Insert(ctx context.Context, rows []store.Event) error {
	for _, r := range rows {
		if string(r.Payload) == "poison" {
			return fmt.Errorf("insert: %w: CANNOT_PARSE_TEXT", store.ErrBadRow)
		}
	}
	return p.Events.Insert(ctx, rows)
}

// When ClickHouse refuses rows for their data, only those rows are dead-lettered; the rest of the
// batch is stored.
func TestSinkDeadLettersOnlyRefusedRows(t *testing.T) {
	f := newFixture(t)
	for _, p := range []string{"a", "poison", "b", "c", "d"} {
		f.append(t, event.KindAgentLogs, "", p)
	}
	run(t, f.sink("w1", poisoned{f.events}), func() bool { return f.streamLen() == 0 })
	if n := f.rows(t); n != 4 {
		t.Fatalf("rows = %d, want 4", n)
	}
	dead := f.rdb.XRange(context.Background(), stream.DeadLetters(stream.Ingest), "-", "+").Val()
	if len(dead) != 1 || dead[0].Values["payload"] != "poison" {
		t.Fatalf("dead letters = %+v, want only the poison row", dead)
	}
}

// A webhook dead-lettered because its body does not parse keeps no TURN password either.
func TestDeadLetterBlanksTURNPassword(t *testing.T) {
	f := newFixture(t)
	f.append(t, event.KindLiveKitWebhook, "EV_9", `{"event":"room_started","room":{"turnPassword":"turn-secret","sid":`)
	run(t, f.sink("w1", f.events), func() bool { return f.streamLen() == 0 })
	dead := f.rdb.XRange(context.Background(), stream.DeadLetters(stream.Ingest), "-", "+").Val()
	if len(dead) != 1 {
		t.Fatalf("dead letters = %d, want 1", len(dead))
	}
	if body, _ := dead[0].Values["payload"].(string); strings.Contains(body, "turn-secret") || !strings.Contains(body, `"turnPassword":""`) {
		t.Fatalf("dead-lettered body = %s", body)
	}
}

// tooBig fails, transiently, any insert of more than one row, as a memory limit would.
type tooBig struct {
	Events
	calls atomic.Int32
}

func (b *tooBig) Insert(ctx context.Context, rows []store.Event) error {
	b.calls.Add(1)
	if len(rows) > 1 {
		return errors.New("code: 241, MEMORY_LIMIT_EXCEEDED")
	}
	return b.Events.Insert(ctx, rows)
}

// A batch that keeps failing for a reason other than its data is halved until it goes in; nothing
// is dead-lettered.
func TestSinkHalvesBatchThatKeepsFailing(t *testing.T) {
	f := newFixture(t)
	for _, p := range []string{"a", "b", "c", "d"} {
		f.append(t, event.KindAgentLogs, "", p)
	}
	run(t, f.sink("w1", &tooBig{Events: f.events}), func() bool { return f.streamLen() == 0 })
	if n := f.rows(t); n != 4 {
		t.Fatalf("rows = %d, want 4", n)
	}
	if n := f.rdb.XLen(context.Background(), stream.DeadLetters(stream.Ingest)).Val(); n != 0 {
		t.Fatalf("dead letters = %d, want 0", n)
	}
}

func TestChunksByBytes(t *testing.T) {
	row := func(n int) store.Event { return store.Event{Payload: make([]byte, n)} }
	got := chunks([]store.Event{row(4), row(4), row(4), row(20), row(1)}, 10)
	var sizes []int
	for _, c := range got {
		sizes = append(sizes, len(c))
	}
	if fmt.Sprint(sizes) != "[2 1 1 1]" {
		t.Fatalf("chunk sizes = %v, want [2 1 1 1]", sizes)
	}
}
