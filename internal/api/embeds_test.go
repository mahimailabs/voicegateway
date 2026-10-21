package api

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/alexedwards/scs/v2/memstore"

	"github.com/mahimailabs/voicegateway/internal/auth"
	"github.com/mahimailabs/voicegateway/internal/store"
)

func TestNormaliseOrigin(t *testing.T) {
	for _, tt := range []struct{ in, want string }{
		{"https://app.example.com", "https://app.example.com"},
		{"  HTTPS://App.Example.COM/ ", "https://app.example.com"},
		{"https://app.example.com:443", "https://app.example.com"},
		{"https://app.example.com:8443", "https://app.example.com:8443"},
		{"http://localhost:5173", "http://localhost:5173"},
		{"http://127.0.0.1", "http://127.0.0.1"},
		{"http://[::1]:80", "http://[::1]"},
		{"https://bücher.example", "https://xn--bcher-kva.example"},
		{"http://app.example.com", ""},
		{"https://app.example.com/path", ""},
		{"https://app.example.com?x=1", ""},
		{"https://app.example.com#x", ""},
		{"https://user@app.example.com", ""},
		{"https://*.example.com", ""},
		{"https://example.com.", ""},
		{"app.example.com", ""},
		{"javascript:alert(1)", ""},
		{"*", ""},
		{"'self'", ""},
		{"", ""},
	} {
		got, err := NormaliseOrigin(tt.in)
		if got != tt.want || (err == nil) != (tt.want != "") {
			t.Errorf("NormaliseOrigin(%q) = %q, %v; want %q", tt.in, got, err, tt.want)
		}
	}
}

// Plan task dev-embeds: an approved origin renders the intended project, while expired tokens,
// disallowed origins and other projects are rejected. Real Postgres, the real gate and the real
// frame headers; every answer is checked against the contract.
func TestEmbedTokensFrameOneProjectForOneOrigin(t *testing.T) {
	f := newFixture(t)
	ctx := context.Background()
	f.storeRealCall("api/testdata/full-call.webhooks.jsonl", "api/testdata/full-call.spans.jsonl", "api/testdata/full-call.session_report.json")
	f.summarize()
	callID := f.callID("RM_35ayuuwAkwVj")
	other := f.addProject(t.Name() + "-other")

	now := time.Now()
	tokens, err := auth.NewEmbedTokens(bytes.Repeat([]byte{7}, 32))
	if err != nil {
		t.Fatal(err)
	}
	tokens.Now = func() time.Time { return now }
	keyID, sum, key, err := auth.NewKey()
	if err != nil {
		t.Fatal(err)
	}
	if _, err := f.pg.CreateProjectKey(ctx, "default", f.project, keyID, "embeds", sum, ""); err != nil {
		t.Fatal(err)
	}
	otherKeyID, otherSum, otherKey, _ := auth.NewKey()
	if _, err := f.pg.CreateProjectKey(ctx, "default", other, otherKeyID, "embeds", otherSum, ""); err != nil {
		t.Fatal(err)
	}
	secure, plain := auth.NewSessions(memstore.New())
	accounts := &auth.Service{Users: f.pg, Secure: secure, Plain: plain, Embeds: tokens, Log: f.log, Now: time.Now}
	routes := (&API{
		Projects: f.pg, Settings: f.pg, Origins: f.pg, Embeds: tokens, Metadata: f.pg, Issues: f.pg, Calls: f.pg, Traces: f.events,
		Layers: f.events, Stats: f.pg, Accounts: f.pg, Keys: f.pg, Log: f.log, Now: time.Now, Gate: accounts.Gate,
	}).Routes()
	admin := (&API{Projects: f.pg, Settings: f.pg, Origins: f.pg, Log: f.log, Now: time.Now, Gate: passThrough}).Routes()
	frames := accounts.Frames(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) { _, _ = io.WriteString(w, "<!doctype html>") }))

	base := "/api/v1/org/default/project/" + f.project
	send := func(t *testing.T, h http.Handler, method, path, bearer, body string) (int, []byte) {
		t.Helper()
		req := httptest.NewRequest(method, path, strings.NewReader(body))
		if bearer != "" {
			req.Header.Set("Authorization", "Bearer "+bearer)
		}
		if body != "" {
			req.Header.Set("Content-Type", "application/json")
		}
		rec := httptest.NewRecorder()
		h.ServeHTTP(rec, req)
		f.validateResponse(t, httptest.NewRequest(method, path, strings.NewReader(body)), rec.Code, rec.Result().Header, rec.Body.Bytes())
		return rec.Code, rec.Body.Bytes()
	}
	code := func(body []byte) string {
		var e struct {
			Code string `json:"code"`
		}
		_ = json.Unmarshal(body, &e)
		return e.Code
	}
	mint := func(t *testing.T, bearer, project, body string) (int, string, []byte) {
		t.Helper()
		status, b := send(t, routes, http.MethodPost, "/api/v1/org/default/project/"+project+"/embed-tokens", bearer, body)
		var tok struct {
			Token string `json:"token"`
		}
		_ = json.Unmarshal(b, &tok)
		return status, tok.Token, b
	}
	frameAncestors := func(t *testing.T, path string) string {
		t.Helper()
		rec := httptest.NewRecorder()
		frames.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, path, nil))
		if strings.Contains(path, "/embed/") && rec.Header().Get("Referrer-Policy") != "no-referrer" {
			t.Errorf("%s: Referrer-Policy %q, want no-referrer", path, rec.Header().Get("Referrer-Policy"))
		}
		return strings.TrimPrefix(rec.Header().Get("Content-Security-Policy"), "frame-ancestors ")
	}
	revision := func(t *testing.T) int64 {
		t.Helper()
		s, err := f.pg.ProjectSettings(ctx, "default", f.project)
		if err != nil {
			t.Fatal(err)
		}
		return s.Revision
	}
	setOrigins := func(t *testing.T, origins string) (int, []byte) {
		t.Helper()
		return send(t, admin, http.MethodPut, base+"/settings/allowed-origins", "", `{"allowedOrigins":`+origins+`,"revision":`+jsonInt(revision(t))+`}`)
	}
	app := `"https://app.example.com"`
	calls := `{"widgets":["recent-calls","call"],"origin":"https://App.Example.com:443","expiresInSeconds":60}`

	t.Run("no allowed origin, no token", func(t *testing.T) {
		if status, _, body := mint(t, key, f.project, calls); status != http.StatusBadRequest || code(body) != "origin_not_allowed" {
			t.Fatalf("mint with no allowed origins = %d %s, want 400 origin_not_allowed", status, body)
		}
	})

	t.Run("admins set allowed origins", func(t *testing.T) {
		for _, bad := range []string{`["https://*.example.com"]`, `["https://app.example.com/path"]`, `["http://app.example.com"]`, `"https://app.example.com"`} {
			if status, body := setOrigins(t, bad); status != http.StatusBadRequest {
				t.Errorf("allowedOrigins %s = %d %s, want 400", bad, status, body)
			}
		}
		status, body := setOrigins(t, `["HTTPS://APP.example.com:443", "https://app.example.com", "http://localhost:5173"]`)
		var s projectSettingsRead
		if err := json.Unmarshal(body, &s); status != http.StatusOK || err != nil || s.AllowedOrigins == nil || strings.Join(*s.AllowedOrigins, " ") != "https://app.example.com http://localhost:5173" {
			t.Fatalf("set origins = %d %s", status, body)
		}
		if status, body := send(t, admin, http.MethodPut, base+"/settings/allowed-origins", "", `{"allowedOrigins":[],"revision":0}`); status != http.StatusConflict {
			t.Errorf("stale revision = %d %s, want 409", status, body)
		}
		// A project key never sets them.
		if status, _ := send(t, routes, http.MethodPut, base+"/settings/allowed-origins", key, `{"allowedOrigins":[],"revision":`+jsonInt(revision(t))+`}`); status != http.StatusForbidden {
			t.Errorf("a key set origins: %d", status)
		}
	})

	status, token, _ := mint(t, key, f.project, calls)
	if status != http.StatusCreated || token == "" {
		t.Fatalf("mint = %d", status)
	}

	t.Run("an approved origin renders the intended project", func(t *testing.T) {
		if got := frameAncestors(t, "/org/default/project/"+f.project+"/embed/recent-calls?token="+token+"&theme=dark"); got != "https://app.example.com" {
			t.Errorf("calls frame-ancestors = %q, want the token's origin", got)
		}
		if got := frameAncestors(t, "/org/default/project/"+f.project+"/embed/call?token="+token+"&call="+callID); got != "https://app.example.com" {
			t.Errorf("call frame-ancestors = %q, want the token's origin", got)
		}
		var page struct {
			Items []struct {
				ID string `json:"id"`
			} `json:"items"`
		}
		status, body := send(t, routes, http.MethodGet, base+"/calls", token, "")
		if err := json.Unmarshal(body, &page); status != http.StatusOK || err != nil || len(page.Items) == 0 || page.Items[0].ID != callID {
			t.Fatalf("listCalls with the token = %d %s, want the project's call", status, body)
		}
		if status, body := send(t, routes, http.MethodGet, base+"/calls/"+callID, token, ""); status != http.StatusOK {
			t.Errorf("getCall with the token = %d %s", status, body)
		}
	})

	t.Run("other widgets, routes and projects are refused", func(t *testing.T) {
		for _, path := range []string{
			base + "/issues?from=2026-10-17T00:00:00Z&to=2026-10-18T00:00:00Z",
			base + "/call-stats?from=2026-10-17T00:00:00Z&to=2026-10-18T00:00:00Z",
			base + "/settings", base + "/keys", base + "/calls/" + callID + "/metadata", base + "/media-server",
			"/api/v1/org/default/project/" + other + "/calls", "/api/v1/org/default/project/" + other + "/calls/" + callID,
			"/api/v1/workspaces", "/api/v1/org/default/members",
		} {
			if status, body := send(t, routes, http.MethodGet, path, token, ""); status != http.StatusForbidden {
				t.Errorf("GET %s with the token = %d %s, want 403", path, status, body)
			}
		}
		// A token cannot mint another, so it cannot outlive its expiry.
		if status, _, _ := mint(t, token, f.project, calls); status != http.StatusForbidden {
			t.Errorf("a token minted a token: %d", status)
		}
		for path, want := range map[string]string{
			"/org/default/project/" + f.project + "/embed/issues?token=" + token:   "'self'",
			"/org/default/project/" + other + "/embed/recent-calls?token=" + token: "'self'",
			"/org/default/project/" + f.project + "/embed/recent-calls":            "'self'",
			"/org/default/project/" + f.project + "/calls?token=" + token:          "'self'",
			"/": "'self'",
		} {
			if got := frameAncestors(t, path); got != want {
				t.Errorf("%s: frame-ancestors %q, want %q", path, got, want)
			}
		}
		// Another project's key mints for its own project only, and only for that project's origins.
		if status, _, _ := mint(t, otherKey, f.project, calls); status != http.StatusForbidden {
			t.Errorf("another project's key minted here: %d", status)
		}
		if status, _, body := mint(t, otherKey, other, calls); status != http.StatusBadRequest || code(body) != "origin_not_allowed" {
			t.Errorf("another project minted for an origin it does not allow: %d %s", status, body)
		}
	})

	t.Run("bad requests", func(t *testing.T) {
		for _, body := range []string{
			`{"widgets":[],"origin":"https://app.example.com"}`,
			`{"widgets":["settings"],"origin":"https://app.example.com"}`,
			`{"widgets":["calls"],"origin":"https://*.example.com"}`,
			`{"widgets":["calls"],"origin":"https://app.example.com","expiresInSeconds":59}`,
			`{"widgets":["calls"],"origin":"https://app.example.com","expiresInSeconds":86401}`,
			`{"widgets":["calls"],"origin":"https://app.example.com","project":"other"}`,
		} {
			if status, _, _ := mint(t, key, f.project, body); status != http.StatusBadRequest {
				t.Errorf("mint %s = %d, want 400", body, status)
			}
		}
		if status, _, body := mint(t, key, f.project, `{"widgets":["calls"],"origin":"https://evil.example.com"}`); status != http.StatusBadRequest || code(body) != "origin_not_allowed" {
			t.Errorf("mint for an origin not allowed = %d %s", status, body)
		}
	})

	t.Run("an expired token is refused", func(t *testing.T) {
		now = now.Add(61 * time.Second)
		defer func() { now = now.Add(-61 * time.Second) }()
		if status, _ := send(t, routes, http.MethodGet, base+"/calls", token, ""); status != http.StatusUnauthorized {
			t.Errorf("listCalls with an expired token = %d, want 401", status)
		}
		if got := frameAncestors(t, "/org/default/project/"+f.project+"/embed/recent-calls?token="+token); got != "'self'" {
			t.Errorf("expired token frame-ancestors = %q", got)
		}
	})

	t.Run("removing the origin stops live tokens", func(t *testing.T) {
		if status, body := setOrigins(t, `["http://localhost:5173"]`); status != http.StatusOK {
			t.Fatalf("set origins = %d %s", status, body)
		}
		if status, _ := send(t, routes, http.MethodGet, base+"/calls", token, ""); status != http.StatusUnauthorized {
			t.Errorf("listCalls after the origin was removed = %d, want 401", status)
		}
		if got := frameAncestors(t, "/org/default/project/"+f.project+"/embed/recent-calls?token="+token); got != "'self'" {
			t.Errorf("frame-ancestors after the origin was removed = %q", got)
		}
		if status, body := setOrigins(t, `[`+app+`]`); status != http.StatusOK {
			t.Fatalf("set origins = %d %s", status, body)
		}
		if status, _ := send(t, routes, http.MethodGet, base+"/calls", token, ""); status != http.StatusOK {
			t.Errorf("listCalls after the origin came back = %d", status)
		}
	})

	t.Run("a member's token lasts while they are a member", func(t *testing.T) {
		userID, err := f.pg.AddUser(ctx, "embeds-"+f.project+"@example.com", "x", "default", "viewer")
		if err != nil {
			t.Fatal(err)
		}
		g := store.EmbedScope{OrganisationID: "default", ProjectID: f.project, Widgets: []string{"recent-calls"}, Origin: "https://app.example.com", UserID: userID}
		if ok, err := f.pg.EmbedGrantActive(ctx, g); err != nil || !ok {
			t.Fatalf("a member's grant = %v %v", ok, err)
		}
		if err := f.pg.RemoveMember(ctx, "default", userID); err != nil {
			t.Fatal(err)
		}
		if ok, err := f.pg.EmbedGrantActive(ctx, g); err != nil || ok {
			t.Errorf("a removed member's grant = %v %v", ok, err)
		}
	})

	t.Run("revoking the key stops its tokens", func(t *testing.T) {
		if err := f.pg.RevokeProjectKey(ctx, "default", f.project, keyID); err != nil {
			t.Fatal(err)
		}
		if status, _ := send(t, routes, http.MethodGet, base+"/calls", token, ""); status != http.StatusUnauthorized {
			t.Errorf("listCalls after the key was revoked = %d, want 401", status)
		}
	})
}

func jsonInt(n int64) string {
	b, _ := json.Marshal(n)
	return string(b)
}
