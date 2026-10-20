package auth_test

import (
	"context"
	"encoding/json"
	"net/http"
	"strings"
	"testing"

	"github.com/mahimailabs/voicegateway/internal/store"
)

func TestCallMetadataSessionPersistenceAndPermissions(t *testing.T) {
	f := newFixture(t)
	id := f.addProjectCall("org-a")
	other := f.addProjectCall("org-b")
	f.addUser("member@example.com", "org-a", "member")
	f.addUser("viewer@example.com", "org-a", "viewer")
	f.addUser("admin@example.com", "org-a", "admin")
	f.addUser("outside@example.com", "org-b", "admin")
	s := f.service()
	web := http.NewCrossOriginProtection().Handler(f.membersAPI(s))
	path := "/api/v1/org/org-a/project/p/calls/" + id + "/metadata"
	valid := `{"tags":[" Demo ","demo"],"archived":true,"revision":0}`
	member := f.signedIn(s, "member@example.com")
	viewer := f.signedIn(s, "viewer@example.com")
	admin := f.signedIn(s, "admin@example.com")
	outside := f.signedIn(s, "outside@example.com")
	key := f.projectKey("org-a", "p")
	check := func(c call, want int) []byte {
		t.Helper()
		code, body := f.send(web, c)
		if code != want {
			t.Fatalf("%s %s: %d %s want %d", c.method, c.path, code, body, want)
		}
		return body
	}
	for _, c := range []call{
		{method: http.MethodPut, path: path, body: valid, cookie: viewer},
		{method: http.MethodPut, path: path, body: valid, cookie: outside},
		{method: http.MethodPut, path: path, body: valid, token: key},
		{method: http.MethodPut, path: path, body: valid, cookie: member, headers: map[string]string{"Origin": "https://evil.example"}},
	} {
		check(c, http.StatusForbidden)
	}
	check(call{method: http.MethodPut, path: path, body: valid}, http.StatusUnauthorized)
	for _, body := range []string{
		`{}`, `null`, `[]`, `{"tags":null,"archived":false,"revision":0}`,
		`{"tags":[],"revision":0}`, `{"tags":[],"archived":false}`, valid + `{}`,
		strings.Repeat(" ", 16385) + valid, strings.Replace(valid, `"revision":0`, `"revision":-1`, 1),
		strings.Replace(valid, `"revision":0`, `"revision":1.5`, 1),
		strings.Replace(valid, `"revision":0`, `"revision":9007199254740991`, 1),
		strings.Replace(valid, `" Demo "`, `"bad\n"`, 1),
		strings.Replace(valid, `" Demo "`, `"`+strings.Repeat("界", 65)+`"`, 1),
		strings.Replace(valid, `"tags":`, `"targetUser":"someone","tags":`, 1),
		strings.Replace(valid, "Demo", string([]byte{0xff}), 1),
	} {
		check(call{method: http.MethodPut, path: path, body: body, cookie: member}, http.StatusBadRequest)
	}
	check(call{method: http.MethodPut, path: path, body: valid, cookie: member}, http.StatusOK)
	check(call{method: http.MethodPut, path: path, body: valid, cookie: admin}, http.StatusConflict)
	for _, c := range []call{
		{method: http.MethodGet, path: path, cookie: viewer},
		{method: http.MethodGet, path: path, token: key},
	} {
		var got store.CallMetadata
		if err := json.Unmarshal(check(c, http.StatusOK), &got); err != nil {
			t.Fatal(err)
		}
		if got.Revision != 1 || !got.Archived || len(got.Tags) != 1 || got.Tags[0] != "demo" {
			t.Fatalf("metadata %+v", got)
		}
	}
	for _, method := range []string{http.MethodGet, http.MethodPut} {
		for _, wrong := range []string{other, "00000000-0000-4000-8000-000000000001", "not-a-uuid"} {
			check(call{method: method, path: strings.Replace(path, id, wrong, 1), body: valid, cookie: member}, http.StatusNotFound)
		}
	}
	check(call{method: http.MethodGet, path: strings.Replace(path, "org-a", "org-b", 1), token: key}, http.StatusForbidden)
	check(call{method: http.MethodPut, path: path, body: `{"tags":[],"archived":false,"revision":1}`, cookie: admin}, http.StatusOK)
	got, err := f.pg.CallMetadata(context.Background(), "org-a", "p", id)
	if err != nil || got.Archived || len(got.Tags) != 0 || got.Revision != 2 {
		t.Fatalf("clear %+v %v", got, err)
	}
	// A second web instance sees the same metadata, not browser-local state.
	code, body := f.send(f.membersAPI(f.service()), call{method: http.MethodGet, path: path, cookie: viewer})
	if code != http.StatusOK || !strings.Contains(string(body), `"revision":2`) {
		t.Fatalf("second instance %d %s", code, body)
	}
}
