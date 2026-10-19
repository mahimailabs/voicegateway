package ingest

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/base64"
	"errors"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/livekit/protocol/auth"
	"github.com/redis/go-redis/v9"
	"github.com/testcontainers/testcontainers-go"
	tcpostgres "github.com/testcontainers/testcontainers-go/modules/postgres"
	tcredis "github.com/testcontainers/testcontainers-go/modules/redis"

	"github.com/mahimailabs/voicegateway/internal/event"
	"github.com/mahimailabs/voicegateway/internal/seal"
	"github.com/mahimailabs/voicegateway/internal/store"
	"github.com/mahimailabs/voicegateway/internal/store/pgstore"
	"github.com/mahimailabs/voicegateway/internal/stream"
)

// The same images, tags and digests as deploy/compose.yaml.
const (
	postgresImage = "postgres:16.15-alpine@sha256:721873c34ceb9f8d8fc265984940dc982404c105f19ad51be9fdc5970a6080ea"
	redisImage    = "redis:8.10.2-alpine@sha256:3811787313eba226a2ef38658c6ccb91cd5e110edc89c37767de373120a0e5a0"
)

const (
	testKey    = "APItestkey"
	testSecret = "test-secret-that-is-long-enough-for-hs256"
	// Shaped like a LiveKit room_started webhook, with only the fields read here.
	roomStarted = `{"event":"room_started","room":{"sid":"RM_test","name":"call-1"},"id":"EV_test1","createdAt":"1760616000"}`
)

func needDocker(t *testing.T) {
	t.Helper()
	if os.Getenv("CI") != "" {
		return
	}
	if testing.Short() {
		t.Skip("needs Docker; runs in CI")
	}
	testcontainers.SkipIfProviderIsNotHealthy(t)
}

// fixture is a migrated Postgres holding one project, a Redis, and the handler over both.
type fixture struct {
	handler http.Handler
	uploads *Uploads
	rdb     *redis.Client
}

func newFixture(t *testing.T) fixture {
	t.Helper()
	needDocker(t)
	ctx := context.Background()

	pg, err := tcpostgres.Run(ctx, postgresImage,
		tcpostgres.WithDatabase("voicegateway"), tcpostgres.WithUsername("voicegateway"), tcpostgres.WithPassword("pg-test"),
		tcpostgres.BasicWaitStrategies())
	testcontainers.CleanupContainer(t, pg)
	if err != nil {
		t.Fatalf("start postgres: %v", err)
	}
	pgURL, err := pg.ConnectionString(ctx, "sslmode=disable")
	if err != nil {
		t.Fatal(err)
	}
	pool, err := pgxpool.New(ctx, pgURL)
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
	projects := pgstore.NewStore(pool, sealer)
	if _, err := projects.CreateProject(ctx, pgstore.NewProject{
		OrganisationID: "default", ID: "production", Name: "Production",
		LiveKitURL: "ws://localhost:7880", LiveKitAPIKey: testKey, LiveKitAPISecret: testSecret,
	}); err != nil {
		t.Fatal(err)
	}

	rd, err := tcredis.Run(ctx, redisImage)
	testcontainers.CleanupContainer(t, rd)
	if err != nil {
		t.Fatalf("start redis: %v", err)
	}
	rdURL, err := rd.ConnectionString(ctx)
	if err != nil {
		t.Fatal(err)
	}
	opts, err := redis.ParseURL(rdURL)
	if err != nil {
		t.Fatal(err)
	}
	rdb := redis.NewClient(opts)
	t.Cleanup(func() { _ = rdb.Close() })

	producer := stream.NewProducer(rdb, stream.Ingest)
	log := slog.New(slog.NewTextHandler(io.Discard, nil))
	return fixture{
		handler: &Webhooks{Projects: ScopedProjects{Store: projects, OrganisationID: "default", ProjectID: "production"}, Stream: producer, Log: log, Now: time.Now},
		uploads: &Uploads{Projects: ScopedProjects{Store: projects, OrganisationID: "default", ProjectID: "production"}, Stream: producer, Log: log, Now: time.Now},
		rdb:     rdb,
	}
}

// sign returns the Authorization value LiveKit sends: a JWT from key and secret carrying the body's
// SHA-256, built with livekit/protocol as the server does (webhook/url_notifier.go).
func sign(t *testing.T, key, secret, body string) string {
	t.Helper()
	sum := sha256.Sum256([]byte(body))
	jwt, err := auth.NewAccessToken(key, secret).SetValidFor(5 * time.Minute).
		SetSha256(base64.StdEncoding.EncodeToString(sum[:])).ToJWT()
	if err != nil {
		t.Fatal(err)
	}
	return jwt
}

// A webhook is appended only when the project's own key and secret signed this exact body; every
// other request is 401 and the stream stays empty.
func TestWebhookVerified(t *testing.T) {
	f := newFixture(t)
	cases := []struct {
		name   string
		auth   string
		body   string
		status int
	}{
		{"no signature", "", roomStarted, http.StatusUnauthorized},
		{"garbage token", "not-a-jwt", roomStarted, http.StatusUnauthorized},
		{"unknown key", sign(t, "APIother", testSecret, roomStarted), roomStarted, http.StatusUnauthorized},
		{"wrong secret", sign(t, testKey, "another-secret-that-is-long-enough", roomStarted), roomStarted, http.StatusUnauthorized},
		{"body changed after signing", sign(t, testKey, testSecret, roomStarted), strings.Replace(roomStarted, "call-1", "call-2", 1), http.StatusUnauthorized},
		{"too large", sign(t, testKey, testSecret, roomStarted), strings.Repeat("x", maxWebhookBody+1), http.StatusRequestEntityTooLarge},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			rec := post(f.handler, c.auth, c.body)
			if rec.Code != c.status {
				t.Fatalf("status = %d, want %d", rec.Code, c.status)
			}
			if n := f.rdb.XLen(context.Background(), stream.Ingest).Val(); n != 0 {
				t.Fatalf("stream length = %d, want 0", n)
			}
		})
	}

	rec := post(f.handler, sign(t, testKey, testSecret, roomStarted), roomStarted)
	if rec.Code != http.StatusOK {
		t.Fatalf("signed webhook: status = %d, want 200", rec.Code)
	}
	e := f.only(t)
	if e.ID != "EV_test1" || e.Kind != event.KindLiveKitWebhook || e.OrganisationID != "default" || e.ProjectID != "production" ||
		string(e.Payload) != roomStarted {
		t.Fatalf("envelope = %+v, want EV_test1 for default/production with the body unchanged", e)
	}
}

// When the stream cannot take the event the answer is 503, so LiveKit retries it, never 200.
func TestWebhookStreamDown(t *testing.T) {
	f := newFixture(t)
	if err := f.rdb.Close(); err != nil {
		t.Fatal(err)
	}
	rec := post(f.handler, sign(t, testKey, testSecret, roomStarted), roomStarted)
	if rec.Code != http.StatusServiceUnavailable {
		t.Fatalf("status = %d, want 503", rec.Code)
	}
}

func post(h http.Handler, authz, body string) *httptest.ResponseRecorder {
	req := httptest.NewRequest(http.MethodPost, "/livekit/webhook", strings.NewReader(body))
	req.Header.Set("Content-Type", "application/webhook+json")
	if authz != "" {
		req.Header.Set("Authorization", authz)
	}
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	return rec
}

// brokenProjects is a store that cannot answer, as when Postgres is down.
type brokenProjects struct{}

func (brokenProjects) ProjectByLiveKitKey(context.Context, string) (store.Project, error) {
	return store.Project{}, errors.New("connection refused")
}

func (brokenProjects) LiveKitSecret(context.Context, string, string) (string, error) {
	return "", errors.New("connection refused")
}

// appendCounter records whether anything was appended.
type appendCounter struct{ n int }

func (a *appendCounter) Append(context.Context, event.Envelope) (string, error) {
	a.n++
	return "1-0", nil
}

// When the project cannot be looked up the answer is 503, not 401: the signature was never checked,
// so calling it bad would be wrong, and LiveKit retries a 503.
func TestWebhookProjectLookupDown(t *testing.T) {
	appended := &appendCounter{}
	h := &Webhooks{Projects: brokenProjects{}, Stream: appended, Log: slog.New(slog.NewTextHandler(io.Discard, nil)), Now: time.Now}
	rec := post(h, sign(t, testKey, testSecret, roomStarted), roomStarted)
	if rec.Code != http.StatusServiceUnavailable {
		t.Fatalf("status = %d, want 503", rec.Code)
	}
	if appended.n != 0 {
		t.Fatalf("appended %d envelopes, want 0", appended.n)
	}
}

// only reads the stream and returns its single envelope.
func (f fixture) only(t *testing.T) event.Envelope {
	t.Helper()
	c := stream.NewConsumer(f.rdb, stream.Ingest, "test", "t1")
	if err := c.Ensure(context.Background()); err != nil {
		t.Fatal(err)
	}
	msgs, err := c.Read(context.Background(), 10, time.Second)
	if err != nil {
		t.Fatal(err)
	}
	if len(msgs) != 1 || msgs[0].Err != nil {
		t.Fatalf("stream holds %+v, want one envelope", msgs)
	}
	return msgs[0].Envelope
}
