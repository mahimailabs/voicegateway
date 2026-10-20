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
	"slices"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/mahimailabs/voicegateway/internal/api"
	"github.com/mahimailabs/voicegateway/internal/app"
	"github.com/mahimailabs/voicegateway/internal/auth"
	"github.com/mahimailabs/voicegateway/internal/config"
	"github.com/mahimailabs/voicegateway/internal/store"

	validator "github.com/pb33f/libopenapi-validator"
)

// quietLayers answers every layer read as freshly read and empty, so the media server, SIP,
// egress, ingress, Cloud agents and workers reads answer 200: the role gate is under test, not the layers.
type quietLayers struct{}

func (quietLayers) LayerStatus(context.Context, string, string, string) (store.LayerStatus, error) {
	now := time.Now()
	return store.LayerStatus{LastReadAt: &now, LastGoodReadAt: &now}, nil
}

func (quietLayers) LayerReads(context.Context, string, string, string, time.Time) ([]store.LayerRead, error) {
	return nil, nil
}

func (quietLayers) RoomsSince(context.Context, string, string, time.Time, int) ([]store.RoomSeen, error) {
	return nil, nil
}

func (quietLayers) Participants(context.Context, string, string, []string) ([]store.ParticipantSeen, error) {
	return nil, nil
}

func (quietLayers) ParticipantChanges(context.Context, string, string, []string) ([]store.ParticipantChange, error) {
	return nil, nil
}

func (quietLayers) LatestSIP(context.Context, string, string) (store.SIPRead, error) {
	return store.SIPRead{ReadAt: time.Now()}, nil
}

func (quietLayers) EgressSince(context.Context, string, string, time.Time, int) ([]store.EgressSeen, error) {
	return nil, nil
}

func (quietLayers) EgressChanges(context.Context, string, string, []string) ([]store.StatusChange, error) {
	return nil, nil
}

func (quietLayers) EgressWebhooks(context.Context, string, string, []string, time.Time) ([]store.WebhookStatus, error) {
	return nil, nil
}

func (quietLayers) IngressSince(context.Context, string, string, time.Time, int) ([]store.IngressSeen, error) {
	return nil, nil
}

func (quietLayers) IngressChanges(context.Context, string, string, []string) ([]store.StatusChange, error) {
	return nil, nil
}

func (quietLayers) IngressWebhooks(context.Context, string, string, []string, time.Time) ([]store.WebhookStatus, error) {
	return nil, nil
}

func (quietLayers) LatestCloudAgents(context.Context, string, string) (store.CloudAgentsRead, error) {
	return store.CloudAgentsRead{ReadAt: time.Now()}, nil
}

func (quietLayers) WorkerSamples(context.Context, string, string, time.Time, int) ([]store.WorkerSample, error) {
	return nil, nil
}

func (quietLayers) WorkerEndpointStatus(context.Context, string, string) (map[string]store.LayerStatus, error) {
	return nil, nil
}

// membersAPI is the API behind the sign-in gate, with every read it serves wired.
func (f *fixture) membersAPI(s *auth.Service) http.Handler {
	return f.membersAPIWith(s, acceptLiveKit{})
}

// membersAPIWith is membersAPI with v proving each new project's LiveKit secret.
func (f *fixture) membersAPIWith(s *auth.Service, v api.LiveKitVerifier) http.Handler {
	return (&api.API{
		Projects: f.pg, Settings: f.pg, NewProjects: f.sealed(), VerifyLiveKit: v, Calls: f.pg, Traces: emptyTraces{}, Layers: quietLayers{}, Endpoints: f.pg, Accounts: f.pg, Keys: f.pg, NewKey: auth.NewKey,
		Log: slog.New(slog.NewJSONHandler(f.logs, nil)), Now: time.Now, Gate: s.Gate,
	}).Routes()
}

// signedIn returns a session cookie for an account made by addUser.
func (f *fixture) signedIn(s *auth.Service, email string) *http.Cookie {
	f.t.Helper()
	res := f.do(s.Routes(), http.MethodPost, "/auth/sign-in", signIn(email, password), request{tls: true})
	c := sessionCookie(res)
	if res.StatusCode != http.StatusNoContent || c == nil {
		f.t.Fatalf("sign-in as %s = %d", email, res.StatusCode)
	}
	return c
}

func (f *fixture) userID(email string) string {
	f.t.Helper()
	var id string
	if err := f.pool.QueryRow(context.Background(), `SELECT id::text FROM users WHERE email = $1`, email).Scan(&id); err != nil {
		f.t.Fatal(err)
	}
	return id
}

// addProjectCall adds project p with one call to an organisation and returns the call's id.
func (f *fixture) addProjectCall(org string) string {
	f.t.Helper()
	ctx := context.Background()
	if _, err := f.pool.Exec(ctx, `INSERT INTO organisations (id, name) VALUES ($1, $1) ON CONFLICT DO NOTHING`, org); err != nil {
		f.t.Fatal(err)
	}
	if _, err := f.pool.Exec(ctx, `INSERT INTO projects (organisation_id, id, name, livekit_url, livekit_api_key, livekit_api_secret_sealed) VALUES ($1, 'p', 'P', 'wss://x', 'API' || $1, '\x00')`, org); err != nil {
		f.t.Fatal(err)
	}
	var id string
	if err := f.pool.QueryRow(ctx, `INSERT INTO calls (organisation_id, project_id, room_sid, room_name, started_at) VALUES ($1, 'p', 'RM_' || $1, 'r', now()) RETURNING id::text`, org).Scan(&id); err != nil {
		f.t.Fatal(err)
	}
	return id
}

// call sends one API request as a session or a project key, checks the response against the
// contract when the operation is one 06b, 06c, 06d, 06e or 06f added, and returns the status and body.
type call struct {
	method, path, body string
	cookie             *http.Cookie
	token              string
	headers            map[string]string
}

func (f *fixture) send(h http.Handler, c call) (int, []byte) {
	f.t.Helper()
	req := httptest.NewRequest(c.method, "http://voicegateway.example"+c.path, strings.NewReader(c.body))
	req.RemoteAddr, req.TLS = "127.0.0.1:40000", &tls.ConnectionState{}
	if c.body != "" {
		req.Header.Set("Content-Type", "application/json")
	}
	if c.cookie != nil {
		req.AddCookie(c.cookie)
	}
	if c.token != "" {
		req.Header.Set("Authorization", "Bearer "+c.token)
	}
	for k, v := range c.headers {
		req.Header.Set(k, v)
	}
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	res := rec.Result()
	body := rec.Body.Bytes()
	f.mu.Lock()
	f.header = res.Header
	f.mu.Unlock()
	// A cross-site request is refused by CrossOriginProtection before the API, in plain text.
	if (strings.Contains(c.path, "/members") || strings.Contains(c.path, "/keys") || strings.Contains(c.path, "/invitations") || strings.HasPrefix(c.path, "/api/v1/instance/") || strings.HasSuffix(c.path, "/projects")) && c.headers["Origin"] == "" {
		res.Body = io.NopCloser(bytes.NewReader(body))
		vreq := httptest.NewRequest(c.method, "http://voicegateway.example"+c.path, strings.NewReader(c.body))
		if c.body != "" {
			vreq.Header.Set("Content-Type", "application/json")
		}
		if ok, errs := contract(f.t).ValidateHttpResponse(vreq, res); !ok {
			for _, e := range errs {
				f.t.Errorf("contract, %s %s: %s: %s", c.method, c.path, e.Message, e.Reason)
			}
		}
	}
	return res.StatusCode, body
}

var (
	contractOnce sync.Once
	contractV    validator.Validator
)

// contract loads docs/api/openapi.yaml once for the package's tests.
func contract(t *testing.T) validator.Validator {
	t.Helper()
	contractOnce.Do(func() { contractV = loadContract(t) })
	return contractV
}

// Acceptance check 1 of phase 06b: every route admits its minimum role and above, and the interim
// token keeps the project reads and nothing organisation-wide.
func TestRolesGateEveryRoute(t *testing.T) {
	f := newFixture(t)
	callID := f.addProjectCall("org-a")
	f.addUser("admin@example.com", "org-a", "admin")
	f.addUser("member@example.com", "org-a", "member")
	f.addUser("viewer@example.com", "org-a", "viewer")
	f.addUser("target@example.com", "org-a", "viewer")
	f.addUser("spare@example.com", "org-a", "admin")
	s := f.service()
	web := f.membersAPI(s)
	target := f.userID("target@example.com")

	reads := []string{
		"/api/v1/workspaces",
		"/api/v1/org/org-a/project/p/calls",
		"/api/v1/org/org-a/project/p/settings",
		"/api/v1/org/org-a/project/p/calls/" + callID,
		"/api/v1/org/org-a/project/p/media-server",
		"/api/v1/org/org-a/project/p/sip",
		"/api/v1/org/org-a/project/p/egress",
		"/api/v1/org/org-a/project/p/ingress",
		"/api/v1/org/org-a/project/p/agents",
		"/api/v1/org/org-a/project/p/workers",
		"/api/v1/org/org-a/members",
	}
	for _, role := range []string{"viewer", "member", "admin"} {
		cookie := f.signedIn(s, role+"@example.com")
		for _, path := range reads {
			if status, body := f.send(web, call{method: http.MethodGet, path: path, cookie: cookie}); status != http.StatusOK {
				t.Errorf("%s GET %s = %d %s, want 200", role, path, status, body)
			}
		}
		writes := []call{
			{method: http.MethodPatch, path: "/api/v1/org/org-a/members/" + target, body: `{"role":"Member"}`},
			{method: http.MethodDelete, path: "/api/v1/org/org-a/members/" + f.userID("spare@example.com")},
		}
		for _, w := range writes {
			w.cookie = cookie
			status, body := f.send(web, w)
			want := http.StatusForbidden
			if role == "admin" {
				want = map[string]int{http.MethodPatch: http.StatusOK, http.MethodDelete: http.StatusNoContent}[w.method]
			}
			if status != want {
				t.Errorf("%s %s %s = %d %s, want %d", role, w.method, w.path, status, body, want)
			}
			if want == http.StatusForbidden && !strings.Contains(string(body), `"forbidden"`) {
				t.Errorf("%s %s: 403 body is not forbidden: %s", role, w.method, body)
			}
		}
	}

	// A project key (phase 06c, which retired the interim token) reads its project, nothing else.
	token := f.projectKey("org-a", "p")
	for _, path := range reads[1:9] {
		if status, _ := f.send(web, call{method: http.MethodGet, path: path, token: token}); status != http.StatusOK {
			t.Errorf("key GET %s = %d, want 200", path, status)
		}
	}
	for _, c := range []call{
		{method: http.MethodGet, path: "/api/v1/org/org-a/members"},
		{method: http.MethodPatch, path: "/api/v1/org/org-a/members/" + target, body: `{"role":"Viewer"}`},
		{method: http.MethodDelete, path: "/api/v1/org/org-a/members/" + target},
	} {
		c.token = token
		if status, _ := f.send(web, c); status != http.StatusForbidden {
			t.Errorf("key %s %s = %d, want 403", c.method, c.path, status)
		}
	}
	if status, body := f.send(web, call{method: http.MethodGet, path: "/api/v1/workspaces", token: token}); status != http.StatusOK || !strings.Contains(string(body), `"id":"p"`) {
		t.Errorf("key GET /api/v1/workspaces = %d %s, want 200 with its one project", status, body)
	}
}

type memberBody struct {
	UserID string `json:"userId"`
	Email  string `json:"email"`
	Role   string `json:"role"`
}

// Acceptance check 3 of phase 06b.
func TestGetAndUpdateMembers(t *testing.T) {
	f := newFixture(t)
	f.addUser("cat@example.com", "org-a", "viewer")
	f.addUser("ada@example.com", "org-a", "admin")
	f.addUser("bob@example.com", "org-a", "member")
	f.addUser("eve@example.com", "org-b", "admin")
	s := f.service()
	web := f.membersAPI(s)
	cookie := f.signedIn(s, "ada@example.com")

	status, body := f.send(web, call{method: http.MethodGet, path: "/api/v1/org/org-a/members", cookie: cookie})
	var got struct{ Items []memberBody }
	if err := json.Unmarshal(body, &got); err != nil || status != http.StatusOK {
		t.Fatalf("getMembers = %d %s", status, body)
	}
	var list []string
	for _, m := range got.Items {
		list = append(list, m.Email+":"+m.Role)
	}
	if want := "[ada@example.com:Admin bob@example.com:Member cat@example.com:Viewer]"; fmt.Sprint(list) != want {
		t.Errorf("members = %v, want %s", list, want)
	}

	cat := f.userID("cat@example.com")
	status, body = f.send(web, call{method: http.MethodPatch, path: "/api/v1/org/org-a/members/" + cat, body: `{"role":"Owner"}`, cookie: cookie})
	if status != http.StatusBadRequest {
		t.Errorf("role Owner = %d %s, want 400", status, body)
	}
	if role := f.role("cat@example.com", "org-a"); role != "viewer" {
		t.Errorf("after a refused change cat is %q, want viewer", role)
	}
	status, body = f.send(web, call{method: http.MethodPatch, path: "/api/v1/org/org-a/members/" + cat, body: `{"role":"Member"}`, cookie: cookie})
	var m memberBody
	if err := json.Unmarshal(body, &m); err != nil || status != http.StatusOK || m.Role != "Member" || m.Email != "cat@example.com" {
		t.Errorf("promote cat = %d %s, want 200 with role Member", status, body)
	}
}

// role returns a user's stored role in an organisation, or "" when they are not a member.
func (f *fixture) role(email, org string) string {
	f.t.Helper()
	var role string
	err := f.pool.QueryRow(context.Background(), `SELECT coalesce((SELECT m.role FROM memberships m JOIN users u ON u.id = m.user_id WHERE u.email = $1 AND m.organisation_id = $2), '')`, email, org).Scan(&role)
	if err != nil {
		f.t.Fatal(err)
	}
	return role
}

// Acceptance check 4 of phase 06b.
func TestLastAdminIsKept(t *testing.T) {
	f := newFixture(t)
	f.addUser("ada@example.com", "org-a", "admin")
	s := f.service()
	web := f.membersAPI(s)
	cookie := f.signedIn(s, "ada@example.com")
	ada := f.userID("ada@example.com")
	for _, c := range []call{
		{method: http.MethodPatch, path: "/api/v1/org/org-a/members/" + ada, body: `{"role":"Viewer"}`},
		{method: http.MethodDelete, path: "/api/v1/org/org-a/members/" + ada},
	} {
		c.cookie = cookie
		status, body := f.send(web, c)
		if status != http.StatusConflict || !strings.Contains(string(body), `"last_admin"`) {
			t.Errorf("%s the only admin = %d %s, want 409 last_admin", c.method, status, body)
		}
	}
	if role := f.role("ada@example.com", "org-a"); role != "admin" {
		t.Errorf("the only admin is now %q", role)
	}

	// Two admins take the admin role from each other at once, 20 times over: by demotion, by
	// removal, or one of each. Exactly one change wins. The other is refused: 409 when both ran under
	// the lock, or 403 when the winner landed before the loser's request was gated, since the loser
	// is then no longer an admin. Either way one admin remains.
	f.addUser("bob@example.com", "org-a", "admin")
	bob := f.userID("bob@example.com")
	bobCookie := f.signedIn(s, "bob@example.com")
	for i := range 20 {
		adaChange := call{method: http.MethodPatch, path: "/api/v1/org/org-a/members/" + bob, body: `{"role":"Viewer"}`, cookie: cookie}
		bobChange := call{method: http.MethodPatch, path: "/api/v1/org/org-a/members/" + ada, body: `{"role":"Viewer"}`, cookie: bobCookie}
		switch i % 3 {
		case 1:
			adaChange = call{method: http.MethodDelete, path: adaChange.path, cookie: cookie}
		case 2:
			adaChange = call{method: http.MethodDelete, path: adaChange.path, cookie: cookie}
			bobChange = call{method: http.MethodDelete, path: bobChange.path, cookie: bobCookie}
		}
		var wg sync.WaitGroup
		statuses := make([]int, 2)
		for k, c := range []call{adaChange, bobChange} {
			wg.Go(func() { statuses[k], _ = f.send(web, c) })
		}
		wg.Wait()
		slices.Sort(statuses)
		won := statuses[0] == http.StatusOK || statuses[0] == http.StatusNoContent
		refused := statuses[1] == http.StatusConflict || statuses[1] == http.StatusForbidden
		if !won || !refused {
			t.Fatalf("round %d: statuses %v, want one success and one 409 or 403", i, statuses)
		}
		var admins int
		if err := f.pool.QueryRow(context.Background(), `SELECT count(*) FROM memberships WHERE organisation_id = 'org-a' AND role = 'admin'`).Scan(&admins); err != nil {
			t.Fatal(err)
		}
		if admins != 1 {
			t.Fatalf("round %d: %d admins after two demotions at once, want 1", i, admins)
		}
		// Put both back as admins for the next round.
		for _, id := range []string{ada, bob} {
			if _, err := f.pool.Exec(context.Background(), `INSERT INTO memberships (user_id, organisation_id, role) VALUES ($1, 'org-a', 'admin') ON CONFLICT (user_id, organisation_id) DO UPDATE SET role = 'admin'`, id); err != nil {
				t.Fatal(err)
			}
		}
	}
}

// Acceptance check 5 of phase 06b.
func TestRoleChangeTakesEffectOnNextRequest(t *testing.T) {
	f := newFixture(t)
	f.addUser("ada@example.com", "org-a", "admin")
	f.addUser("bob@example.com", "org-a", "member")
	f.addUser("cat@example.com", "org-a", "admin")
	f.addUser("dan@example.com", "org-a", "viewer")
	f.addProjectCall("org-b")
	if _, err := f.pg.AddMembership(context.Background(), "dan@example.com", "org-b", "viewer"); err != nil {
		t.Fatal(err)
	}
	s := f.service()
	web := f.membersAPI(s)
	ada, bob, cat, dan := f.signedIn(s, "ada@example.com"), f.signedIn(s, "bob@example.com"), f.signedIn(s, "cat@example.com"), f.signedIn(s, "dan@example.com")
	patch := func(as *http.Cookie, email, role string) {
		t.Helper()
		if status, body := f.send(web, call{method: http.MethodPatch, path: "/api/v1/org/org-a/members/" + f.userID(email), body: `{"role":"` + role + `"}`, cookie: as}); status != http.StatusOK {
			t.Fatalf("set %s to %s = %d %s", email, role, status, body)
		}
	}
	target := "/api/v1/org/org-a/members/" + f.userID("dan@example.com")

	// bob, a Member, is refused a write, promoted, then allowed one with the same cookie.
	if status, _ := f.send(web, call{method: http.MethodPatch, path: target, body: `{"role":"Viewer"}`, cookie: bob}); status != http.StatusForbidden {
		t.Errorf("member write = %d, want 403", status)
	}
	patch(ada, "bob@example.com", "Admin")
	if status, _ := f.send(web, call{method: http.MethodPatch, path: target, body: `{"role":"Viewer"}`, cookie: bob}); status != http.StatusOK {
		t.Errorf("promoted member write = %d, want 200", status)
	}
	// cat, an Admin, is demoted to Viewer and refused the next write with the same cookie.
	patch(ada, "cat@example.com", "Viewer")
	if status, _ := f.send(web, call{method: http.MethodPatch, path: target, body: `{"role":"Viewer"}`, cookie: cat}); status != http.StatusForbidden {
		t.Errorf("demoted admin write = %d, want 403", status)
	}
	// dan is removed from org-a: 403 there on the next request, while org-b still reads.
	if status, body := f.send(web, call{method: http.MethodDelete, path: target, cookie: ada}); status != http.StatusNoContent {
		t.Fatalf("remove dan = %d %s", status, body)
	}
	if status, _ := f.send(web, call{method: http.MethodGet, path: "/api/v1/org/org-a/members", cookie: dan}); status != http.StatusForbidden {
		t.Errorf("removed member reading org-a = %d, want 403", status)
	}
	if status, _ := f.send(web, call{method: http.MethodGet, path: "/api/v1/org/org-b/project/p/calls", cookie: dan}); status != http.StatusOK {
		t.Errorf("removed member reading org-b = %d, want 200", status)
	}
}

// Acceptance check 6 of phase 06b.
func TestMemberIdsStayInTheirOrganisation(t *testing.T) {
	f := newFixture(t)
	f.addUser("ada@example.com", "org-a", "admin")
	f.addUser("eve@example.com", "org-b", "member")
	s := f.service()
	web := f.membersAPI(s)
	cookie := f.signedIn(s, "ada@example.com")
	for _, id := range []string{f.userID("eve@example.com"), "00000000-0000-4000-8000-000000000000", "not-a-uuid"} {
		for _, c := range []call{
			{method: http.MethodPatch, path: "/api/v1/org/org-a/members/" + id, body: `{"role":"Admin"}`},
			{method: http.MethodDelete, path: "/api/v1/org/org-a/members/" + id},
		} {
			c.cookie = cookie
			if status, body := f.send(web, c); status != http.StatusNotFound {
				t.Errorf("%s %s = %d %s, want 404", c.method, id, status, body)
			}
		}
	}
	if role := f.role("eve@example.com", "org-b"); role != "member" {
		t.Errorf("eve in org-b is now %q, want member", role)
	}
	if role := f.role("eve@example.com", "org-a"); role != "" {
		t.Errorf("eve joined org-a as %q", role)
	}
}

// Acceptance check 7a of phase 06b: a password reset signs the user out of every session; a session
// from before 06b, holding no password version, is valid only while the version is 0.
func TestResetPasswordSignsOutEverywhere(t *testing.T) {
	f := newFixture(t)
	f.addUser("ada@example.com", "org-a", "admin")
	s := f.service()
	web := f.membersAPI(s)
	first, second := f.signedIn(s, "ada@example.com"), f.signedIn(s, "ada@example.com")
	members := "/api/v1/org/org-a/members"
	for _, c := range []*http.Cookie{first, second} {
		if status, _ := f.send(web, call{method: http.MethodGet, path: members, cookie: c}); status != http.StatusOK {
			t.Fatalf("before the reset = %d, want 200", status)
		}
	}
	ctx := context.Background()
	cfg := config.Config{PostgresURL: f.url}
	if err := app.ResetPassword(ctx, cfg, "ada@example.com", func() (string, error) { return "too short", nil }); err == nil {
		t.Error("a 9-character password was accepted")
	}
	if status, _ := f.send(web, call{method: http.MethodGet, path: members, cookie: first}); status != http.StatusOK {
		t.Error("a refused reset signed the user out")
	}
	const fresh = "a brand new long password"
	if err := app.ResetPassword(ctx, cfg, "ada@example.com", func() (string, error) { return fresh, nil }); err != nil {
		t.Fatal(err)
	}
	for i, c := range []*http.Cookie{first, second} {
		if status, _ := f.send(web, call{method: http.MethodGet, path: members, cookie: c}); status != http.StatusUnauthorized {
			t.Errorf("session %d after the reset = %d, want 401", i+1, status)
		}
	}
	routes := s.Routes()
	if res := f.do(routes, http.MethodPost, "/auth/sign-in", signIn("ada@example.com", password), request{tls: true}); res.StatusCode != http.StatusUnauthorized {
		t.Errorf("old password = %d, want 401", res.StatusCode)
	}
	if res := f.do(routes, http.MethodPost, "/auth/sign-in", signIn("ada@example.com", fresh), request{tls: true}); res.StatusCode != http.StatusNoContent {
		t.Errorf("new password = %d, want 204", res.StatusCode)
	}

	// A 06a session: the user id alone, no password version. Valid at version 0, signed out after.
	f.addUser("bob@example.com", "org-a", "viewer")
	old := f.legacySession("bob@example.com")
	if status, _ := f.send(web, call{method: http.MethodGet, path: members, cookie: old}); status != http.StatusOK {
		t.Errorf("06a session at version 0 = %d, want 200", status)
	}
	if err := app.ResetPassword(ctx, cfg, "bob@example.com", func() (string, error) { return fresh, nil }); err != nil {
		t.Fatal(err)
	}
	if status, _ := f.send(web, call{method: http.MethodGet, path: members, cookie: old}); status != http.StatusUnauthorized {
		t.Errorf("06a session after a reset = %d, want 401", status)
	}
}

// legacySession stores a session the way 06a wrote it, with the user id and no password version,
// and returns its cookie.
func (f *fixture) legacySession(email string) *http.Cookie {
	f.t.Helper()
	s := f.service()
	id := f.userID(email)
	var cookie *http.Cookie
	h := s.Sessions(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		s.Secure.Put(r.Context(), "user", id)
	}))
	res := f.do(h, http.MethodGet, "/", "", request{tls: true})
	cookie = sessionCookie(res)
	if cookie == nil {
		f.t.Fatal("no legacy session cookie")
	}
	return cookie
}

// Settings writes use actual signed-in sessions and the same role gate as other
// project actions. A member can save; viewer and unrelated organisation cannot.
func TestProjectSettingsSessionWrites(t *testing.T) {
	f := newFixture(t)
	f.addProjectCall("org-a")
	f.addUser("member@example.com", "org-a", "member")
	f.addUser("viewer@example.com", "org-a", "viewer")
	f.addUser("outside@example.com", "org-b", "admin")
	s := f.service()
	web := f.membersAPI(s)
	path := "/api/v1/org/org-a/project/p/settings"
	body := `{"thresholds":{"answerMs":2500,"joinMs":3000,"ttsStartMs":1200},"revision":0}`
	for _, tc := range []struct {
		email string
		want  int
	}{
		{"viewer@example.com", http.StatusForbidden},
		{"outside@example.com", http.StatusForbidden},
		{"member@example.com", http.StatusOK},
		{"member@example.com", http.StatusConflict},
	} {
		cookie := f.signedIn(s, tc.email)
		status, response := f.send(web, call{method: http.MethodPut, path: path, body: body, cookie: cookie})
		if status != tc.want {
			t.Fatalf("settings as %s: %d %s, want %d", tc.email, status, response, tc.want)
		}
	}
	settings, err := f.pg.ProjectSettings(context.Background(), "org-a", "p")
	if err != nil || settings.Revision != 1 || settings.Thresholds.AnswerMs != 2500 {
		t.Fatalf("stored settings %+v %v", settings, err)
	}
}
