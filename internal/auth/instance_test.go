package auth_test

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
	"strings"
	"sync"
	"testing"
	"time"
)

// instanceToken is a VOICEGATEWAY_INSTANCE_TOKEN for the tests: 44 characters, as openssl rand
// -base64 32 makes one.
const instanceToken = "aW5zdGFuY2UtdG9rZW4tZm9yLXRoZS10ZXN0cy0wMDA="

// lastHeader is the last response's headers from send.
func (f *fixture) lastHeader() http.Header {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.header
}

// instanceOrg is the organisation path of the instance API.
func instanceOrg(id string) string { return "/api/v1/instance/organisations/" + id }

type instanceOrganisation struct {
	ID, Name                            string
	CreatedAt                           time.Time
	Members, Admins, PendingInvitations int64
}

type instanceInvitation struct {
	InvitationID, Email, Role string
	ExpiresAt                 time.Time
	Emailed                   bool
	Link                      *string
}

// put calls putOrganisation with the instance token.
func (w *inviteWeb) put(id, name string) (int, instanceOrganisation) {
	w.f.t.Helper()
	status, body := w.f.send(w.h, call{method: http.MethodPut, path: instanceOrg(id), body: fmt.Sprintf(`{"name":%q}`, name), token: instanceToken})
	var o instanceOrganisation
	if status == http.StatusOK || status == http.StatusCreated {
		if err := json.Unmarshal(body, &o); err != nil {
			w.f.t.Error(err)
		}
	}
	return status, o
}

// organisation calls getOrganisation with the instance token.
func (w *inviteWeb) organisation(id string) (int, instanceOrganisation) {
	w.f.t.Helper()
	status, body := w.f.send(w.h, call{method: http.MethodGet, path: instanceOrg(id), token: instanceToken})
	var o instanceOrganisation
	if status == http.StatusOK {
		if err := json.Unmarshal(body, &o); err != nil {
			w.f.t.Error(err)
		}
	}
	return status, o
}

// instanceInvite calls createOrganisationInvitation with the instance token and an Idempotency-Key.
func (w *inviteWeb) instanceInvite(org, email, role, key string) (int, instanceInvitation) {
	w.f.t.Helper()
	status, body := w.f.send(w.h, call{
		method: http.MethodPost, path: instanceOrg(org) + "/invitations", body: fmt.Sprintf(`{"email":%q,"role":%q}`, email, role),
		token: instanceToken, headers: map[string]string{"Idempotency-Key": key},
	})
	var c instanceInvitation
	if status == http.StatusOK || status == http.StatusCreated {
		if err := json.Unmarshal(body, &c); err != nil {
			w.f.t.Error(err)
		}
	}
	return status, c
}

type instanceUsage struct {
	OrganisationID                                                                        string
	From, To                                                                              time.Time
	CallSeconds, Calls, InferredCalls, InferredCallSeconds, UnendedCalls, UnmeasuredCalls int64
}

// usage calls getOrganisationUsage with the instance token for [from, to).
func (w *inviteWeb) usage(org string, from, to time.Time) (int, instanceUsage) {
	w.f.t.Helper()
	q := url.Values{"from": {from.Format(time.RFC3339)}, "to": {to.Format(time.RFC3339)}}
	status, body := w.f.send(w.h, call{method: http.MethodGet, path: instanceOrg(org) + "/usage?" + q.Encode(), token: instanceToken})
	var u instanceUsage
	if status == http.StatusOK {
		if err := json.Unmarshal(body, &u); err != nil {
			w.f.t.Error(err)
		}
	}
	return status, u
}

// instanceCalls are the 4 instance operations, each with a valid body.
var instanceCalls = []call{
	{method: http.MethodPut, path: instanceOrg("acme"), body: `{"name":"Acme"}`},
	{method: http.MethodGet, path: instanceOrg("acme")},
	{method: http.MethodPost, path: instanceOrg("acme") + "/invitations", body: `{"email":"first@example.com","role":"Admin"}`, headers: map[string]string{"Idempotency-Key": "checkout-1"}},
	{method: http.MethodGet, path: instanceOrg("acme") + "/usage?from=2026-10-01T00%3A00%3A00Z&to=2026-11-01T00%3A00%3A00Z"},
}

// Acceptance check 1 of phase 06e.
func TestInstanceAPIOffWithoutToken(t *testing.T) {
	f := newFixture(t)
	w := f.web(nil)
	for _, c := range instanceCalls {
		for name, token := range map[string]string{"no credential": "", "a Bearer": instanceToken} {
			c.token = token
			if status, body := f.send(w.h, c); status != http.StatusNotFound || !strings.Contains(string(body), `"not_found"`) {
				t.Errorf("%s %s with %s = %d %s, want 404 not_found", c.method, c.path, name, status, body)
			}
		}
	}
	if n := f.count(`SELECT count(*) FROM organisations WHERE id = 'acme'`); n != 0 {
		t.Errorf("%d acme organisations with the API off, want 0", n)
	}
}

// Acceptance check 2 of phase 06e.
func TestInstanceScopeIsItsOwn(t *testing.T) {
	f := newFixture(t)
	f.addProjectCall("org-a")
	f.addUser("admin@example.com", "org-a", "admin")
	w := f.webWith(nil, instanceToken)
	admin := w.signIn("admin@example.com")
	key := f.projectKey("org-a", "p")
	wrong := strings.Repeat("x", len(instanceToken))
	cases := 0
	for _, c := range instanceCalls {
		for _, tc := range []struct {
			name   string
			cookie *http.Cookie
			token  string
			want   int
		}{
			{"no credential", nil, "", http.StatusUnauthorized},
			{"a wrong token of the same length", nil, wrong, http.StatusUnauthorized},
			{"an Admin's session", admin, "", http.StatusForbidden},
			{"a project API key", nil, key, http.StatusForbidden},
		} {
			c.cookie, c.token = tc.cookie, tc.token
			if status, body := f.send(w.h, c); status != tc.want {
				t.Errorf("%s %s with %s = %d %s, want %d", c.method, c.path, tc.name, status, body, tc.want)
			}
			if challenge := f.lastHeader().Get("WWW-Authenticate"); tc.want == http.StatusUnauthorized && !strings.HasPrefix(challenge, "Bearer") {
				t.Errorf("%s %s with %s: WWW-Authenticate = %q, want a Bearer challenge", c.method, c.path, tc.name, challenge)
			}
			cases++
		}
	}
	for _, path := range []string{"/api/v1/workspaces", "/api/v1/org/org-a/members", "/api/v1/org/org-a/project/p/calls"} {
		if status, body := f.send(w.h, call{method: http.MethodGet, path: path, token: instanceToken}); status != http.StatusForbidden || !strings.Contains(string(body), `"forbidden"`) {
			t.Errorf("GET %s with the instance token = %d %s, want 403 forbidden", path, status, body)
		}
		cases++
	}
	before := f.count(`SELECT count(*) FROM projects`)
	if status, body := f.send(w.h, call{method: http.MethodPost, path: "/api/v1/org/org-a/projects", body: projectBody("Blocked", "wss://blocked.example.com", "APIblocked", testLiveKitSecret), token: instanceToken}); status != http.StatusForbidden || !strings.Contains(string(body), `"forbidden"`) {
		t.Errorf("createProject with the instance token = %d %s, want 403 forbidden", status, body)
	}
	if after := f.count(`SELECT count(*) FROM projects`); after != before {
		t.Errorf("instance token created a project: before=%d after=%d", before, after)
	}
	cases++
	if cases != 20 {
		t.Errorf("%d cases, want 20", cases)
	}
	if n := f.count(`SELECT count(*) FROM organisations WHERE id = 'acme'`); n != 0 {
		t.Errorf("%d acme organisations after refused calls, want 0", n)
	}
}

// Acceptance check 3 of phase 06e, with the rules of putOrganisation's body and id.
func TestPutOrganisationIsIdempotent(t *testing.T) {
	f := newFixture(t)
	w := f.webWith(nil, instanceToken)
	statuses := make(chan int, 10)
	var wg sync.WaitGroup
	for range 10 {
		wg.Go(func() {
			status, o := w.put("acme", "Acme")
			if o.ID != "" && (o.ID != "acme" || o.Name != "Acme") {
				t.Errorf("putOrganisation answered %+v", o)
			}
			statuses <- status
		})
	}
	wg.Wait()
	close(statuses)
	got := map[int]int{}
	for s := range statuses {
		got[s]++
	}
	if got[http.StatusCreated] != 1 || got[http.StatusOK] != 9 {
		t.Errorf("10 parallel putOrganisation = %v, want one 201 and nine 200", got)
	}
	if n := f.count(`SELECT count(*) FROM organisations WHERE id = 'acme'`); n != 1 {
		t.Errorf("%d acme organisations, want 1", n)
	}

	if status, o := w.put("acme", "Acme Ltd"); status != http.StatusOK || o.Name != "Acme Ltd" {
		t.Errorf("a rename = %d %+v, want 200 with the new name", status, o)
	}
	for _, tc := range []struct{ id, body string }{
		{"Acme", `{"name":"Acme"}`},
		{"-acme", `{"name":"Acme"}`},
		{"acme", `{"name":""}`},
		{"acme", `{"name":"   "}`},
		{"acme", `{}`},
		{"acme", `{"name":"Acme","plan":"pro"}`},
	} {
		if status, body := f.send(w.h, call{method: http.MethodPut, path: instanceOrg(tc.id), body: tc.body, token: instanceToken}); status != http.StatusBadRequest || !strings.Contains(string(body), `"invalid_request"`) {
			t.Errorf("putOrganisation %s %s = %d %s, want 400 invalid_request", tc.id, tc.body, status, body)
		}
	}
	if status, _ := w.organisation("nobody"); status != http.StatusNotFound {
		t.Errorf("getOrganisation of an unknown id = %d, want 404", status)
	}
}

// Acceptance check 4 of phase 06e.
func TestFirstAdminJoinsByInvitation(t *testing.T) {
	f := newFixture(t)
	f.addUser("someone@example.com", "other-org", "admin")
	w := f.webWith(nil, instanceToken)
	if status, o := w.put("acme", "Acme"); status != http.StatusCreated || o.Members != 0 || o.Admins != 0 || o.PendingInvitations != 0 {
		t.Fatalf("putOrganisation = %d %+v, want 201 with no members", status, o)
	}
	status, c := w.instanceInvite("acme", "first@example.com", "Admin", "checkout-1")
	if status != http.StatusCreated || c.Emailed || c.Link == nil || !linkPattern.MatchString(*c.Link) || c.Role != "Admin" || c.Email != "first@example.com" {
		t.Fatalf("createOrganisationInvitation = %d %+v, want 201, emailed false and a link", status, c)
	}
	if n := f.count(`SELECT count(*) FROM invitations WHERE id = $1 AND invited_by IS NULL AND idempotency_key = 'checkout-1'`, c.InvitationID); n != 1 {
		t.Error("the invitation has an inviting user or lost its idempotency key")
	}
	if _, o := w.organisation("acme"); o.PendingInvitations != 1 || o.Admins != 0 {
		t.Errorf("getOrganisation before the accept = %+v, want 1 pending and no admin", o)
	}

	res, body := w.accept(tokenOf(*c.Link), newPassword, request{tls: true})
	joined := sessionCookie(res)
	if res.StatusCode != http.StatusNoContent || joined == nil {
		t.Fatalf("accept = %d %s, want 204 with a session", res.StatusCode, body)
	}
	if status, roles := w.session(joined); status != http.StatusOK || len(roles) != 1 || roles["acme"] != "admin" {
		t.Errorf("session = %d %v, want acme as admin and nothing else", status, roles)
	}
	if status, o := w.organisation("acme"); status != http.StatusOK || o.ID != "acme" || o.Name != "Acme" || o.Members != 1 || o.Admins != 1 || o.PendingInvitations != 0 {
		t.Errorf("getOrganisation after the accept = %d %+v, want 1 member, admins 1 and pendingInvitations 0", status, o)
	}

	// The invitation's other answers: a member is 409, an unknown organisation 404, a missing key
	// or a bad role 400.
	if status, _ := w.instanceInvite("acme", "first@example.com", "Admin", "checkout-2"); status != http.StatusConflict {
		t.Errorf("inviting a member = %d, want 409", status)
	}
	if status, _ := w.instanceInvite("nobody", "x@example.com", "Admin", "checkout-3"); status != http.StatusNotFound {
		t.Errorf("inviting to an unknown organisation = %d, want 404", status)
	}
	if status, _ := w.instanceInvite("acme", "x@example.com", "Owner", "checkout-4"); status != http.StatusBadRequest {
		t.Errorf("a bad role = %d, want 400", status)
	}
	for _, k := range []string{"", strings.Repeat("k", 201), "tab\there"} {
		status, body := f.send(w.h, call{
			method: http.MethodPost, path: instanceOrg("acme") + "/invitations", body: `{"email":"x@example.com","role":"Member"}`,
			token: instanceToken, headers: map[string]string{"Idempotency-Key": k},
		})
		if status != http.StatusBadRequest || !strings.Contains(string(body), `"invalid_request"`) {
			t.Errorf("Idempotency-Key %q = %d %s, want 400 invalid_request", k, status, body)
		}
	}
}

// Acceptance check 5 of phase 06e.
func TestInvitationIsNotDuplicated(t *testing.T) {
	f := newFixture(t)
	mp := startMailpit(t)
	w := f.webWith(mp.mailer(t), instanceToken)
	if status, _ := w.put("acme", "Acme"); status != http.StatusCreated {
		t.Fatalf("putOrganisation = %d", status)
	}
	statuses := make(chan int, 10)
	var wg sync.WaitGroup
	for range 10 {
		wg.Go(func() {
			status, c := w.instanceInvite("acme", "first@example.com", "Admin", "checkout-1")
			if c.Link != nil {
				t.Errorf("an answer with SMTP set holds a link")
			}
			statuses <- status
		})
	}
	wg.Wait()
	close(statuses)
	got := map[int]int{}
	for s := range statuses {
		got[s]++
	}
	if got[http.StatusCreated] != 1 || got[http.StatusOK] != 9 {
		t.Errorf("10 parallel createOrganisationInvitation = %v, want one 201 and nine 200", got)
	}
	if n := f.count(`SELECT count(*) FROM invitations WHERE organisation_id = 'acme' AND accepted_at IS NULL AND revoked_at IS NULL`); n != 1 {
		t.Errorf("%d open invitations, want 1", n)
	}
	texts := mp.messages(t, "first@example.com")
	if len(texts) != 1 || !mailedLink.MatchString(texts[0]) {
		t.Fatalf("mailpit holds %d messages for the invited address, want 1 with a link", len(texts))
	}
	first := mailedLink.FindString(texts[0])

	if status, _ := w.instanceInvite("acme", "other@example.com", "Admin", "checkout-1"); status != http.StatusBadRequest {
		t.Errorf("the same key with another email = %d, want 400", status)
	}
	if status, _ := w.instanceInvite("acme", "first@example.com", "Member", "checkout-1"); status != http.StatusBadRequest {
		t.Errorf("the same key with another role = %d, want 400", status)
	}

	if status, c := w.instanceInvite("acme", "first@example.com", "Admin", "checkout-2"); status != http.StatusCreated || !c.Emailed || c.Link != nil {
		t.Errorf("a new key = %d %+v, want 201, emailed and no link", status, c)
	}
	var second string
	for _, text := range mp.messages(t, "first@example.com") {
		if l := mailedLink.FindString(text); l != "" && l != first {
			second = l
		}
	}
	if second == "" {
		t.Fatal("the new key sent no new link")
	}
	if res, _ := w.accept(tokenOf(first), newPassword, request{tls: true}); res.StatusCode != http.StatusNotFound {
		t.Errorf("the first link after a new key = %d, want 404", res.StatusCode)
	}
	if res, body := w.accept(tokenOf(second), newPassword, request{tls: true}); res.StatusCode != http.StatusNoContent {
		t.Errorf("the new link = %d %s, want 204", res.StatusCode, body)
	}
}

// Acceptance check 6 of phase 06e.
func TestInvitationEmailedWhenSMTPIsSet(t *testing.T) {
	f := newFixture(t)
	mp := startMailpit(t)
	w := f.webWith(mp.mailer(t), instanceToken)
	if status, _ := w.put("acme", "Acme"); status != http.StatusCreated {
		t.Fatalf("putOrganisation = %d", status)
	}
	status, c := w.instanceInvite("acme", "first@example.com", "Admin", "checkout-1")
	if status != http.StatusCreated || !c.Emailed || c.Link != nil {
		t.Errorf("createOrganisationInvitation with SMTP = %d %+v, want 201, emailed true and link null", status, c)
	}
	if texts := mp.messages(t, "first@example.com"); len(texts) != 1 || !mailedLink.MatchString(texts[0]) || !strings.Contains(texts[0], "You are invited to join Acme on VoiceGateway") {
		t.Errorf("mailpit holds %d messages for the invited address, want 1 with the link", len(texts))
	}

	w.links.Mailer = unreachableMailer(t)
	status, c = w.instanceInvite("acme", "second@example.com", "Admin", "checkout-2")
	if status != http.StatusCreated || c.Emailed || c.Link != nil {
		t.Errorf("createOrganisationInvitation with SMTP failing = %d %+v, want 201, emailed false and link null", status, c)
	}
	if status, c := w.instanceInvite("acme", "second@example.com", "Admin", "checkout-2"); status != http.StatusOK || c.Emailed || c.Link != nil {
		t.Errorf("a repeat of the failed send = %d %+v, want 200 with emailed false, nothing resent", status, c)
	}

	w.links.Mailer = mp.mailer(t)
	if status, c := w.instanceInvite("acme", "second@example.com", "Admin", "checkout-3"); status != http.StatusCreated || !c.Emailed || c.Link != nil {
		t.Errorf("a new key once the sink is back = %d %+v, want 201, emailed true and link null", status, c)
	}
	texts := mp.messages(t, "second@example.com")
	if len(texts) != 1 || !mailedLink.MatchString(texts[0]) {
		t.Fatalf("mailpit holds %d messages for the second address, want the one replacement", len(texts))
	}
	if res, _ := w.look(tokenOf(mailedLink.FindString(texts[0]))); res.StatusCode != http.StatusOK {
		t.Errorf("the replacement link = %d, want 200", res.StatusCode)
	}
	if n := f.count(`SELECT count(*) FROM invitations WHERE email = 'second@example.com' AND accepted_at IS NULL AND revoked_at IS NULL`); n != 1 {
		t.Errorf("%d open invitations for the second address, want 1", n)
	}
}

// Acceptance check 7 of phase 06e.
func TestUsageCountsEndedCallsOnly(t *testing.T) {
	f := newFixture(t)
	w := f.webWith(nil, instanceToken)
	from := time.Date(2025, 10, 1, 0, 0, 0, 0, time.UTC)
	to := time.Date(2025, 11, 1, 0, 0, 0, 0, time.UTC)
	f.addProjectCall("acme")
	f.addProjectCall("other")
	at := func(d time.Duration) time.Time { return from.Add(10*24*time.Hour + d) }
	// Durations of 90.6 s and 29.6 s sum to 120.2 s, 120 whole seconds; rounding each call first
	// gives 119 when floored or 121 when rounded to nearest. The inferred call ends at its last activity (decision 0029).
	for _, c := range []struct {
		org, sid, state string
		start, end      *time.Time
	}{
		{"acme", "RM_observed", "observed", new(at(0)), new(at(90600 * time.Millisecond))},
		{"acme", "RM_inferred", "inferred", new(at(time.Hour)), new(at(time.Hour + 29600*time.Millisecond))},
		{"acme", "RM_missing_observed_end", "observed", new(at(4 * time.Hour)), nil},
		{"acme", "RM_missing_inferred_end", "inferred", new(at(5 * time.Hour)), nil},
		{"acme", "RM_negative_observed", "observed", new(at(6 * time.Hour)), new(at(6*time.Hour - time.Second))},
		{"acme", "RM_negative_inferred", "inferred", new(at(7 * time.Hour)), new(at(7*time.Hour - time.Second))},
		{"acme", "RM_older_unmeasured", "observed", new(from.Add(-time.Hour)), nil},
		{"acme", "RM_older_open", "open", new(from.Add(-time.Hour)), nil},
		{"acme", "RM_no_start", "observed", nil, new(at(3 * time.Hour))},
		{"acme", "RM_after", "observed", new(to.Add(-time.Minute)), new(to.Add(time.Minute))},
		{"acme", "RM_open", "open", new(at(2 * time.Hour)), nil},
		{"other", "RM_elsewhere", "observed", new(at(0)), new(at(600 * time.Second))},
	} {
		endedAt, lastActivity := c.end, c.end
		// Distinct clocks ensure usage selects ended_at for observed calls and
		// last_activity_at for inferred calls, including missing and reversed ends.
		switch c.state {
		case "observed":
			lastActivity = new(at(12 * time.Hour))
		case "inferred":
			endedAt = new(at(12 * time.Hour))
		}
		if _, err := f.pool.Exec(context.Background(),
			`INSERT INTO calls (organisation_id, project_id, room_sid, room_name, started_at, ended_at, last_activity_at, end_state) VALUES ($1, 'p', $2, 'r', $3, $4, $5, $6)`,
			c.org, c.sid, c.start, endedAt, lastActivity, c.state); err != nil {
			t.Fatal(err)
		}
	}
	// addProjectCall's own calls started now, with no end: open, and after the period.
	status, u := w.usage("acme", from, to)
	if status != http.StatusOK || u.OrganisationID != "acme" || !u.From.Equal(from) || !u.To.Equal(to) ||
		u.CallSeconds != 120 || u.Calls != 2 || u.InferredCalls != 1 || u.InferredCallSeconds != 29 || u.UnendedCalls != 1 || u.UnmeasuredCalls != 4 {
		t.Errorf("getOrganisationUsage = %d %+v, want callSeconds 120, calls 2, inferredCalls 1, inferredCallSeconds 29, unendedCalls 1, unmeasuredCalls 4", status, u)
	}
	if status, u := w.usage("acme", to, to.Add(24*time.Hour)); status != http.StatusOK || u.CallSeconds != 120 || u.Calls != 1 || u.UnendedCalls != 0 {
		t.Errorf("the next period = %d %+v, want the call that ended in it, 120 s", status, u)
	}
	for name, period := range map[string][2]time.Time{
		"63 days":        {from, from.Add(63 * 24 * time.Hour)},
		"to before from": {to, from},
		"to equal from":  {from, from},
	} {
		if status, _ := w.usage("acme", period[0], period[1]); status != http.StatusBadRequest {
			t.Errorf("%s = %d, want 400", name, status)
		}
	}
	for _, q := range []string{"", "?from=2026-10-01&to=2026-10-02", "?from=2026-10-01T00:00:00Z"} {
		if status, body := f.send(w.h, call{method: http.MethodGet, path: instanceOrg("acme") + "/usage" + q, token: instanceToken}); status != http.StatusBadRequest || !strings.Contains(string(body), `"invalid_request"`) {
			t.Errorf("usage%s = %d %s, want 400 invalid_request", q, status, body)
		}
	}
	if status, _ := w.usage("nobody", from, to); status != http.StatusNotFound {
		t.Errorf("usage of an unknown organisation = %d, want 404", status)
	}
}

// instanceSecrets runs checks 1 to 7's flows for TestNoSecretInLogs, without an SMTP sink, and
// returns the credentials they used, none of which may reach a log line.
func instanceSecrets(t *testing.T, f *fixture) []string {
	t.Helper()
	off := f.web(nil)
	f.send(off.h, call{method: http.MethodGet, path: instanceOrg("acme"), token: instanceToken})
	w := f.webWith(nil, instanceToken)
	w.put("acme", "Acme")
	w.organisation("acme")
	w.usage("acme", time.Date(2026, 10, 1, 0, 0, 0, 0, time.UTC), time.Date(2026, 11, 1, 0, 0, 0, 0, time.UTC))
	f.send(w.h, call{method: http.MethodGet, path: instanceOrg("acme"), token: strings.Repeat("x", len(instanceToken))})
	f.send(w.h, call{method: http.MethodGet, path: "/api/v1/workspaces", token: instanceToken})
	_, c := w.instanceInvite("acme", "first@example.com", "Admin", "checkout-1")
	if c.Link == nil {
		t.Fatal("no link from the instance API without SMTP")
	}
	link := *c.Link
	w.instanceInvite("acme", "first@example.com", "Admin", "checkout-1")
	w.instanceInvite("acme", "other@example.com", "Admin", "checkout-1")
	res, _ := w.accept(tokenOf(link), newPassword, request{tls: true})
	joined := sessionCookie(res)
	if joined == nil {
		t.Fatal("the instance API's link did not sign the admin in")
	}
	failing := f.webWith(unreachableMailer(t), instanceToken)
	failing.instanceInvite("acme", "unsent@example.com", "Member", "checkout-2")
	if logs := f.logs.String(); !strings.Contains(logs, `"instance api"`) || !strings.Contains(logs, `"createOrganisationInvitation"`) {
		t.Error("the instance API did not log its calls")
	}
	return []string{instanceToken, link, tokenOf(link), joined.Value}
}
