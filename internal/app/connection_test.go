package app

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"testing"
	"testing/synctest"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/livekit/protocol/auth"

	"github.com/mahimailabs/voicegateway/internal/api"
	"github.com/mahimailabs/voicegateway/internal/ingest"
	"github.com/mahimailabs/voicegateway/internal/seal"
	"github.com/mahimailabs/voicegateway/internal/store"
	"github.com/mahimailabs/voicegateway/internal/store/pgstore"
)

// connectionStack is web's ingestion and its API on a real Postgres, with a stream that records
// what was appended and a quota the test switches.
type connectionStack struct {
	pool   *pgxpool.Pool
	sealer *seal.Sealer
	store  *pgstore.Store
	stream *scopedIngestionStream
	quota  *switchQuota
	mux    *http.ServeMux
	api    http.Handler
}

// switchQuota allows everything until refuse names a reason code.
type switchQuota struct{ refuse string }

func (q *switchQuota) Check(context.Context, string) (bool, int, error) {
	return q.refuse == "", 0, nil
}

func (q *switchQuota) Refusal(context.Context, string) string { return q.refuse }

func newConnectionStack(t *testing.T, projects ...store.NewProject) *connectionStack {
	t.Helper()
	pool, _ := newPostgres(t)
	sealer, err := seal.New(bytes.Repeat([]byte{7}, seal.KeySize))
	if err != nil {
		t.Fatal(err)
	}
	st := pgstore.NewStore(pool, sealer)
	ctx := context.Background()
	for _, p := range projects {
		if _, _, err := st.PutOrganisation(ctx, p.OrganisationID, p.OrganisationID); err != nil {
			t.Fatal(err)
		}
		if _, err := st.CreateProject(ctx, p); err != nil {
			t.Fatal(err)
		}
	}
	log := slog.New(slog.NewTextHandler(io.Discard, nil))
	s := &connectionStack{pool: pool, sealer: sealer, store: st, stream: &scopedIngestionStream{}, quota: &switchQuota{}, mux: http.NewServeMux()}
	conns := &ingest.Connections{Store: st, Log: log, Now: time.Now}
	mountIngestion(s.mux, st, s.stream, s.quota, nil, conns, log, time.Now)
	roles := map[string]string{}
	for _, p := range projects {
		roles[p.OrganisationID] = "viewer"
	}
	s.api = (&api.API{
		Projects: st, Connections: st, PublicURL: "https://vg.example", Log: log, Now: time.Now,
		Gate: func(next http.Handler) http.Handler {
			return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				next.ServeHTTP(w, r.WithContext(store.WithPrincipal(r.Context(), store.Principal{UserID: "u", Roles: roles})))
			})
		},
	}).Routes()
	return s
}

func lkProject(org, id, key, secret string) store.NewProject {
	return store.NewProject{OrganisationID: org, ID: id, Name: id, LiveKitURL: "wss://example.livekit.cloud", LiveKitAPIKey: key, LiveKitAPISecret: secret}
}

type connectionRead struct {
	URLs struct {
		Observability *string `json:"observability"`
		Webhook       *string `json:"webhook"`
	} `json:"urls"`
	Webhook   sourceRead `json:"webhook"`
	Agent     sourceRead `json:"agent"`
	FirstCall *struct {
		ID        string     `json:"id"`
		StartedAt *time.Time `json:"startedAt"`
	} `json:"firstCall"`
}

type sourceRead struct {
	LastAcceptedAt *time.Time `json:"lastAcceptedAt"`
	LastRejectedAt *time.Time `json:"lastRejectedAt"`
	Reason         *string    `json:"reason"`
	UsedKey        *string    `json:"usedKey"`
	ExpectedKey    *string    `json:"expectedKey"`
}

func (s *connectionStack) connection(t *testing.T, org, project string) connectionRead {
	t.Helper()
	rec := httptest.NewRecorder()
	s.api.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/api/v1/org/"+org+"/project/"+project+"/connection", nil))
	if rec.Code != http.StatusOK {
		t.Fatalf("connection: status %d: %s", rec.Code, rec.Body)
	}
	var out connectionRead
	if err := json.Unmarshal(rec.Body.Bytes(), &out); err != nil {
		t.Fatal(err)
	}
	return out
}

func (s *connectionStack) post(path, authorization string, body []byte) int {
	req := httptest.NewRequest(http.MethodPost, path, bytes.NewReader(body))
	if authorization != "" {
		req.Header.Set("Authorization", authorization)
	}
	rec := httptest.NewRecorder()
	s.mux.ServeHTTP(rec, req)
	return rec.Code
}

func webhookToken(t *testing.T, key, secret string, body []byte) string {
	t.Helper()
	sum := sha256.Sum256(body)
	tok, err := auth.NewAccessToken(key, secret).SetSha256(base64.StdEncoding.EncodeToString(sum[:])).ToJWT()
	if err != nil {
		t.Fatal(err)
	}
	return tok
}

func uploadToken(t *testing.T, key, secret string, grant bool) string {
	t.Helper()
	at := auth.NewAccessToken(key, secret)
	if grant {
		at.SetObservabilityGrant(&auth.ObservabilityGrant{Write: true})
	} else {
		at.SetVideoGrant(&auth.VideoGrant{RoomJoin: true, Room: "r"})
	}
	tok, err := at.ToJWT()
	if err != nil {
		t.Fatal(err)
	}
	return "Bearer " + tok
}

// emptyExport is an empty OTLP export request, a valid protobuf body.
var emptyExport = []byte("\x0a\x00")

const hookBody = `{"event":"room_started","id":"event-1","room":{"sid":"RM_1","name":"r"}}`

func reasonOf(s sourceRead) string {
	if s.Reason == nil {
		return ""
	}
	return *s.Reason
}

// TestConnectionReasons: each refusal answers its status code, appends nothing, and the endpoint
// shows its reason; a valid request then shows accepted (docs/specs/first-call-setup.md check 3).
func TestConnectionReasons(t *testing.T) {
	s := newConnectionStack(t, lkProject("acme", "voice", "APIacme", "acme-secret"))
	const hook = "/ingest/acme/voice/livekit/webhook"
	const traces = "/ingest/acme/voice/observability/traces/otlp/v0"
	body := []byte(hookBody)
	// A token issued at synctest's fake epoch, 2000-01-01, expired long ago.
	var expired string
	synctest.Test(t, func(t *testing.T) { expired = uploadToken(t, "APIacme", "acme-secret", true) })
	for _, tc := range []struct {
		name, path, authorization string
		body                      []byte
		refuse                    string
		status                    int
		source, reason, usedKey   string
	}{
		{"webhook without token", hook, "", body, "", 401, store.SourceWebhook, store.ReasonTokenMissing, ""},
		{"webhook from another key", hook, webhookToken(t, "APIother", "acme-secret", body), body, "", 401, store.SourceWebhook, store.ReasonKeyMismatch, "APIother"},
		{"webhook with the wrong secret", hook, webhookToken(t, "APIacme", "wrong", body), body, "", 401, store.SourceWebhook, store.ReasonSignatureInvalid, ""},
		{"webhook body changed", hook, webhookToken(t, "APIacme", "acme-secret", body), append(bytes.Clone(body), ' '), "", 401, store.SourceWebhook, store.ReasonBodyChanged, ""},
		{"webhook too large", hook, webhookToken(t, "APIacme", "acme-secret", body), bytes.Repeat([]byte{' '}, 1<<20+1), "", 413, store.SourceWebhook, store.ReasonTooLarge, ""},
		{"webhook plan inactive", hook, webhookToken(t, "APIacme", "acme-secret", body), body, store.ReasonPlanInactive, 402, store.SourceWebhook, store.ReasonPlanInactive, ""},
		{"upload without token", traces, "", emptyExport, "", 401, store.SourceAgent, store.ReasonTokenMissing, ""},
		{"upload from another key", traces, uploadToken(t, "APIother", "x", true), emptyExport, "", 401, store.SourceAgent, store.ReasonKeyMismatch, "APIother"},
		{"upload expired", traces, expired, emptyExport, "", 401, store.SourceAgent, store.ReasonSignatureInvalid, ""},
		{"upload without the grant", traces, uploadToken(t, "APIacme", "acme-secret", false), emptyExport, "", 403, store.SourceAgent, store.ReasonGrantMissing, ""},
		{"upload too large", traces, uploadToken(t, "APIacme", "acme-secret", true), bytes.Repeat([]byte{0}, 4<<20+1), "", 413, store.SourceAgent, store.ReasonTooLarge, ""},
		{"upload plan at its cap", traces, uploadToken(t, "APIacme", "acme-secret", true), emptyExport, store.ReasonPlanCap, 402, store.SourceAgent, store.ReasonPlanCap, ""},
	} {
		t.Run(tc.name, func(t *testing.T) {
			s.quota.refuse = tc.refuse
			if got := s.post(tc.path, tc.authorization, tc.body); got != tc.status {
				t.Fatalf("status %d, want %d", got, tc.status)
			}
			if len(s.stream.events) != 0 {
				t.Fatalf("appended %d", len(s.stream.events))
			}
			c := s.connection(t, "acme", "voice")
			src := c.Webhook
			if tc.source == store.SourceAgent {
				src = c.Agent
			}
			if reasonOf(src) != tc.reason || src.LastRejectedAt == nil || src.LastAcceptedAt != nil {
				t.Fatalf("%s source: %+v, want reason %s", tc.source, src, tc.reason)
			}
			if tc.usedKey != "" && (src.UsedKey == nil || *src.UsedKey != tc.usedKey || src.ExpectedKey == nil || *src.ExpectedKey != "APIacme") {
				t.Fatalf("keys %v %v", src.UsedKey, src.ExpectedKey)
			}
			if tc.usedKey == "" && (src.UsedKey != nil || src.ExpectedKey != nil) {
				t.Fatal("keys shown for a reason other than key_mismatch")
			}
		})
	}

	s.quota.refuse = ""
	if got := s.post(hook, webhookToken(t, "APIacme", "acme-secret", body), body); got != 200 {
		t.Fatalf("valid webhook: %d", got)
	}
	if got := s.post(traces, uploadToken(t, "APIacme", "acme-secret", true), emptyExport); got != 200 {
		t.Fatalf("valid upload: %d", got)
	}
	c := s.connection(t, "acme", "voice")
	for _, src := range []sourceRead{c.Webhook, c.Agent} {
		if src.LastAcceptedAt == nil || src.LastAcceptedAt.Before(*src.LastRejectedAt) {
			t.Fatalf("not accepted after the valid request: %+v", src)
		}
	}
	if c.URLs.Observability == nil || *c.URLs.Observability != "https://vg.example/ingest/acme/voice" ||
		c.URLs.Webhook == nil || *c.URLs.Webhook != "https://vg.example/ingest/acme/voice/livekit/webhook" {
		t.Fatalf("urls %+v", c.URLs)
	}
}

// TestConnectionOtherTenant: a request signed for project A sent to B's URL is recorded only on B
// (check 4).
func TestConnectionOtherTenant(t *testing.T) {
	s := newConnectionStack(t,
		lkProject("acme", "voice", "APIacme", "acme-secret"),
		lkProject("beta", "voice", "APIbeta", "beta-secret"),
		lkProject("gamma", "voice", "APIacme", "gamma-secret"),
	)
	body := []byte(hookBody)
	tok := webhookToken(t, "APIacme", "acme-secret", body)
	for _, tc := range []struct{ org, reason string }{
		{"beta", store.ReasonKeyMismatch},
		// The same key string on another LiveKit server, with its own secret.
		{"gamma", store.ReasonSignatureInvalid},
	} {
		if got := s.post("/ingest/"+tc.org+"/voice/livekit/webhook", tok, body); got != 401 {
			t.Fatalf("%s: status %d", tc.org, got)
		}
		if got := reasonOf(s.connection(t, tc.org, "voice").Webhook); got != tc.reason {
			t.Fatalf("%s: reason %q, want %s", tc.org, got, tc.reason)
		}
	}
	if a := s.connection(t, "acme", "voice"); a.Webhook != (sourceRead{}) || a.Agent != (sourceRead{}) {
		t.Fatalf("project A changed: %+v", a)
	}
	if len(s.stream.events) != 0 {
		t.Fatalf("appended %d", len(s.stream.events))
	}
	// An unknown project is refused as before and records nothing.
	if got := s.post("/ingest/nobody/voice/livekit/webhook", tok, body); got != 401 {
		t.Fatalf("unknown project: status %d", got)
	}
	var rows int
	if err := s.pool.QueryRow(context.Background(), "SELECT count(*) FROM project_connections WHERE organisation_id = 'nobody'").Scan(&rows); err != nil || rows != 0 {
		t.Fatalf("unknown project recorded %d rows (%v)", rows, err)
	}
}

// TestConnectionRevoked: once the project's LiveKit secret is replaced, or its plan goes inactive,
// the old credentials are refused, the reason is shown and nothing is stored (check 5).
func TestConnectionRevoked(t *testing.T) {
	s := newConnectionStack(t, lkProject("acme", "voice", "APIacme", "old-secret"))
	const traces = "/ingest/acme/voice/observability/traces/otlp/v0"
	old := uploadToken(t, "APIacme", "old-secret", true)
	if got := s.post(traces, old, emptyExport); got != 200 {
		t.Fatalf("before rotation: %d", got)
	}
	s.stream.events = nil

	sealed, err := s.sealer.Seal([]byte("new-secret"), "project:acme/voice")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := s.pool.Exec(context.Background(), "UPDATE projects SET livekit_api_secret_sealed = $1 WHERE organisation_id = 'acme' AND id = 'voice'", sealed); err != nil {
		t.Fatal(err)
	}
	if got := s.post(traces, old, emptyExport); got != 401 {
		t.Fatalf("old secret: %d", got)
	}
	if got := reasonOf(s.connection(t, "acme", "voice").Agent); got != store.ReasonSignatureInvalid {
		t.Fatalf("reason %q", got)
	}

	s.quota.refuse = store.ReasonPlanInactive
	if got := s.post(traces, uploadToken(t, "APIacme", "new-secret", true), emptyExport); got != 402 {
		t.Fatalf("inactive plan: %d", got)
	}
	if got := reasonOf(s.connection(t, "acme", "voice").Agent); got != store.ReasonPlanInactive {
		t.Fatalf("reason %q", got)
	}
	if len(s.stream.events) != 0 {
		t.Fatalf("appended %d", len(s.stream.events))
	}
}

// TestFirstCallIgnoresFixtures: accepted sample data does not make a first call; only a row the
// worker writes to the call index does, and the earliest one is shown (check 7).
func TestFirstCallIgnoresFixtures(t *testing.T) {
	s := newConnectionStack(t, lkProject("acme", "voice", "APIacme", "acme-secret"))
	if got := s.post("/ingest/acme/voice/observability/traces/otlp/v0", uploadToken(t, "APIacme", "acme-secret", true), emptyExport); got != 200 {
		t.Fatalf("sample trace: %d", got)
	}
	if c := s.connection(t, "acme", "voice"); c.FirstCall != nil || c.Agent.LastAcceptedAt == nil {
		t.Fatalf("a sample trace counted as a first call: %+v", c)
	}
	ctx := context.Background()
	later, earlier := time.Date(2026, 10, 21, 10, 0, 0, 0, time.UTC), time.Date(2026, 10, 21, 9, 0, 0, 0, time.UTC)
	if _, err := s.store.UpsertCall(ctx, store.CallUpsert{OrganisationID: "acme", ProjectID: "voice", RoomSID: "RM_late", RoomName: "late", StartedAt: &later}); err != nil {
		t.Fatal(err)
	}
	first, err := s.store.UpsertCall(ctx, store.CallUpsert{OrganisationID: "acme", ProjectID: "voice", RoomSID: "RM_early", RoomName: "early", StartedAt: &earlier})
	if err != nil {
		t.Fatal(err)
	}
	c := s.connection(t, "acme", "voice")
	if c.FirstCall == nil || c.FirstCall.ID != first || c.FirstCall.StartedAt == nil || !c.FirstCall.StartedAt.Equal(earlier) {
		t.Fatalf("first call %+v, want %s", c.FirstCall, first)
	}
}
