package auth_test

import (
	"bytes"
	"context"
	"crypto/tls"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"os"
	"slices"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/alexedwards/scs/pgxstore"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/pb33f/libopenapi"
	validator "github.com/pb33f/libopenapi-validator"
	"github.com/testcontainers/testcontainers-go"
	tcpostgres "github.com/testcontainers/testcontainers-go/modules/postgres"

	"github.com/mahimailabs/voicegateway/internal/api"
	"github.com/mahimailabs/voicegateway/internal/app"
	"github.com/mahimailabs/voicegateway/internal/auth"
	"github.com/mahimailabs/voicegateway/internal/config"
	"github.com/mahimailabs/voicegateway/internal/store"
	"github.com/mahimailabs/voicegateway/internal/store/pgstore"
	"github.com/mahimailabs/voicegateway/internal/turn"
)

// The same image, tag and digest as deploy/compose.yaml.
const postgresImage = "postgres:16.15-alpine@sha256:721873c34ceb9f8d8fc265984940dc982404c105f19ad51be9fdc5970a6080ea"

const password = "correct horse battery staple"

type fixture struct {
	t    *testing.T
	url  string
	pool *pgxpool.Pool
	pg   *pgstore.Store
	logs *bytes.Buffer
	// header is the last response's headers from send, under mu: sends run in parallel.
	mu     sync.Mutex
	header http.Header
}

func newFixture(t *testing.T) *fixture {
	t.Helper()
	if os.Getenv("CI") == "" {
		if testing.Short() {
			t.Skip("needs Docker; runs in CI")
		}
		testcontainers.SkipIfProviderIsNotHealthy(t)
	}
	ctx := context.Background()
	c, err := tcpostgres.Run(ctx, postgresImage,
		tcpostgres.WithDatabase("voicegateway"), tcpostgres.WithUsername("voicegateway"), tcpostgres.WithPassword("pg-test"),
		tcpostgres.BasicWaitStrategies())
	testcontainers.CleanupContainer(t, c)
	if err != nil {
		t.Fatal(err)
	}
	url, err := c.ConnectionString(ctx, "sslmode=disable")
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
	return &fixture{t: t, url: url, pool: pool, pg: pgstore.NewStore(pool, nil), logs: &bytes.Buffer{}}
}

// service returns a sign-in service over the shared Postgres: each call is another web instance.
func (f *fixture) service(trusted ...string) *auth.Service {
	f.t.Helper()
	prefixes, err := auth.ParseTrusted(strings.Join(trusted, ","))
	if err != nil {
		f.t.Fatal(err)
	}
	secure, plain := auth.NewSessions(pgxstore.New(f.pool))
	return &auth.Service{
		Users: f.pg, Secure: secure, Plain: plain, Trusted: prefixes,
		Log: slog.New(slog.NewJSONHandler(f.logs, nil)), Now: time.Now,
	}
}

// projectKey creates an API key for a project, as createProjectKey does, and returns the full key.
func (f *fixture) projectKey(org, project string) string {
	f.t.Helper()
	keyID, sum, full, err := auth.NewKey()
	if err != nil {
		f.t.Fatal(err)
	}
	if _, err := f.pg.CreateProjectKey(context.Background(), org, project, keyID, "test", sum, ""); err != nil {
		f.t.Fatal(err)
	}
	return full
}

// addUser creates an account with a role in an organisation, creating the organisation if needed.
func (f *fixture) addUser(email, org, role string) {
	f.t.Helper()
	ctx := context.Background()
	if _, err := f.pool.Exec(ctx, `INSERT INTO organisations (id, name) VALUES ($1, $1) ON CONFLICT DO NOTHING`, org); err != nil {
		f.t.Fatal(err)
	}
	hash, err := auth.HashPassword(ctx, password)
	if err != nil {
		f.t.Fatal(err)
	}
	if _, err := f.pg.AddUser(ctx, email, hash, org, role); err != nil {
		f.t.Fatal(err)
	}
}

// request is how a test request reaches web: from an address, over TLS or not, with headers.
type request struct {
	from    string
	host    string
	tls     bool
	headers map[string]string
	cookies []*http.Cookie
}

func (f *fixture) do(h http.Handler, method, path, body string, rq request) *http.Response {
	f.t.Helper()
	req := httptest.NewRequest(method, "http://voicegateway.example"+path, strings.NewReader(body))
	req.RemoteAddr = cmpOr(rq.from, "127.0.0.1") + ":40000"
	if rq.host != "" {
		req.Host = rq.host
	}
	if rq.tls {
		req.TLS = &tls.ConnectionState{}
	}
	if body != "" {
		req.Header.Set("Content-Type", "application/json")
	}
	for k, v := range rq.headers {
		req.Header.Set(k, v)
	}
	for _, c := range rq.cookies {
		req.AddCookie(c)
	}
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	return rec.Result()
}

func cmpOr(a, b string) string {
	if a != "" {
		return a
	}
	return b
}

func signIn(email, pw string) string {
	b, _ := json.Marshal(map[string]string{"email": email, "password": pw})
	return string(b)
}

func code(t *testing.T, res *http.Response) string {
	t.Helper()
	var e struct {
		Code string `json:"code"`
	}
	b, _ := io.ReadAll(res.Body)
	_ = json.Unmarshal(b, &e)
	return e.Code
}

func sessionCookie(res *http.Response) *http.Cookie {
	for _, c := range res.Cookies() {
		if c.Name == "vg_session" {
			return c
		}
	}
	return nil
}

// Acceptance check 2.
func TestSignInSessionSignOut(t *testing.T) {
	f := newFixture(t)
	f.addUser("ada@example.com", "default", "admin")
	s := f.service()
	routes := s.Routes()

	res := f.do(routes, http.MethodPost, "/auth/sign-in", signIn("Ada@Example.com", password), request{from: "10.0.0.5", tls: true})
	c := sessionCookie(res)
	if res.StatusCode != http.StatusNoContent || c == nil || !c.HttpOnly || !c.Secure || c.SameSite != http.SameSiteLaxMode {
		t.Fatalf("sign-in = %d, cookie %+v; want 204 with an HttpOnly, Secure, SameSite=Lax cookie", res.StatusCode, c)
	}
	res = f.do(routes, http.MethodGet, "/auth/session", "", request{from: "10.0.0.5", tls: true, cookies: []*http.Cookie{c}})
	var session struct {
		Email         string `json:"email"`
		Organisations []struct{ ID, Role string }
	}
	if err := json.NewDecoder(res.Body).Decode(&session); err != nil || res.StatusCode != http.StatusOK {
		t.Fatalf("session = %d, %v", res.StatusCode, err)
	}
	if session.Email != "ada@example.com" || len(session.Organisations) != 1 || session.Organisations[0].ID != "default" || session.Organisations[0].Role != "admin" {
		t.Errorf("session = %+v", session)
	}
	if res := f.do(routes, http.MethodPost, "/auth/sign-out", "", request{from: "10.0.0.5", tls: true, cookies: []*http.Cookie{c}}); res.StatusCode != http.StatusNoContent {
		t.Fatalf("sign-out = %d", res.StatusCode)
	}
	if res := f.do(routes, http.MethodGet, "/auth/session", "", request{from: "10.0.0.5", tls: true, cookies: []*http.Cookie{c}}); res.StatusCode != http.StatusUnauthorized {
		t.Errorf("session after sign-out = %d, want 401", res.StatusCode)
	}
}

// Acceptance check 3.
func TestSignInFailuresLookAlike(t *testing.T) {
	f := newFixture(t)
	f.addUser("ada@example.com", "default", "admin")
	routes := f.service().Routes()
	// Interleaved, so both kinds of failure share the same conditions: the race detector makes
	// each hash slow, and drift between two separate runs would land on one side only.
	try := func(email string) (time.Duration, string) {
		// The limit is not under test here.
		if _, err := f.pool.Exec(context.Background(), `DELETE FROM sign_in_attempts`); err != nil {
			t.Fatal(err)
		}
		start := time.Now()
		res := f.do(routes, http.MethodPost, "/auth/sign-in", signIn(email, "wrong password, wrong"), request{tls: true})
		took := time.Since(start)
		b, _ := io.ReadAll(res.Body)
		if res.StatusCode != http.StatusUnauthorized {
			t.Fatalf("%s: status %d, want 401", email, res.StatusCode)
		}
		return took, string(b)
	}
	try("nobody@example.com") // the dummy hash is made on first use; keep that out of the medians
	var wrongTimes, unknownTimes []time.Duration
	var wrongBody, unknownBody string
	for range 20 {
		var d time.Duration
		d, wrongBody = try("ada@example.com")
		wrongTimes = append(wrongTimes, d)
		d, unknownBody = try("nobody@example.com")
		unknownTimes = append(unknownTimes, d)
	}
	slices.Sort(wrongTimes)
	slices.Sort(unknownTimes)
	wrong, unknown := wrongTimes[10], unknownTimes[10]
	t.Logf("median wrong password %v, unknown email %v", wrong, unknown)
	if wrongBody != unknownBody {
		t.Errorf("bodies differ: %q and %q", wrongBody, unknownBody)
	}
	if d := float64(wrong-unknown) / float64(wrong); d > 0.25 || d < -0.25 {
		t.Errorf("median times %v (wrong password) and %v (unknown email) differ by more than 25%%", wrong, unknown)
	}
}

// Acceptance check 4.
func TestSignInLimitIsShared(t *testing.T) {
	f := newFixture(t)
	f.addUser("ada@example.com", "default", "admin")
	web := []http.Handler{f.service().Routes(), f.service().Routes()}
	for i := range 10 {
		if res := f.do(web[i%2], http.MethodPost, "/auth/sign-in", signIn("ada@example.com", "wrong"), request{tls: true}); res.StatusCode != http.StatusUnauthorized {
			t.Fatalf("failure %d = %d, want 401", i+1, res.StatusCode)
		}
	}
	res := f.do(web[0], http.MethodPost, "/auth/sign-in", signIn("ada@example.com", password), request{tls: true})
	if res.StatusCode != http.StatusTooManyRequests || res.Header.Get("Retry-After") == "" {
		t.Errorf("11th sign-in = %d, Retry-After %q; want 429 with a Retry-After", res.StatusCode, res.Header.Get("Retry-After"))
	}
}

// Acceptance check 5.
func TestSessionCookieFollowsHTTPS(t *testing.T) {
	f := newFixture(t)
	f.addUser("ada@example.com", "default", "admin")
	routes := f.service("10.0.0.1").Routes()
	tests := []struct {
		name       string
		rq         request
		wantStatus int
		wantSecure bool
	}{
		{"plain HTTP by IP", request{from: "10.0.0.5"}, http.StatusBadRequest, false},
		{"plain HTTP to localhost", request{from: "127.0.0.1", host: "localhost:8080"}, http.StatusNoContent, false},
		{"plain HTTP to localhost through Docker's port forwarding", request{from: "172.17.0.1", host: "localhost:5180"}, http.StatusNoContent, false},
		{"trusted proxy over HTTPS", request{from: "10.0.0.1", headers: map[string]string{"X-Forwarded-Proto": "https", "X-Forwarded-For": "203.0.113.7"}}, http.StatusNoContent, true},
		{"untrusted peer claiming HTTPS", request{from: "10.0.0.9", headers: map[string]string{"X-Forwarded-Proto": "https"}}, http.StatusBadRequest, false},
		{"proxy passing plain HTTP on to localhost", request{from: "127.0.0.1", host: "localhost:8080", headers: map[string]string{"X-Forwarded-For": "203.0.113.9"}}, http.StatusBadRequest, false},
		{"trusted proxy saying plain HTTP, to localhost", request{from: "10.0.0.1", host: "localhost:8080", headers: map[string]string{"X-Forwarded-Proto": "http", "X-Forwarded-For": "203.0.113.9"}}, http.StatusBadRequest, false},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			res := f.do(routes, http.MethodPost, "/auth/sign-in", signIn("ada@example.com", password), tt.rq)
			if res.StatusCode != tt.wantStatus {
				t.Fatalf("status %d, want %d", res.StatusCode, tt.wantStatus)
			}
			c := sessionCookie(res)
			if tt.wantStatus == http.StatusBadRequest {
				if got := code(t, res); got != "https_required" || c != nil {
					t.Errorf("code %q, cookie %v; want https_required and no cookie", got, c)
				}
				return
			}
			if c == nil || c.Secure != tt.wantSecure {
				t.Errorf("cookie %+v, want Secure=%v", c, tt.wantSecure)
			}
		})
	}
}

// Acceptance check 5a.
func TestClientAddressBehindProxy(t *testing.T) {
	f := newFixture(t)
	routes := f.service("10.0.0.1").Routes()
	via := func(client string) request {
		return request{from: "10.0.0.1", headers: map[string]string{"X-Forwarded-Proto": "https", "X-Forwarded-For": client}}
	}
	// 50 failures from one client, each for a different email so no per-email limit applies.
	for i := range 50 {
		if res := f.do(routes, http.MethodPost, "/auth/sign-in", signIn(fmt.Sprintf("u%d@example.com", i), "wrong"), via("203.0.113.7")); res.StatusCode != http.StatusUnauthorized {
			t.Fatalf("failure %d = %d", i+1, res.StatusCode)
		}
	}
	if res := f.do(routes, http.MethodPost, "/auth/sign-in", signIn("next@example.com", "wrong"), via("203.0.113.7")); res.StatusCode != http.StatusTooManyRequests {
		t.Errorf("51st from 203.0.113.7 = %d, want 429", res.StatusCode)
	}
	if res := f.do(routes, http.MethodPost, "/auth/sign-in", signIn("other@example.com", "wrong"), via("203.0.113.8")); res.StatusCode != http.StatusUnauthorized {
		t.Errorf("203.0.113.8 = %d, want 401: it is not locked out by its neighbour", res.StatusCode)
	}
	// An untrusted peer's forwarded header is ignored: the failure counts under the peer.
	spoof := request{from: "198.51.100.9", tls: true, headers: map[string]string{"X-Forwarded-For": "203.0.113.8"}}
	f.do(routes, http.MethodPost, "/auth/sign-in", signIn("spoof@example.com", "wrong"), spoof)
	since := time.Now().Add(-time.Hour)
	peer, _, err := f.pg.FailedSignIns(context.Background(), "address", "198.51.100.9", since)
	if err != nil {
		t.Fatal(err)
	}
	claimed, _, err := f.pg.FailedSignIns(context.Background(), "address", "203.0.113.8", since)
	if err != nil {
		t.Fatal(err)
	}
	if peer != 1 || claimed != 1 {
		t.Errorf("failures under the peer %d and the claimed address %d; want 1 and 1 (only its own)", peer, claimed)
	}
}

// Acceptance check 6.
func TestCrossOriginSignInRefused(t *testing.T) {
	f := newFixture(t)
	f.addUser("ada@example.com", "default", "admin")
	web := http.NewCrossOriginProtection().Handler(f.service().Routes())
	res := f.do(web, http.MethodPost, "/auth/sign-in", signIn("ada@example.com", password),
		request{tls: true, headers: map[string]string{"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"}})
	if res.StatusCode != http.StatusForbidden || sessionCookie(res) != nil {
		t.Errorf("cross-origin sign-in = %d, cookie %v; want 403 and no cookie", res.StatusCode, sessionCookie(res))
	}
}

// emptyTraces stands in for ClickHouse: the gate is under test, not the call's contents.
type emptyTraces struct{}

func (emptyTraces) CallTrace(context.Context, string, string, string) ([]store.Span, turn.Result, error) {
	return nil, turn.Result{}, nil
}

// apiWith returns the API behind the sign-in gate.
func (f *fixture) apiWith(s *auth.Service) http.Handler {
	return (&api.API{Projects: f.pg, Settings: f.pg, Metadata: f.pg, Calls: f.pg, Traces: emptyTraces{}, Accounts: f.pg, Log: slog.New(slog.NewTextHandler(io.Discard, nil)), Now: time.Now, Gate: s.Gate}).Routes()
}

// Acceptance check 7.
func TestGateAcceptsSessionScopedToMembership(t *testing.T) {
	f := newFixture(t)
	f.addUser("ada@example.com", "org-a", "viewer")
	ctx := context.Background()
	calls := map[string]string{}
	for _, org := range []string{"org-a", "org-b"} {
		if _, err := f.pool.Exec(ctx, `INSERT INTO organisations (id, name) VALUES ($1, $1) ON CONFLICT DO NOTHING`, org); err != nil {
			t.Fatal(err)
		}
		if _, err := f.pool.Exec(ctx, `INSERT INTO projects (organisation_id, id, name, livekit_url, livekit_api_key, livekit_api_secret_sealed) VALUES ($1, 'p', 'P', 'wss://x', 'API' || $1, '\x00')`, org); err != nil {
			t.Fatal(err)
		}
		var id string
		if err := f.pool.QueryRow(ctx, `INSERT INTO calls (organisation_id, project_id, room_sid, room_name, started_at) VALUES ($1, 'p', 'RM_' || $1, 'r', now()) RETURNING id::text`, org).Scan(&id); err != nil {
			t.Fatal(err)
		}
		calls[org] = id
	}
	s := f.service()
	cookie := sessionCookie(f.do(s.Routes(), http.MethodPost, "/auth/sign-in", signIn("ada@example.com", password), request{tls: true}))
	web := f.apiWith(s)
	path := func(org string) string { return "/api/v1/org/" + org + "/project/p/calls/" + calls[org] }
	for _, tt := range []struct {
		name string
		path string
		rq   request
		want int
	}{
		{"member", path("org-a"), request{tls: true, cookies: []*http.Cookie{cookie}}, http.StatusOK},
		{"not a member", path("org-b"), request{tls: true, cookies: []*http.Cookie{cookie}}, http.StatusForbidden},
		{"no credential", path("org-a"), request{tls: true}, http.StatusUnauthorized},
		{"retired interim token", path("org-a"), request{tls: true, headers: map[string]string{"Authorization": "Bearer 0123456789abcdef0123456789abcdef"}}, http.StatusUnauthorized},
	} {
		t.Run(tt.name, func(t *testing.T) {
			res := f.do(web, http.MethodGet, tt.path, "", tt.rq)
			if res.StatusCode != tt.want {
				t.Errorf("status %d, want %d", res.StatusCode, tt.want)
			}
			if tt.want == http.StatusForbidden && code(t, res) != "forbidden" {
				t.Error("403 body code is not forbidden")
			}
		})
	}
}

// Acceptance check 8.
func TestGetWorkspacesFromStorage(t *testing.T) {
	f := newFixture(t)
	f.addUser("ada@example.com", "org-a", "member")
	f.addUser("eve@example.com", "org-b", "member")
	ctx := context.Background()
	for _, p := range []string{"p1", "p2"} {
		if _, err := f.pool.Exec(ctx, `INSERT INTO projects (organisation_id, id, name, livekit_url, livekit_api_key, livekit_api_secret_sealed) VALUES ('org-a', $1, $1, 'wss://x', 'API' || $1, '\x00')`, p); err != nil {
			t.Fatal(err)
		}
	}
	if _, err := f.pool.Exec(ctx, `INSERT INTO projects (organisation_id, id, name, livekit_url, livekit_api_key, livekit_api_secret_sealed) VALUES ('org-b', 'secret', 'Secret', 'wss://x', 'APIsecret', '\x00')`); err != nil {
		t.Fatal(err)
	}
	// A user with no membership: the organisation was removed from them after sign-up.
	f.addUser("lone@example.com", "org-c", "member")
	if _, err := f.pool.Exec(ctx, `DELETE FROM memberships WHERE organisation_id = 'org-c'`); err != nil {
		t.Fatal(err)
	}
	s := f.service()
	web := f.apiWith(s)
	contract := loadContract(t)
	read := func(email string, rq request) (int, map[string]any) {
		if email != "" {
			rq.cookies = []*http.Cookie{sessionCookie(f.do(s.Routes(), http.MethodPost, "/auth/sign-in", signIn(email, password), request{tls: true}))}
		}
		req := httptest.NewRequest(http.MethodGet, "http://voicegateway.example/api/v1/workspaces", nil)
		req.RemoteAddr, req.TLS = "127.0.0.1:40000", &tls.ConnectionState{}
		for _, c := range rq.cookies {
			req.AddCookie(c)
		}
		for k, v := range rq.headers {
			req.Header.Set(k, v)
		}
		rec := httptest.NewRecorder()
		web.ServeHTTP(rec, req)
		res := rec.Result()
		body := rec.Body.Bytes()
		res.Body = io.NopCloser(bytes.NewReader(body))
		if ok, errs := contract.ValidateHttpResponse(req, res); !ok {
			for _, e := range errs {
				t.Errorf("contract: %s: %s", e.Message, e.Reason)
			}
		}
		var out map[string]any
		_ = json.Unmarshal(body, &out)
		return res.StatusCode, out
	}
	status, body := read("ada@example.com", request{})
	if status != http.StatusOK || fmt.Sprint(body["items"]) != "[map[id:org-a name:org-a projects:[map[id:p1 name:p1] map[id:p2 name:p2]]]]" {
		t.Errorf("ada = %d %v; want org-a with p1 and p2 only", status, body)
	}
	if status, body := read("lone@example.com", request{}); status != http.StatusOK || fmt.Sprint(body["items"]) != "[]" {
		t.Errorf("no membership = %d %v; want an empty list", status, body)
	}
	if status, _ := read("", request{headers: map[string]string{"Authorization": "Bearer 0123456789abcdef0123456789abcdef"}}); status != http.StatusUnauthorized {
		t.Errorf("token only = %d, want 401", status)
	}
}

func loadContract(t *testing.T) validator.Validator {
	t.Helper()
	spec, err := os.ReadFile("../../docs/api/openapi.yaml")
	if err != nil {
		t.Fatal(err)
	}
	doc, err := libopenapi.NewDocument(spec)
	if err != nil {
		t.Fatal(err)
	}
	v, errs := validator.NewValidator(doc)
	if len(errs) > 0 {
		t.Fatal(errs)
	}
	return v
}

// failingAccounts is the account store failing on member reads, to reach the API's error path.
type failingAccounts struct{ api.Accounts }

func (failingAccounts) Members(context.Context, string) ([]store.Member, error) {
	return nil, fmt.Errorf("members: dial tcp 127.0.0.1:5432: connection refused")
}

// failingProjects is the project store failing on create, to reach createProject's error path.
type failingProjects struct{}

func (failingProjects) CreateProject(context.Context, store.NewProject) (store.Project, error) {
	return store.Project{}, fmt.Errorf("create project: the project store is down")
}

// failingUsers is the account store failing on lookups, to reach sign-in's error path.
type failingUsers struct{ auth.Users }

func (failingUsers) UserByEmail(context.Context, string) (store.User, error) {
	return store.User{}, fmt.Errorf("dial tcp 127.0.0.1:5432: connection refused")
}

// Acceptance check 10, over the flows above: no log line holds a password, a session id or the
// token.
func TestNoSecretInLogs(t *testing.T) {
	f := newFixture(t)
	f.addUser("ada@example.com", "default", "admin")
	s := f.service()
	routes := s.Routes()
	res := f.do(routes, http.MethodPost, "/auth/sign-in", signIn("ada@example.com", password), request{tls: true})
	c := sessionCookie(res)
	f.do(routes, http.MethodPost, "/auth/sign-in", signIn("ada@example.com", "a wrong one"), request{tls: true})
	if _, err := f.pool.Exec(context.Background(), `INSERT INTO projects (organisation_id, id, name, livekit_url, livekit_api_key, livekit_api_secret_sealed) VALUES ('default', 'logs', 'Logs', 'wss://x', 'APIlogs', '\x00') ON CONFLICT DO NOTHING`); err != nil {
		t.Fatal(err)
	}
	key := f.projectKey("default", "logs")
	f.do(f.apiWith(s), http.MethodGet, "/api/v1/workspaces", "", request{tls: true, headers: map[string]string{"Authorization": "Bearer " + key}})
	// An error path, which logs: the account store fails during a sign-in.
	broken := &auth.Service{Users: failingUsers{Users: f.pg}, Secure: s.Secure, Plain: s.Plain, Log: s.Log, Now: time.Now}
	f.do(broken.Routes(), http.MethodPost, "/auth/sign-in", signIn("ada@example.com", password), request{tls: true})
	// Phase 06b's flows: members read and changed, a password reset, and a failing member store.
	f.addUser("bob@example.com", "default", "viewer")
	web := f.membersAPI(s)
	members := "/api/v1/org/default/members"
	f.send(web, call{method: http.MethodGet, path: members, cookie: c})
	f.send(web, call{method: http.MethodPatch, path: members + "/" + f.userID("bob@example.com"), body: `{"role":"Member"}`, cookie: c})
	const reset = "a reset password, long enough"
	if err := app.ResetPassword(context.Background(), config.Config{PostgresURL: f.url}, "bob@example.com", func() (string, error) { return reset, nil }); err != nil {
		t.Fatal(err)
	}
	bob := sessionCookie(f.do(routes, http.MethodPost, "/auth/sign-in", signIn("bob@example.com", reset), request{tls: true}))
	// Phase 06c: a key created through the API, used, and refused with a wrong secret.
	created := f.createKey(web, c, "default", "logs").Secret
	f.send(web, call{method: http.MethodGet, path: "/api/v1/org/default/project/logs/calls", token: created})
	f.send(web, call{method: http.MethodGet, path: "/api/v1/org/default/project/logs/calls", token: created[:len(created)-2] + "xx"})
	failing := (&api.API{Projects: f.pg, Settings: f.pg, Metadata: f.pg, Calls: f.pg, Traces: emptyTraces{}, Accounts: failingAccounts{Accounts: f.pg}, Log: s.Log, Now: time.Now, Gate: s.Gate}).Routes()
	f.send(failing, call{method: http.MethodGet, path: members, cookie: c})
	// Phase 06d: links made, read, refused and used without SMTP, a reset, and a failed send whose
	// SMTP URL holds a password.
	iw := f.web(nil)
	invited := iw.link(c, "default", "invitee@example.com", "Member")
	iw.look(tokenOf(invited))
	iw.accept(tokenOf(invited), "too short", request{tls: true})
	joined := sessionCookie(func() *http.Response { r, _ := iw.accept(tokenOf(invited), newPassword, request{tls: true}); return r }())
	iw.accept(tokenOf(invited), newPassword, request{tls: true})
	existing := iw.link(c, "default", "elsewhere@example.com", "Viewer")
	f.addUser("elsewhere@example.com", "other-org", "viewer")
	iw.accept(tokenOf(existing), "a wrong one", request{tls: true})
	_, resetLink, _, _ := iw.resetLink(c, "default", f.userID("invitee@example.com"))
	const fromReset = "set through a reset link"
	iw.accept(tokenOf(*resetLink), fromReset, request{tls: true})
	down := f.web(unreachableMailer(t))
	down.invite(c, "default", "unsent@example.com", "Viewer")
	down.resetLink(c, "default", f.userID("invitee@example.com"))
	// Phase 06f: a project created through the API, created again (409), and a create whose store
	// fails, which logs.
	newProject := call{method: http.MethodPost, path: "/api/v1/org/default/projects", body: projectBody("Logs project", "wss://logs.example.com", "APIlogsproject", testLiveKitSecret), cookie: c}
	f.send(web, newProject)
	f.send(web, newProject)
	storeDown := (&api.API{Projects: f.pg, NewProjects: failingProjects{}, VerifyLiveKit: acceptLiveKit{}, Accounts: f.pg, Log: s.Log, Now: time.Now, Gate: s.Gate}).Routes()
	f.send(storeDown, newProject)
	if !strings.Contains(f.logs.String(), "the project store is down") {
		t.Error("the failing project store did not log")
	}
	if !strings.Contains(f.logs.String(), `"class":"unreachable"`) {
		t.Error("the failed send did not log")
	}
	// Phase 06e: the instance API off and on, refused, an invitation link made, repeated and used,
	// and a failed send.
	instance := instanceSecrets(t, f)
	logs := f.logs.String()
	if logs == "" {
		t.Fatal("no log lines to check: the error path did not log")
	}
	if !strings.Contains(logs, "members") {
		t.Error("the failing member store did not log")
	}
	for _, secret := range append([]string{
		password, "a wrong one", c.Value, key, key[len(key)-43:], created, created[len(created)-43:], reset, bob.Value,
		invited, tokenOf(invited), existing, tokenOf(existing), *resetLink, tokenOf(*resetLink), newPassword, fromReset, joined.Value, smtpPassword,
		testLiveKitSecret,
	}, instance...) {
		if strings.Contains(logs, secret) {
			t.Errorf("a log line holds a secret %q", secret)
		}
	}
}

// Security review of 06a: a burst of parallel sign-ins cannot pass the limit together; the attempt
// is reserved before the password is checked, so at most 10 are checked for one email.
func TestParallelSignInsCannotPassTheLimit(t *testing.T) {
	f := newFixture(t)
	f.addUser("ada@example.com", "default", "admin")
	routes := f.service().Routes()
	statuses := make(chan int, 30)
	var wg sync.WaitGroup
	for i := range 30 {
		wg.Add(1)
		go func() {
			defer wg.Done()
			res := f.do(routes, http.MethodPost, "/auth/sign-in", signIn("ada@example.com", fmt.Sprintf("guess %d", i)), request{tls: true, from: fmt.Sprintf("203.0.113.%d", i+1)})
			statuses <- res.StatusCode
		}()
	}
	wg.Wait()
	close(statuses)
	counts := map[int]int{}
	for s := range statuses {
		counts[s]++
	}
	if counts[http.StatusUnauthorized] != 10 || counts[http.StatusTooManyRequests] != 20 {
		t.Errorf("statuses = %v, want 10 checked (401) and 20 refused (429)", counts)
	}
}

// Security review of 06a: an https public URL makes the cookie Secure, but a password sent over
// plain HTTP to a non-local address is still refused.
func TestPublicHTTPSDoesNotLiftHTTPSRequired(t *testing.T) {
	f := newFixture(t)
	f.addUser("ada@example.com", "default", "admin")
	s := f.service()
	s.PublicHTTPS = true
	res := f.do(s.Routes(), http.MethodPost, "/auth/sign-in", signIn("ada@example.com", password), request{from: "10.0.0.5", host: "10.0.0.5:8080"})
	if res.StatusCode != http.StatusBadRequest || code(t, res) != "https_required" {
		t.Errorf("plain HTTP by IP with an https public URL = %d, want 400 https_required", res.StatusCode)
	}
}

// Security review of 06a: IPv6 clients are counted by their /64, so rotating within it does not
// buy more attempts; successful sign-ins do not use up the limit.
func TestAddressLimitsByIPv6PrefixAndSuccessesAreFree(t *testing.T) {
	f := newFixture(t)
	f.addUser("ada@example.com", "default", "admin")
	routes := f.service().Routes()
	for i := range 50 {
		from := fmt.Sprintf("2001:db8:1:2::%x", i+1)
		if res := f.do(routes, http.MethodPost, "/auth/sign-in", signIn(fmt.Sprintf("u%d@example.com", i), "wrong"), request{tls: true, from: from}); res.StatusCode != http.StatusUnauthorized {
			t.Fatalf("failure %d = %d", i+1, res.StatusCode)
		}
	}
	if res := f.do(routes, http.MethodPost, "/auth/sign-in", signIn("next@example.com", "wrong"), request{tls: true, from: "2001:db8:1:2::ffff"}); res.StatusCode != http.StatusTooManyRequests {
		t.Errorf("another address in the same /64 = %d, want 429", res.StatusCode)
	}
	for i := range 12 {
		if res := f.do(routes, http.MethodPost, "/auth/sign-in", signIn("ada@example.com", password), request{tls: true, from: "198.51.100.20"}); res.StatusCode != http.StatusNoContent {
			t.Fatalf("successful sign-in %d = %d, want 204 every time", i+1, res.StatusCode)
		}
	}
}

// Pair review of #378: a proxy web does not trust is warned about once whatever its address, also
// under Docker, where a proxy on the host arrives from the bridge gateway.
func TestUntrustedProxyIsWarnedAboutOnce(t *testing.T) {
	f := newFixture(t)
	routes := f.service().Routes()
	// An untrusted proxy terminating TLS forwards plain HTTP: refused, and warned about.
	rq := request{from: "172.18.0.1", headers: map[string]string{"X-Forwarded-For": "203.0.113.9", "X-Forwarded-Proto": "https"}}
	for range 2 {
		res := f.do(routes, http.MethodPost, "/auth/sign-in", signIn("ada@example.com", "wrong"), rq)
		b, _ := io.ReadAll(res.Body)
		if res.StatusCode != http.StatusBadRequest || !strings.Contains(string(b), "https_required") || !strings.Contains(string(b), "VOICEGATEWAY_TRUSTED_PROXIES") {
			t.Fatalf("status %d, body %s; want 400 https_required naming VOICEGATEWAY_TRUSTED_PROXIES", res.StatusCode, b)
		}
	}
	if n := strings.Count(f.logs.String(), "set VOICEGATEWAY_TRUSTED_PROXIES"); n != 1 {
		t.Errorf("warnings = %d, want 1", n)
	}
}
