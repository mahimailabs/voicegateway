package auth_test

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"strings"
	"testing"

	"github.com/mahimailabs/voicegateway/internal/seal"
	"github.com/mahimailabs/voicegateway/internal/store/pgstore"
)

// testSealKey is a fixed test key, not a deployment's VOICEGATEWAY_SECRET_KEY.
var testSealKey = bytes.Repeat([]byte{7}, seal.KeySize)

func testSealer(t *testing.T) *seal.Sealer {
	t.Helper()
	s, err := seal.New(testSealKey)
	if err != nil {
		t.Fatal(err)
	}
	return s
}

// sealed is the fixture's store with a sealer, as web wires it for createProject.
func (f *fixture) sealed() *pgstore.Store {
	return pgstore.NewStore(f.pool, testSealer(f.t))
}

// A LiveKit secret made up for these tests; it reaches no LiveKit server.
const testLiveKitSecret = "test-livekit-secret-not-real-0123456789"

func projectBody(name, url, key, secret string) string {
	b, _ := json.Marshal(map[string]string{"name": name, "livekitUrl": url, "livekitApiKey": key, "livekitApiSecret": secret})
	return string(b)
}

// Acceptance checks 1 and 2 of phase 06f: an Admin creates a project; the answer is the
// WorkspaceProject with no secret and no-store; the stored secret is sealed and opens to the original
// with the sealer; the project then shows in getWorkspaces and its secret through LiveKitSecret.
func TestCreateProject(t *testing.T) {
	f := newFixture(t)
	f.addUser("ada@example.com", "org-a", "admin")
	s := f.service()
	web := f.membersAPI(s)
	admin := f.signedIn(s, "ada@example.com")

	res := f.do(web, http.MethodPost, "/api/v1/org/org-a/projects", projectBody("  EU Production ", "wss://eu.lk.example.com", "APIeuprod", testLiveKitSecret), request{tls: true, cookies: []*http.Cookie{admin}})
	if res.StatusCode != http.StatusCreated {
		t.Fatalf("createProject = %d", res.StatusCode)
	}
	if cc := res.Header.Get("Cache-Control"); cc != "no-store" {
		t.Errorf("Cache-Control = %q, want no-store", cc)
	}
	status, body := f.send(web, call{method: http.MethodPost, path: "/api/v1/org/org-a/projects", body: projectBody("Staging", "https://staging.lk.example.com", "APIstaging", testLiveKitSecret+"-2"), cookie: admin})
	if status != http.StatusCreated || string(body) != `{"id":"staging","name":"Staging"}` {
		t.Fatalf("createProject = %d %s, want 201 with the project", status, body)
	}
	if bytes.Contains(body, []byte(testLiveKitSecret)) || bytes.Contains(body, []byte("APIstaging")) {
		t.Errorf("the answer holds a credential: %s", body)
	}

	var url, key string
	var stored []byte
	if err := f.pool.QueryRow(context.Background(), `SELECT livekit_url, livekit_api_key, livekit_api_secret_sealed FROM projects WHERE organisation_id = 'org-a' AND id = 'eu-production'`).Scan(&url, &key, &stored); err != nil {
		t.Fatalf("the first project was not stored under its slug: %v", err)
	}
	if url != "wss://eu.lk.example.com" || key != "APIeuprod" {
		t.Errorf("stored %s %s", url, key)
	}
	if bytes.Contains(stored, []byte(testLiveKitSecret)) {
		t.Error("the stored secret is in the clear")
	}
	opened, err := testSealer(t).Open(stored, "project:org-a/eu-production")
	if err != nil || string(opened) != testLiveKitSecret {
		t.Errorf("the stored secret opens to %q, %v; want the original", opened, err)
	}
	if got, err := f.sealed().LiveKitSecret(context.Background(), "org-a", "staging"); err != nil || got != testLiveKitSecret+"-2" {
		t.Errorf("LiveKitSecret(staging) = %v, want the original", err)
	}
	_, ws := f.send(web, call{method: http.MethodGet, path: "/api/v1/workspaces", cookie: admin})
	if !strings.Contains(string(ws), `{"id":"eu-production","name":"EU Production"}`) || !strings.Contains(string(ws), `{"id":"staging","name":"Staging"}`) {
		t.Errorf("getWorkspaces = %s, want both new projects", ws)
	}
}

// Acceptance check 3 of phase 06f: refusals. A taken id or LiveKit key is 409 with its code, a
// Member, a Viewer and a project key are 403, a non-member is 403, missing and invalid fields are
// 400, an oversized or unknown-field body is 400, and a create from another site is 403. None stores
// a row.
func TestCreateProjectRefusals(t *testing.T) {
	f := newFixture(t)
	f.addUser("admin@example.com", "org-a", "admin")
	f.addUser("member@example.com", "org-a", "member")
	f.addUser("viewer@example.com", "org-a", "viewer")
	f.addUser("eve@example.com", "org-b", "admin")
	s := f.service()
	web := f.membersAPI(s)
	admin := f.signedIn(s, "admin@example.com")
	path := "/api/v1/org/org-a/projects"
	ok := projectBody("Production", "wss://lk.example.com", "APIprod", testLiveKitSecret)
	if status, body := f.send(web, call{method: http.MethodPost, path: path, body: ok, cookie: admin}); status != http.StatusCreated {
		t.Fatalf("first create = %d %s", status, body)
	}
	f.addProject("org-a", "keyed")
	count := func() int { return f.count(`SELECT count(*) FROM projects`) }
	before := count()

	codeOf := func(b []byte) string {
		var e struct{ Code string }
		_ = json.Unmarshal(b, &e)
		return e.Code
	}
	for _, tt := range []struct {
		name   string
		c      call
		status int
		code   string
	}{
		{"same name", call{body: projectBody("production", "wss://other.example.com", "APIother", testLiveKitSecret), cookie: admin}, http.StatusConflict, "project_exists"},
		{"same LiveKit key", call{body: projectBody("Other", "wss://other.example.com", "APIprod", testLiveKitSecret), cookie: admin}, http.StatusConflict, "livekit_key_in_use"},
		{"member", call{body: projectBody("M", "wss://m.example.com", "APIm", testLiveKitSecret), cookie: f.signedIn(s, "member@example.com")}, http.StatusForbidden, "forbidden"},
		{"viewer", call{body: projectBody("V", "wss://v.example.com", "APIv", testLiveKitSecret), cookie: f.signedIn(s, "viewer@example.com")}, http.StatusForbidden, "forbidden"},
		{"another organisation's admin", call{body: projectBody("E", "wss://e.example.com", "APIe", testLiveKitSecret), cookie: f.signedIn(s, "eve@example.com")}, http.StatusForbidden, "forbidden"},
		{"project key", call{body: projectBody("K", "wss://k.example.com", "APIk", testLiveKitSecret), token: f.projectKey("org-a", "keyed")}, http.StatusForbidden, "forbidden"},
		{"no credential", call{body: projectBody("N", "wss://n.example.com", "APIn", testLiveKitSecret)}, http.StatusUnauthorized, "unauthenticated"},
		{"missing fields", call{body: `{"name":"Only a name"}`, cookie: admin}, http.StatusBadRequest, "invalid_request"},
		{"blank secret", call{body: projectBody("B", "wss://b.example.com", "APIb", "   "), cookie: admin}, http.StatusBadRequest, "invalid_request"},
		{"bad scheme", call{body: projectBody("F", "ftp://f.example.com", "APIf", testLiveKitSecret), cookie: admin}, http.StatusBadRequest, "invalid_request"},
		{"no host", call{body: projectBody("H", "wss://", "APIh", testLiveKitSecret), cookie: admin}, http.StatusBadRequest, "invalid_request"},
		{"name with no letter", call{body: projectBody("!!!", "wss://x.example.com", "APIx", testLiveKitSecret), cookie: admin}, http.StatusBadRequest, "invalid_request"},
		{"unknown field", call{body: `{"name":"U","livekitUrl":"wss://u.example.com","livekitApiKey":"APIu","livekitApiSecret":"s","id":"chosen"}`, cookie: admin}, http.StatusBadRequest, "invalid_request"},
		{"not JSON", call{body: `name=x`, cookie: admin}, http.StatusBadRequest, "invalid_request"},
		{"over 4 KiB", call{body: projectBody("Big", "wss://big.example.com", "APIbig", strings.Repeat("s", 5000)), cookie: admin}, http.StatusBadRequest, "invalid_request"},
	} {
		t.Run(tt.name, func(t *testing.T) {
			tt.c.method, tt.c.path = http.MethodPost, path
			status, body := f.send(web, tt.c)
			if status != tt.status || codeOf(body) != tt.code {
				t.Errorf("= %d %s, want %d %s", status, body, tt.status, tt.code)
			}
			if bytes.Contains(body, []byte(testLiveKitSecret)) {
				t.Errorf("the answer holds the secret: %s", body)
			}
		})
	}
	// The 409 for a taken key does not name the project that holds it.
	_, body := f.send(web, call{method: http.MethodPost, path: path, body: projectBody("Other", "wss://other.example.com", "APIprod", testLiveKitSecret), cookie: admin})
	if strings.Contains(string(body), "production") {
		t.Errorf("livekit_key_in_use names the owner: %s", body)
	}
	guarded := http.NewCrossOriginProtection().Handler(web)
	if status, _ := f.send(guarded, call{method: http.MethodPost, path: path, body: projectBody("Evil", "wss://evil.example.com", "APIevil", testLiveKitSecret), cookie: admin, headers: map[string]string{"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"}}); status != http.StatusForbidden {
		t.Errorf("cross-site create = %d, want 403", status)
	}
	if after := count(); after != before {
		t.Errorf("%d projects after the refusals, want %d", after, before)
	}
}
