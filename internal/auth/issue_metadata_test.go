package auth_test

import (
	"context"
	"encoding/json"
	"net/http"
	"strings"
	"testing"

	"github.com/mahimailabs/voicegateway/internal/store"
)

func TestIssueMetadataPermissionsAndPersistence(t *testing.T) {
	f := newFixture(t)
	id := f.addProjectCall("org-a")
	finding := []store.Finding{{ID: "slow", Title: "Slow answers", Severity: "medium", Detail: "Reported latency", Turns: []int{0}}}
	if err := f.pg.ApplyCallFindings(context.Background(), "org-a", "p", id, finding); err != nil {
		t.Fatal(err)
	}
	f.addUser("member@example.com", "org-a", "member")
	f.addUser("viewer@example.com", "org-a", "viewer")
	f.addUser("outside@example.com", "org-b", "admin")
	s := f.service()
	web := http.NewCrossOriginProtection().Handler(f.membersAPI(s))
	member, viewer, outside := f.signedIn(s, "member@example.com"), f.signedIn(s, "viewer@example.com"), f.signedIn(s, "outside@example.com")
	key := f.projectKey("org-a", "p")
	path := "/api/v1/org/org-a/project/p/issues/slow/metadata"
	valid := `{"tags":[" Follow-up "],"archived":true,"revision":0,"evidenceRevision":1,"state":"resolved"}`
	check := func(c call, want int) []byte {
		t.Helper()
		status, body := f.send(web, c)
		if status != want {
			t.Fatalf("%s %s: %d %s want %d", c.method, c.path, status, body, want)
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
		`{}`, `null`, `[]`, valid + `{}`, strings.Repeat(" ", 16385) + valid,
		strings.Replace(valid, `"revision":0`, `"revision":-1`, 1),
		strings.Replace(valid, `"evidenceRevision":1`, `"evidenceRevision":-1`, 1),
		strings.Replace(valid, `"resolved"`, `"invalid"`, 1),
		strings.Replace(valid, `"tags":[" Follow-up "]`, `"tags":null`, 1),
		strings.Replace(valid, "Follow-up", string([]byte{0xff}), 1),
	} {
		check(call{method: http.MethodPut, path: path, body: body, cookie: member}, http.StatusBadRequest)
	}
	check(call{method: http.MethodPut, path: path, body: strings.Replace(valid, `"evidenceRevision":1`, `"evidenceRevision":0`, 1), cookie: member}, http.StatusConflict)
	check(call{method: http.MethodPut, path: path, body: valid, cookie: member}, http.StatusOK)
	check(call{method: http.MethodPut, path: path, body: valid, cookie: member}, http.StatusConflict)
	for _, c := range []call{{method: http.MethodGet, path: path, cookie: viewer}, {method: http.MethodGet, path: path, token: key}} {
		var got store.IssueMetadata
		if err := json.Unmarshal(check(c, http.StatusOK), &got); err != nil {
			t.Fatal(err)
		}
		if got.State != "resolved" || !got.Archived || got.Revision != 1 || len(got.Tags) != 1 || got.Tags[0] != "follow-up" {
			t.Fatalf("persisted decision: %+v", got)
		}
	}
	check(call{method: http.MethodGet, path: strings.Replace(path, "/slow/", "/absent/", 1), cookie: member}, http.StatusNotFound)
	check(call{method: http.MethodGet, path: strings.Replace(path, "/org-a/", "/org-b/", 1), token: key}, http.StatusForbidden)
	web = http.NewCrossOriginProtection().Handler(f.membersAPI(f.service()))
	check(call{method: http.MethodGet, path: path, cookie: member}, http.StatusOK)
}
