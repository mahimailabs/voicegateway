package auth_test

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"net/http"
	"regexp"
	"slices"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/mahimailabs/voicegateway/internal/api"
	"github.com/mahimailabs/voicegateway/internal/auth"
	"github.com/mahimailabs/voicegateway/internal/store"
)

var keyPattern = regexp.MustCompile(`^vgk_[a-z2-7]{12}_[A-Za-z0-9_-]{43}$`)

type createdKey struct {
	KeyID, Prefix, Secret string
}

// createKey creates a key through createProjectKey as the given admin session.
func (f *fixture) createKey(web http.Handler, admin *http.Cookie, org, project string) createdKey {
	f.t.Helper()
	status, body := f.send(web, call{method: http.MethodPost, path: "/api/v1/org/" + org + "/project/" + project + "/keys", body: `{"name":"ci"}`, cookie: admin})
	var k createdKey
	if err := json.Unmarshal(body, &k); err != nil || status != http.StatusCreated {
		f.t.Fatalf("createProjectKey = %d %s", status, body)
	}
	return k
}

func (f *fixture) addProject(org, project string) {
	f.t.Helper()
	ctx := context.Background()
	if _, err := f.pool.Exec(ctx, `INSERT INTO organisations (id, name) VALUES ($1, $1) ON CONFLICT DO NOTHING`, org); err != nil {
		f.t.Fatal(err)
	}
	if _, err := f.pool.Exec(ctx, `INSERT INTO projects (organisation_id, id, name, livekit_url, livekit_api_key, livekit_api_secret_sealed) VALUES ($1, $2, $2, 'wss://x', 'API' || $1 || $2, '\x00')`, org, project); err != nil {
		f.t.Fatal(err)
	}
}

// Acceptance check 1 of phase 06c.
func TestCreateProjectKey(t *testing.T) {
	f := newFixture(t)
	f.addProjectCall("org-a")
	f.addUser("ada@example.com", "org-a", "admin")
	s := f.service()
	web := f.membersAPI(s)
	admin := f.signedIn(s, "ada@example.com")
	k := f.createKey(web, admin, "org-a", "p")
	if !keyPattern.MatchString(k.Secret) || k.Prefix != "vgk_"+k.KeyID || !strings.HasPrefix(k.Secret, k.Prefix+"_") {
		t.Fatalf("created key = %+v, want vgk_<key_id>_<secret>", k)
	}
	var stored []byte
	if err := f.pool.QueryRow(context.Background(), `SELECT secret_sha256 FROM project_keys WHERE key_id = $1`, k.KeyID).Scan(&stored); err != nil {
		t.Fatal(err)
	}
	want := sha256.Sum256([]byte(k.Secret[len(k.Prefix)+1:]))
	if !bytes.Equal(stored, want[:]) || bytes.Contains(stored, []byte(k.Secret)) {
		t.Error("the stored row does not hold the SHA-256 of the secret alone")
	}
	status, body := f.send(web, call{method: http.MethodGet, path: "/api/v1/org/org-a/project/p/keys", cookie: admin})
	if status != http.StatusOK || !strings.Contains(string(body), k.Prefix) || strings.Contains(string(body), k.Secret) || strings.Contains(string(body), `"secret"`) {
		t.Errorf("listProjectKeys = %d %s; want the key's prefix and no secret", status, body)
	}
	if strings.Contains(f.logs.String(), k.Secret[len(k.Prefix)+1:]) {
		t.Error("a log line holds the key's secret")
	}
}

// Acceptance check 2 of phase 06c.
func TestProjectKeyReadsOnlyItsProject(t *testing.T) {
	f := newFixture(t)
	callID := f.addProjectCall("org-a")
	f.addProject("org-a", "p2")
	f.addProjectCall("org-b")
	f.addUser("ada@example.com", "org-a", "admin")
	s := f.service()
	web := f.membersAPI(s)
	key := f.createKey(web, f.signedIn(s, "ada@example.com"), "org-a", "p").Secret
	for _, tt := range []struct {
		path string
		want int
	}{
		{"/api/v1/org/org-a/project/p/calls", http.StatusOK},
		{"/api/v1/org/org-a/project/p/calls/" + callID, http.StatusOK},
		{"/api/v1/org/org-a/project/p2/calls", http.StatusForbidden},
		{"/api/v1/org/org-b/project/p/calls", http.StatusForbidden},
		{"/api/v1/org/org-a/members", http.StatusForbidden},
		{"/api/v1/org/org-a/project/p/keys", http.StatusForbidden},
	} {
		if status, body := f.send(web, call{method: http.MethodGet, path: tt.path, token: key}); status != tt.want {
			t.Errorf("key GET %s = %d %s, want %d", tt.path, status, body, tt.want)
		}
	}
	status, body := f.send(web, call{method: http.MethodGet, path: "/api/v1/workspaces", token: key})
	var ws struct {
		Items []struct {
			ID       string
			Projects []struct{ ID string }
		}
	}
	_ = json.Unmarshal(body, &ws)
	if status != http.StatusOK || len(ws.Items) != 1 || ws.Items[0].ID != "org-a" || len(ws.Items[0].Projects) != 1 || ws.Items[0].Projects[0].ID != "p" {
		t.Errorf("key getWorkspaces = %d %s, want org-a with p only", status, body)
	}
}

// refusals builds, from a valid key, the refused values checks 3 and 3a send.
func refusals(t *testing.T, valid, revoked string) map[string]string {
	t.Helper()
	last := valid[len(valid)-1]
	swap := map[bool]byte{true: 'B', false: 'A'}[last == 'A']
	unknown := "vgk_aaaaaaaaaaaa" + valid[16:]
	return map[string]string{
		"revoked":           "Bearer " + revoked,
		"wrong secret":      "Bearer " + valid[:len(valid)-1] + string(swap),
		"unknown key_id":    "Bearer " + unknown,
		"not a key":         "Bearer vgk_short",
		"not vgk_":          "Bearer 0123456789abcdef0123456789abcdef",
		"underscore secret": "Bearer vgk_" + strings.Repeat("a", 12) + "_" + strings.Repeat("_", 43),
	}
}

// Acceptance check 3 of phase 06c.
func TestProjectKeyFailuresLookAlike(t *testing.T) {
	f := newFixture(t)
	f.addProjectCall("org-a")
	f.addUser("ada@example.com", "org-a", "admin")
	s := f.service()
	web := f.membersAPI(s)
	admin := f.signedIn(s, "ada@example.com")
	valid := f.createKey(web, admin, "org-a", "p").Secret
	revoked := f.createKey(web, admin, "org-a", "p")
	if status, _ := f.send(web, call{method: http.MethodDelete, path: "/api/v1/org/org-a/project/p/keys/" + revoked.KeyID, cookie: admin}); status != http.StatusNoContent {
		t.Fatal("revoke failed")
	}
	calls := "/api/v1/org/org-a/project/p/calls"
	var first string
	for name, auth := range refusals(t, valid, revoked.Secret) {
		status, body := f.send(web, call{method: http.MethodGet, path: calls, headers: map[string]string{"Authorization": auth}})
		// The request id differs per request; the rest of the body must not.
		got := regexp.MustCompile(`"requestId":"[^"]*"`).ReplaceAllString(string(body), "")
		if status != http.StatusUnauthorized {
			t.Errorf("%s = %d, want 401", name, status)
		}
		if first == "" {
			first = got
		} else if got != first {
			t.Errorf("%s body %s differs from %s", name, got, first)
		}
	}
	// A key whose secret holds "_" is read by fixed lengths, not split on it.
	underscored := ""
	for i := 0; i < 200 && underscored == ""; i++ {
		k := f.createKey(web, admin, "org-a", "p")
		if strings.Contains(k.Secret[17:], "_") {
			underscored = k.Secret
		}
	}
	if underscored == "" {
		t.Fatal("no key with _ in its secret in 200 tries")
	}
	if status, _ := f.send(web, call{method: http.MethodGet, path: calls, token: underscored}); status != http.StatusOK {
		t.Errorf("a key with _ in its secret = %d, want 200", status)
	}

	// The unknown key_id and the wrong secret take the same time, interleaved to cancel drift.
	r := refusals(t, valid, revoked.Secret)
	var unknownTimes, wrongTimes []time.Duration
	for range 10 { // warm the pool and the plan cache first
		f.send(web, call{method: http.MethodGet, path: calls, headers: map[string]string{"Authorization": r["unknown key_id"]}})
		f.send(web, call{method: http.MethodGet, path: calls, headers: map[string]string{"Authorization": r["wrong secret"]}})
	}
	for range 60 {
		for name, out := range map[string]*[]time.Duration{"unknown key_id": &unknownTimes, "wrong secret": &wrongTimes} {
			start := time.Now()
			f.send(web, call{method: http.MethodGet, path: calls, headers: map[string]string{"Authorization": r[name]}})
			*out = append(*out, time.Since(start))
		}
	}
	med := func(d []time.Duration) time.Duration { slices.Sort(d); return d[len(d)/2] }
	a, b := med(unknownTimes), med(wrongTimes)
	if diff := float64(max(a, b)-min(a, b)) / float64(max(a, b)); diff > 0.25 {
		t.Errorf("median times differ by %.0f%% (unknown %v, wrong %v), want under 25%%", diff*100, a, b)
	}
}

// Acceptance check 3a of phase 06c: every refusal runs the hash comparison, counted through the
// comparison the gate is given rather than inferred from timing.
func TestProjectKeyComparesEveryRefusal(t *testing.T) {
	f := newFixture(t)
	f.addProjectCall("org-a")
	f.addUser("ada@example.com", "org-a", "admin")
	s := f.service()
	web := f.membersAPI(s)
	admin := f.signedIn(s, "ada@example.com")
	valid := f.createKey(web, admin, "org-a", "p").Secret
	revoked := f.createKey(web, admin, "org-a", "p")
	f.send(web, call{method: http.MethodDelete, path: "/api/v1/org/org-a/project/p/keys/" + revoked.KeyID, cookie: admin})

	var compared atomic.Int32
	counted := f.service()
	counted.Compare = func(x, y []byte) int {
		compared.Add(1)
		return map[bool]int{true: 1, false: 0}[bytes.Equal(x, y)]
	}
	cweb := f.membersAPI(counted)
	r := refusals(t, valid, revoked.Secret)
	for _, name := range []string{"unknown key_id", "revoked", "wrong secret", "not a key", "not vgk_"} {
		compared.Store(0)
		if status, _ := f.send(cweb, call{method: http.MethodGet, path: "/api/v1/org/org-a/project/p/calls", headers: map[string]string{"Authorization": r[name]}}); status != http.StatusUnauthorized {
			t.Errorf("%s = %d, want 401", name, status)
		}
		if n := compared.Load(); n != 1 {
			t.Errorf("%s ran the comparison %d times, want 1", name, n)
		}
	}
}

// Acceptance check 4 of phase 06c.
func TestProjectKeyLastUsedIsThrottled(t *testing.T) {
	f := newFixture(t)
	f.addProjectCall("org-a")
	f.addUser("ada@example.com", "org-a", "admin")
	s := f.service()
	web := f.membersAPI(s)
	k := f.createKey(web, f.signedIn(s, "ada@example.com"), "org-a", "p")
	var writes int
	var last *time.Time
	for range 50 {
		f.send(web, call{method: http.MethodGet, path: "/api/v1/org/org-a/project/p/calls", token: k.Secret})
		var now *time.Time
		if err := f.pool.QueryRow(context.Background(), `SELECT last_used_at FROM project_keys WHERE key_id = $1`, k.KeyID).Scan(&now); err != nil {
			t.Fatal(err)
		}
		if now != nil && (last == nil || !now.Equal(*last)) {
			writes++
		}
		last = now
	}
	if last == nil || writes != 1 {
		t.Errorf("last_used_at set %d times over 50 uses in a minute (last %v), want exactly once", writes, last)
	}
}

// Acceptance check 5 of phase 06c.
func TestProjectKeyRoles(t *testing.T) {
	f := newFixture(t)
	f.addProjectCall("org-a")
	f.addProject("org-a", "p2")
	f.addUser("admin@example.com", "org-a", "admin")
	f.addUser("member@example.com", "org-a", "member")
	f.addUser("viewer@example.com", "org-a", "viewer")
	s := f.service()
	web := f.membersAPI(s)
	admin := f.signedIn(s, "admin@example.com")
	target := f.createKey(web, admin, "org-a", "p")
	keys := "/api/v1/org/org-a/project/p/keys"
	for _, role := range []string{"viewer", "member", "admin"} {
		cookie := f.signedIn(s, role+"@example.com")
		for _, c := range []call{
			{method: http.MethodGet, path: keys},
			{method: http.MethodPost, path: keys, body: `{"name":"x"}`},
			{method: http.MethodDelete, path: keys + "/" + target.KeyID},
		} {
			c.cookie = cookie
			status, _ := f.send(web, c)
			want := http.StatusForbidden
			if role == "admin" {
				want = map[string]int{http.MethodGet: http.StatusOK, http.MethodPost: http.StatusCreated, http.MethodDelete: http.StatusNoContent}[c.method]
			}
			if status != want {
				t.Errorf("%s %s = %d, want %d", role, c.method, status, want)
			}
		}
	}
	// Revoking twice is 204 both times; a key of another project is 404 through this one's path.
	if status, _ := f.send(web, call{method: http.MethodDelete, path: keys + "/" + target.KeyID, cookie: admin}); status != http.StatusNoContent {
		t.Errorf("second revoke = %d, want 204", status)
	}
	other := f.createKey(web, admin, "org-a", "p2")
	if status, _ := f.send(web, call{method: http.MethodDelete, path: keys + "/" + other.KeyID, cookie: admin}); status != http.StatusNotFound {
		t.Errorf("revoking p2's key through p = %d, want 404", status)
	}
	if status, _ := f.send(web, call{method: http.MethodGet, path: "/api/v1/org/org-a/project/p2/calls", token: other.Secret}); status != http.StatusOK {
		t.Errorf("p2's key after the refused revoke = %d, want 200", status)
	}
	// A create from another site is refused before it reaches the API.
	var before int
	_ = f.pool.QueryRow(context.Background(), `SELECT count(*) FROM project_keys`).Scan(&before)
	guarded := http.NewCrossOriginProtection().Handler(web)
	if status, _ := f.send(guarded, call{method: http.MethodPost, path: keys, body: `{"name":"evil"}`, cookie: admin, headers: map[string]string{"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"}}); status != http.StatusForbidden {
		t.Errorf("cross-site create = %d, want 403", status)
	}
	var after int
	_ = f.pool.QueryRow(context.Background(), `SELECT count(*) FROM project_keys`).Scan(&after)
	if after != before {
		t.Error("a cross-site create stored a key")
	}
	// Deleting a project deletes its keys.
	ctx := context.Background()
	if _, err := f.pool.Exec(ctx, `DELETE FROM projects WHERE organisation_id = 'org-a' AND id = 'p2'`); err != nil {
		t.Fatal(err)
	}
	var left int
	_ = f.pool.QueryRow(ctx, `SELECT count(*) FROM project_keys WHERE project_id = 'p2'`).Scan(&left)
	if left != 0 {
		t.Errorf("%d keys outlived their project", left)
	}
}

// keyIDs draws key ids from a list, then real ones: to make createProjectKey meet a taken id.
func keyIDs(ids ...string) func() (string, []byte, string, error) {
	n := 0
	return func() (string, []byte, string, error) {
		keyID, sum, full, err := auth.NewKey()
		if n < len(ids) {
			full = "vgk_" + ids[n] + full[16:]
			keyID = ids[n]
		}
		n++
		return keyID, sum, full, err
	}
}

// A key_id is 60 random bits and the primary key: one that is taken is drawn again, once.
func TestCreateProjectKeyRetriesATakenKeyID(t *testing.T) {
	f := newFixture(t)
	f.addProjectCall("org-a")
	f.addUser("ada@example.com", "org-a", "admin")
	s := f.service()
	admin := f.signedIn(s, "ada@example.com")
	taken := f.createKey(f.membersAPI(s), admin, "org-a", "p").KeyID
	newAPI := func(draw func() (string, []byte, string, error)) http.Handler {
		return (&api.API{Projects: f.pg, Calls: f.pg, Accounts: f.pg, Keys: f.pg, NewKey: draw, Log: slog.New(slog.NewJSONHandler(io.Discard, nil)), Now: time.Now, Gate: s.Gate}).Routes()
	}
	create := call{method: http.MethodPost, path: "/api/v1/org/org-a/project/p/keys", body: `{"name":"retry"}`, cookie: admin}
	status, body := f.send(newAPI(keyIDs(taken)), create)
	var k createdKey
	_ = json.Unmarshal(body, &k)
	if status != http.StatusCreated || k.KeyID == taken {
		t.Errorf("one taken key id = %d %s, want 201 with another id", status, body)
	}
	if status, _ := f.send(newAPI(keyIDs(taken, taken)), create); status != http.StatusInternalServerError {
		t.Errorf("two taken key ids in a row = %d, want 500 after the one retry", status)
	}
}

// failingKeys fails the key lookup, to show the gate answers 500, not 401, when it cannot check.
type failingKeys struct{ auth.Users }

func (failingKeys) ProjectKey(context.Context, string) (store.StoredKey, error) {
	return store.StoredKey{}, errors.New("dial tcp 127.0.0.1:5432: connection refused")
}

func TestProjectKeyLookupFailureIsNotARefusal(t *testing.T) {
	f := newFixture(t)
	f.addProjectCall("org-a")
	s := f.service()
	broken := &auth.Service{Users: failingKeys{Users: f.pg}, Secure: s.Secure, Plain: s.Plain, Log: s.Log, Now: time.Now}
	key := f.projectKey("org-a", "p")
	status, _ := f.send(f.membersAPI(broken), call{method: http.MethodGet, path: "/api/v1/org/org-a/project/p/calls", token: key})
	if status != http.StatusInternalServerError {
		t.Errorf("key lookup failure = %d, want 500", status)
	}
	if strings.Contains(f.logs.String(), key[17:]) {
		t.Error("a log line holds the key's secret")
	}
}
