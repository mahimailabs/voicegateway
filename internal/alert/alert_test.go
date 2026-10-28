package alert_test

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"os"
	"sync"
	"testing"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
	standardwebhooks "github.com/standard-webhooks/standard-webhooks/libraries/go"
	"github.com/testcontainers/testcontainers-go"
	tcpostgres "github.com/testcontainers/testcontainers-go/modules/postgres"

	"github.com/mahimailabs/voicegateway/internal/alert"
	"github.com/mahimailabs/voicegateway/internal/scrape"
	"github.com/mahimailabs/voicegateway/internal/seal"
	"github.com/mahimailabs/voicegateway/internal/store"
	"github.com/mahimailabs/voicegateway/internal/store/pgstore"
)

// The same image, tag and digest as deploy/compose.yaml.
const postgresImage = "postgres:16.15-alpine@sha256:721873c34ceb9f8d8fc265984940dc982404c105f19ad51be9fdc5970a6080ea"

const secret = "whsec_MfKQ9r8GKYqrTwjUPD8ILPZIo2LaLaSw"

type fixture struct {
	t       *testing.T
	pool    *pgxpool.Pool
	store   *pgstore.Store
	eval    *alert.Evaluator
	deliver *alert.Deliverer
	now     time.Time
	mu      sync.Mutex
}

// setup starts an empty Postgres with one project. It skips with -short or without Docker on a
// laptop; in CI a missing Docker fails the test (CLAUDE.md Code quality).
func setup(t *testing.T) *fixture {
	t.Helper()
	if os.Getenv("CI") == "" {
		if testing.Short() {
			t.Skip("needs Docker; runs in CI")
		}
		testcontainers.SkipIfProviderIsNotHealthy(t)
	}
	ctx := context.Background()
	pg, err := tcpostgres.Run(ctx, postgresImage,
		tcpostgres.WithDatabase("voicegateway"), tcpostgres.WithUsername("voicegateway"), tcpostgres.WithPassword("pg-test"),
		tcpostgres.BasicWaitStrategies())
	testcontainers.CleanupContainer(t, pg)
	if err != nil {
		t.Fatalf("start postgres: %v", err)
	}
	url, err := pg.ConnectionString(ctx, "sslmode=disable")
	if err != nil {
		t.Fatal(err)
	}
	pool, err := pgxpool.New(ctx, url)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(pool.Close)
	if _, err := pgstore.Migrate(ctx, pool); err != nil {
		t.Fatal(err)
	}
	sealer, err := seal.New(bytes.Repeat([]byte{7}, seal.KeySize))
	if err != nil {
		t.Fatal(err)
	}
	s := pgstore.NewStore(pool, sealer)
	if _, err := s.CreateProject(ctx, pgstore.NewProject{OrganisationID: "default", ID: "one", Name: "One", LiveKitURL: "wss://example.livekit.cloud", LiveKitAPIKey: "APIone", LiveKitAPISecret: "secret"}); err != nil {
		t.Fatal(err)
	}
	f := &fixture{t: t, pool: pool, store: s, now: time.Now().UTC()}
	log := slog.New(slog.NewTextHandler(io.Discard, nil))
	composer := alert.Composer{PublicURL: "https://vg.example.com", Now: f.clock}
	f.eval = &alert.Evaluator{Store: s, Compose: composer.Compose, Log: log}
	f.deliver = &alert.Deliverer{Store: s, HTTP: http.DefaultClient, Log: log, Now: f.clock}
	return f
}

func (f *fixture) clock() time.Time {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.now
}

func (f *fixture) advance(d time.Duration) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.now = f.now.Add(d)
}

func (f *fixture) exec(sql string, args ...any) {
	f.t.Helper()
	if _, err := f.pool.Exec(context.Background(), sql, args...); err != nil {
		f.t.Fatalf("%s: %v", sql, err)
	}
}

func (f *fixture) count(sql string, args ...any) int {
	f.t.Helper()
	var n int
	if err := f.pool.QueryRow(context.Background(), sql, args...).Scan(&n); err != nil {
		f.t.Fatalf("%s: %v", sql, err)
	}
	return n
}

func (f *fixture) tick() {
	f.t.Helper()
	if err := f.eval.Tick(context.Background(), f.clock()); err != nil {
		f.t.Fatal(err)
	}
}

func (f *fixture) send() {
	f.t.Helper()
	if err := f.deliver.Tick(context.Background(), f.clock()); err != nil {
		f.t.Fatal(err)
	}
}

// webhook configures the project's webhook channel at url and routes rule to it.
func (f *fixture) webhook(url, rule string) {
	f.t.Helper()
	config, err := json.Marshal(store.AlertWebhookConfig{URL: url, Secret: secret}) //nolint:gosec // G117: the configuration the store seals
	if err != nil {
		f.t.Fatal(err)
	}
	ctx := context.Background()
	if err := f.store.PutAlertChannel(ctx, "default", "one", "webhook", config, "receiver"); err != nil {
		f.t.Fatal(err)
	}
	if _, err := f.store.PutAlertRule(ctx, "default", "one", store.AlertRule{RuleID: rule, On: true, To: []string{"webhook"}}); err != nil {
		f.t.Fatal(err)
	}
}

// call stores one finished call of agent, evaluated with turn data, ended at end, with a slow-answer
// occurrence when slow.
func (f *fixture) call(agent string, end time.Time, slow bool) string {
	f.t.Helper()
	var id string
	ctx := context.Background()
	err := f.pool.QueryRow(ctx, `INSERT INTO calls (organisation_id, project_id, room_name, room_sid, started_at, ended_at, end_state, issue_evaluated_at, issue_source_available)
		VALUES ('default', 'one', 'room', gen_random_uuid()::text, $1, $2, 'observed', $2, true) RETURNING id::text`, end.Add(-time.Minute), end).Scan(&id)
	if err != nil {
		f.t.Fatal(err)
	}
	f.exec(`INSERT INTO call_summaries (call_id, agent_key, computed_at) VALUES ($1, $2, now())`, id, agent)
	if slow {
		f.exec(`INSERT INTO issues (organisation_id, project_id, id, title, kind, source, severity) VALUES ('default', 'one', 'slow', 'Slow answers', 'event', 'calls', 'medium') ON CONFLICT DO NOTHING`)
		f.exec(`INSERT INTO issue_occurrences (organisation_id, project_id, issue_id, source_id, call_id, occurred_at, detail)
			VALUES ('default', 'one', 'slow', $1::text, $1::text::uuid, $2, '{"where":"room","detail":"1 turns exceeded the 1500 ms response budget"}')`, id, end)
	}
	return id
}

// Acceptance check 3: the same webhook stored twice is one occurrence and at most one firing
// notification.
func TestRedeliveredWebhookFiresOnce(t *testing.T) {
	f := setup(t)
	f.webhook("http://127.0.0.1:1/never", "recording-lost")
	finding := store.MediaFinding{OrganisationID: "default", ProjectID: "one", Layer: "egress", EventID: "evt-1", ComponentID: "EG_1", Status: "EGRESS_FAILED"}
	for range 2 {
		if err := f.store.PutMediaFinding(context.Background(), finding); err != nil {
			t.Fatal(err)
		}
		f.tick()
	}
	if n := f.count(`SELECT count(*) FROM alert_occurrences WHERE rule_id = 'recording-lost'`); n != 1 {
		t.Fatalf("occurrences = %d, want 1", n)
	}
	if n := f.count(`SELECT count(*) FROM alert_deliveries WHERE change = 'firing'`); n != 1 {
		t.Fatalf("firing deliveries = %d, want 1", n)
	}
}

// Acceptance check 4: 10 slow calls in an hour on one agent are one incident and one firing
// notification, then reminders no more than every 4 hours, carrying the count since the last one.
func TestSlowCallsGroupIntoOneIncident(t *testing.T) {
	f := setup(t)
	f.webhook("http://127.0.0.1:1/never", "slow-answer")
	start := f.clock()
	for i := range 10 {
		f.call("support-agent", start.Add(-time.Hour+time.Duration(i)*6*time.Minute), true)
		f.tick()
	}
	if n := f.count(`SELECT count(*) FROM alert_incidents`); n != 1 {
		t.Fatalf("incidents = %d, want 1", n)
	}
	if n := f.count(`SELECT occurrences FROM alert_incidents`); n != 10 {
		t.Fatalf("occurrences = %d, want 10", n)
	}
	if n := f.count(`SELECT count(*) FROM alert_deliveries`); n != 1 {
		t.Fatalf("deliveries = %d, want 1 firing", n)
	}
	f.advance(3 * time.Hour)
	f.tick()
	if n := f.count(`SELECT count(*) FROM alert_deliveries WHERE change = 'reminder'`); n != 0 {
		t.Fatalf("reminders after 3 h = %d, want 0", n)
	}
	f.advance(time.Hour)
	f.tick()
	f.tick()
	if n := f.count(`SELECT count(*) FROM alert_deliveries WHERE change = 'reminder'`); n != 1 {
		t.Fatalf("reminders after 4 h = %d, want 1", n)
	}
	var since int
	if err := f.pool.QueryRow(context.Background(), `SELECT (message->'incident'->>'sinceLastMessage')::int FROM alert_deliveries WHERE change = 'reminder'`).Scan(&since); err != nil {
		t.Fatal(err)
	}
	if since != 9 {
		t.Fatalf("reminder sinceLastMessage = %d, want 9", since)
	}
	// Nothing new since the reminder: no further one.
	f.advance(5 * time.Hour)
	f.tick()
	if n := f.count(`SELECT count(*) FROM alert_deliveries WHERE change = 'reminder'`); n != 1 {
		t.Fatalf("reminders with nothing new = %d, want 1", n)
	}
}

// Acceptance check 5: a channel answering 500 three times then 200 gets one message, with three
// failed attempts recorded, on the 30 s, 1 min, 2 min schedule, every attempt under the same key.
func TestRetriesUntilDelivered(t *testing.T) {
	f := setup(t)
	var mu sync.Mutex
	var ids []string
	answers := []int{500, 500, 500, 200}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		wh, _ := standardwebhooks.NewWebhook(secret)
		if err := wh.VerifyIgnoringTimestamp(body, r.Header); err != nil {
			w.WriteHeader(http.StatusUnauthorized)
			return
		}
		mu.Lock()
		defer mu.Unlock()
		ids = append(ids, r.Header.Get("webhook-id"))
		w.WriteHeader(answers[len(ids)-1])
	}))
	defer srv.Close()
	f.webhook(srv.URL, "recording-lost")
	if err := f.store.PutMediaFinding(context.Background(), store.MediaFinding{OrganisationID: "default", ProjectID: "one", Layer: "egress", EventID: "evt-1", ComponentID: "EG_1", Status: "EGRESS_FAILED"}); err != nil {
		t.Fatal(err)
	}
	f.tick()
	// The delivery is due from when Postgres stored it; the fixture clock started a little earlier.
	f.advance(time.Minute)
	f.send()
	for _, wait := range []time.Duration{30 * time.Second, time.Minute, 2 * time.Minute} {
		f.advance(wait - time.Second)
		f.send() // not due yet
		f.advance(time.Second)
		f.send()
	}
	if len(ids) != 4 {
		t.Fatalf("attempts = %d, want 4", len(ids))
	}
	for _, id := range ids {
		if id != ids[0] {
			t.Fatalf("webhook ids differ across retries: %v", ids)
		}
	}
	if n := f.count(`SELECT count(*) FROM alert_delivery_attempts WHERE status = 'HTTP 500'`); n != 3 {
		t.Fatalf("failed attempts = %d, want 3", n)
	}
	if n := f.count(`SELECT count(*) FROM alert_deliveries WHERE state = 'delivered' AND attempts = 4`); n != 1 {
		t.Fatal("delivery is not delivered after 4 attempts")
	}
	f.advance(time.Hour)
	f.send()
	if len(ids) != 4 {
		t.Fatal("a delivered change was sent again")
	}
}

// Acceptance check 6: a channel answering 400 gets no retry; the channel is broken, the delivery
// failed, and the failure is on the Issues page without the URL.
func TestRefusalBreaksChannel(t *testing.T) {
	f := setup(t)
	calls := 0
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls++
		w.WriteHeader(http.StatusBadRequest)
		_, _ = io.WriteString(w, "bad payload, token=s3cret")
	}))
	defer srv.Close()
	f.webhook(srv.URL+"/hook?token=s3cret", "recording-lost")
	if err := f.store.PutMediaFinding(context.Background(), store.MediaFinding{OrganisationID: "default", ProjectID: "one", Layer: "egress", EventID: "evt-1", ComponentID: "EG_1", Status: "EGRESS_FAILED"}); err != nil {
		t.Fatal(err)
	}
	f.tick()
	f.send()
	f.advance(time.Hour)
	f.send()
	if calls != 1 {
		t.Fatalf("attempts = %d, want 1", calls)
	}
	channels, err := f.store.AlertChannels(context.Background(), "default", "one")
	if err != nil {
		t.Fatal(err)
	}
	if len(channels) != 1 || !channels[0].Broken || channels[0].Last == nil || channels[0].Last.State != "failed" || *channels[0].Last.LastStatus != "HTTP 400" {
		t.Fatalf("channel = %+v, want broken with a failed HTTP 400 delivery", channels)
	}
	if n := f.count(`SELECT count(*) FROM issue_occurrences WHERE issue_id = 'alert-not-delivered' AND NOT detail::text LIKE '%s3cret%'`); n != 1 {
		t.Fatalf("issue occurrences without the secret = %d, want 1", n)
	}
}

// An address the worker's guard refuses (here loopback on a public-only server, as on Cloud) is
// final on the first attempt, as a refusal is: the channel is broken, not retried for a day.
func TestGuardRefusalBreaksChannel(t *testing.T) {
	f := setup(t)
	calls := 0
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { calls++ }))
	defer srv.Close()
	f.deliver.HTTP, f.deliver.Refused = scrape.NewWorkerClient(true), scrape.Refused
	f.webhook(srv.URL+"/hook", "recording-lost")
	if err := f.store.PutMediaFinding(context.Background(), store.MediaFinding{OrganisationID: "default", ProjectID: "one", Layer: "egress", EventID: "evt-1", ComponentID: "EG_1", Status: "EGRESS_FAILED"}); err != nil {
		t.Fatal(err)
	}
	f.tick()
	f.advance(time.Minute) // the delivery is due from the database clock, ahead of the fixture's
	f.send()
	channels, err := f.store.AlertChannels(context.Background(), "default", "one")
	if err != nil {
		t.Fatal(err)
	}
	if calls != 0 || len(channels) != 1 || !channels[0].Broken || channels[0].Last == nil || channels[0].Last.State != "failed" || *channels[0].Last.LastStatus != "address_not_allowed" {
		t.Fatalf("calls %d, channel %+v: want no call and a broken channel with address_not_allowed", calls, channels)
	}
}

// Acceptance check 7: a source that stops reporting keeps its incident firing, marked silent, and
// never resolves it.
func TestSilentSourceStaysFiring(t *testing.T) {
	f := setup(t)
	f.webhook("http://127.0.0.1:1/never", "slow-answer")
	f.call("support-agent", f.clock().Add(-time.Minute), true)
	f.tick()
	f.advance(6 * time.Hour)
	f.tick()
	var state string
	var silent *time.Time
	var hold *string
	if err := f.pool.QueryRow(context.Background(), `SELECT state, silent_since, hold_reason FROM alert_incidents`).Scan(&state, &silent, &hold); err != nil {
		t.Fatal(err)
	}
	if state != "firing" || silent == nil || hold == nil {
		t.Fatalf("state %s, silent %v, hold %v: want firing, silent and held", state, silent, hold)
	}
}

// The resolve rule of a call rule: 5 calls in the last 30 minutes, none slow, resolve it and send
// a resolved message; a slow call within 10 minutes reopens it without a second firing message.
func TestCleanCallsResolveAndFlapReopens(t *testing.T) {
	f := setup(t)
	f.webhook("http://127.0.0.1:1/never", "slow-answer")
	f.call("support-agent", f.clock().Add(-2*time.Hour), true)
	f.tick()
	for i := range 4 {
		f.call("support-agent", f.clock().Add(-time.Duration(i+1)*time.Minute), false)
	}
	f.tick()
	if n := f.count(`SELECT count(*) FROM alert_incidents WHERE state = 'firing'`); n != 1 {
		t.Fatal("4 clean calls resolved the incident; 5 are needed")
	}
	f.call("support-agent", f.clock().Add(-10*time.Second), false)
	f.tick()
	if n := f.count(`SELECT count(*) FROM alert_incidents WHERE state = 'resolved'`); n != 1 {
		t.Fatal("5 clean calls did not resolve the incident")
	}
	if n := f.count(`SELECT count(*) FROM alert_deliveries WHERE change = 'resolved'`); n != 1 {
		t.Fatal("no resolved message")
	}
	f.advance(5 * time.Minute)
	f.call("support-agent", f.clock(), true)
	f.tick()
	if n := f.count(`SELECT count(*) FROM alert_incidents`); n != 1 {
		t.Fatal("a slow call within 10 minutes opened a new incident")
	}
	if n := f.count(`SELECT count(*) FROM alert_deliveries WHERE change = 'firing'`); n != 1 {
		t.Fatal("the flap guard sent a second firing message")
	}
}

// A rule that is off still opens incidents, so history stays, but sends nothing.
func TestDisabledRuleSendsNothing(t *testing.T) {
	f := setup(t)
	f.webhook("http://127.0.0.1:1/never", "slow-answer")
	if _, err := f.store.PutAlertRule(context.Background(), "default", "one", store.AlertRule{RuleID: "slow-answer", On: false, To: []string{"webhook"}}); err != nil {
		t.Fatal(err)
	}
	f.call("support-agent", f.clock(), true)
	f.tick()
	if n := f.count(`SELECT count(*) FROM alert_incidents`); n != 1 {
		t.Fatal("a disabled rule opened no incident")
	}
	if n := f.count(`SELECT count(*) FROM alert_deliveries`); n != 0 {
		t.Fatal("a disabled rule sent")
	}
}
