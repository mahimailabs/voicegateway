package auth_test

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"regexp"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/testcontainers/testcontainers-go"
	"github.com/testcontainers/testcontainers-go/wait"

	"github.com/mahimailabs/voicegateway/internal/api"
	"github.com/mahimailabs/voicegateway/internal/app"
	"github.com/mahimailabs/voicegateway/internal/auth"
	"github.com/mahimailabs/voicegateway/internal/invite"
	"github.com/mahimailabs/voicegateway/internal/mailer"
)

// mailpitImage is the SMTP server the email checks send to, pinned by tag and digest.
const mailpitImage = "axllent/mailpit:v1.31.4@sha256:b68349e3a014b90c5610bfb26b2ae36f3892d7b8cf25ee140c6c71c98d2fcf48"

// newPassword is long enough for a link: exactly 12 characters.
const newPassword = "twelve chars"

// linkPattern is a link as the API answers it without SMTP, for a request to voicegateway.example
// over TLS.
var linkPattern = regexp.MustCompile(`^https://voicegateway\.example/invite#[A-Za-z0-9_-]{43}$`)

// inviteWeb is web's /auth and /api/v1 routes with invitations wired, behind web's cross-origin
// protection, over the fixture's Postgres.
type inviteWeb struct {
	f     *fixture
	s     *auth.Service
	links *invite.Service
	h     http.Handler
}

// web returns web with m as its mailer; nil is no SMTP.
func (f *fixture) web(m invite.Mailer) *inviteWeb {
	f.t.Helper()
	s := f.service()
	links := &invite.Service{Store: f.pg, Hash: auth.HashPassword, Verify: auth.VerifyPassword, Log: s.Log, Now: time.Now}
	if m != nil {
		// Web refuses SMTP without VOICEGATEWAY_PUBLIC_URL, so emailed links never take the Host.
		links.Mailer = m
		s.PublicURL = "https://voicegateway.example"
	}
	s.Links = links
	apiRoutes := (&api.API{
		Projects: f.pg, Calls: f.pg, Traces: emptyTraces{}, Layers: quietLayers{}, Endpoints: f.pg, Accounts: f.pg, Keys: f.pg, NewKey: auth.NewKey,
		Invitations: f.pg, Inviter: app.NewInviter(links), LinkBase: s.LinkBase,
		Log: s.Log, Now: time.Now, Gate: s.Gate,
	}).Routes()
	mux := http.NewServeMux()
	mux.Handle("/auth/", s.Routes())
	mux.Handle("/api/v1/", apiRoutes)
	return &inviteWeb{f: f, s: s, links: links, h: http.NewCrossOriginProtection().Handler(mux)}
}

// admin signs in an account made by addUser.
func (w *inviteWeb) signIn(email string) *http.Cookie {
	w.f.t.Helper()
	return w.f.signedIn(w.s, email)
}

type createdInvite struct {
	Invitation struct {
		ID, Kind, Email, State string
		Role                   *string
		ExpiresAt              time.Time
		InvitedBy              *string
	}
	Link    *string
	Emailed bool
}

// invite calls createInvitation and returns its status and answer.
func (w *inviteWeb) invite(admin *http.Cookie, org, email, role string) (int, createdInvite) {
	w.f.t.Helper()
	status, body := w.f.send(w.h, call{method: http.MethodPost, path: "/api/v1/org/" + org + "/invitations", body: fmt.Sprintf(`{"email":%q,"role":%q}`, email, role), cookie: admin})
	var c createdInvite
	if status == http.StatusCreated {
		if err := json.Unmarshal(body, &c); err != nil {
			w.f.t.Fatal(err)
		}
	}
	return status, c
}

// link invites and returns the link, which needs SMTP unset.
func (w *inviteWeb) link(admin *http.Cookie, org, email, role string) string {
	w.f.t.Helper()
	status, c := w.invite(admin, org, email, role)
	if status != http.StatusCreated || c.Link == nil {
		w.f.t.Fatalf("createInvitation %s = %d, link %v", email, status, c.Link)
	}
	return *c.Link
}

type listedInvitation struct {
	ID, Kind, Email, State string
	Role                   *string
	CreatedAt, ExpiresAt   time.Time
	InvitedBy              *string
}

func (w *inviteWeb) list(admin *http.Cookie, org string) []listedInvitation {
	w.f.t.Helper()
	status, body := w.f.send(w.h, call{method: http.MethodGet, path: "/api/v1/org/" + org + "/invitations", cookie: admin})
	var out struct{ Items []listedInvitation }
	if err := json.Unmarshal(body, &out); err != nil || status != http.StatusOK {
		w.f.t.Fatalf("getInvitations = %d %s", status, body)
	}
	return out.Items
}

func tokenOf(link string) string {
	_, token, _ := strings.Cut(link, "#")
	return token
}

// look calls GET /auth/invitation with a link's token.
func (w *inviteWeb) look(token string) (*http.Response, string) {
	w.f.t.Helper()
	res := w.f.do(w.h, http.MethodGet, "/auth/invitation", "", request{tls: true, headers: map[string]string{"X-Invitation-Token": token}})
	b, _ := io.ReadAll(res.Body)
	return res, string(b)
}

// accept calls POST /auth/invitation/accept.
func (w *inviteWeb) accept(token, pw string, rq request) (*http.Response, string) {
	w.f.t.Helper()
	b, _ := json.Marshal(map[string]string{"token": token, "password": pw})
	res := w.f.do(w.h, http.MethodPost, "/auth/invitation/accept", string(b), rq)
	body, _ := io.ReadAll(res.Body)
	return res, string(body)
}

func (w *inviteWeb) session(c *http.Cookie) (int, map[string]string) {
	w.f.t.Helper()
	res := w.f.do(w.h, http.MethodGet, "/auth/session", "", request{tls: true, cookies: []*http.Cookie{c}})
	var s struct {
		Organisations []struct{ ID, Role string }
	}
	_ = json.NewDecoder(res.Body).Decode(&s)
	roles := map[string]string{}
	for _, o := range s.Organisations {
		roles[o.ID] = o.Role
	}
	return res.StatusCode, roles
}

func (f *fixture) count(query string, args ...any) int {
	f.t.Helper()
	var n int
	if err := f.pool.QueryRow(context.Background(), query, args...).Scan(&n); err != nil {
		f.t.Fatal(err)
	}
	return n
}

func randomToken(t *testing.T) string {
	t.Helper()
	b := make([]byte, 32)
	if _, err := rand.Read(b); err != nil {
		t.Fatal(err)
	}
	return base64.RawURLEncoding.EncodeToString(b)
}

// Acceptance checks 1 and 16.
func TestCreateInvitation(t *testing.T) {
	f := newFixture(t)
	f.addUser("admin@example.com", "org-a", "admin")
	w := f.web(nil)
	admin := w.signIn("admin@example.com")
	status, c := w.invite(admin, "org-a", "new@example.com", "Viewer")
	if status != http.StatusCreated || c.Link == nil || !linkPattern.MatchString(*c.Link) || c.Emailed {
		t.Fatalf("createInvitation = %d, link %v, emailed %v; want 201, a link and emailed false", status, c.Link, c.Emailed)
	}
	link, token := *c.Link, tokenOf(*c.Link)
	if c.Invitation.InvitedBy == nil || *c.Invitation.InvitedBy != "admin@example.com" {
		t.Errorf("invitedBy = %v, want the admin's email", c.Invitation.InvitedBy)
	}
	items := w.list(admin, "org-a")
	if len(items) != 1 || items[0].State != "pending" || items[0].Email != "new@example.com" || items[0].Role == nil || *items[0].Role != "Viewer" {
		t.Fatalf("getInvitations = %+v, want one pending Viewer invitation", items)
	}
	if want := time.Now().Add(7 * 24 * time.Hour); items[0].ExpiresAt.Sub(want).Abs() > time.Minute {
		t.Errorf("expiresAt = %v, want 7 days from now (%v)", items[0].ExpiresAt, want)
	}
	sum := sha256.Sum256([]byte(token))
	if n := f.count(`SELECT count(*) FROM invitations WHERE token_sha256 = $1`, sum[:]); n != 1 {
		t.Errorf("%d rows hold the token's SHA-256, want 1", n)
	}
	if n := f.count(`SELECT count(*) FROM invitations i WHERE position($1 in row_to_json(i)::text) > 0 OR position($1::bytea in token_sha256) > 0`, token); n != 0 {
		t.Errorf("%d rows hold the token itself", n)
	}
	for _, secret := range []string{token, link} {
		if strings.Contains(f.logs.String(), secret) {
			t.Errorf("a log line holds %q", secret)
		}
	}
}

// Acceptance check 2.
func TestParallelCreatesOneOpenLink(t *testing.T) {
	f := newFixture(t)
	f.addUser("admin@example.com", "org-a", "admin")
	w := f.web(nil)
	admin := w.signIn("admin@example.com")
	links := make(chan string, 10)
	var wg sync.WaitGroup
	for range 10 {
		wg.Go(func() {
			status, c := w.invite(admin, "org-a", "new@example.com", "Member")
			if status != http.StatusCreated || c.Link == nil {
				t.Errorf("createInvitation = %d", status)
				return
			}
			links <- *c.Link
		})
	}
	wg.Wait()
	close(links)
	if n := f.count(`SELECT count(*) FROM invitations WHERE accepted_at IS NULL AND revoked_at IS NULL`); n != 1 {
		t.Fatalf("%d open links, want exactly 1", n)
	}
	var stored []byte
	if err := f.pool.QueryRow(context.Background(), `SELECT token_sha256 FROM invitations WHERE revoked_at IS NULL`).Scan(&stored); err != nil {
		t.Fatal(err)
	}
	working := 0
	for link := range links {
		res, _ := w.look(tokenOf(link))
		sum := sha256.Sum256([]byte(tokenOf(link)))
		switch {
		case res.StatusCode == http.StatusOK && string(sum[:]) == string(stored):
			working++
		case res.StatusCode == http.StatusNotFound:
		default:
			t.Errorf("a link answers %d", res.StatusCode)
		}
	}
	if working != 1 {
		t.Errorf("%d links work, want exactly the stored one", working)
	}
}

// Acceptance check 3.
func TestCreateIdempotentRetry(t *testing.T) {
	f := newFixture(t)
	f.addUser("admin@example.com", "org-a", "admin")
	w := f.web(nil)
	ctx := context.Background()
	in := invite.Invite{OrganisationID: "org-a", Email: "first@example.com", Role: "admin", BaseURL: "https://vg.example", IdempotencyKey: "workspace-42"}
	first, err := w.links.Create(ctx, in)
	if err != nil || first.Link == "" {
		t.Fatalf("first Create: %v, link %q", err, first.Link)
	}
	again, err := w.links.Create(ctx, in)
	if err != nil {
		t.Fatal(err)
	}
	if again.Invitation.ID != first.Invitation.ID || again.Link != "" || again.Emailed != first.Emailed {
		t.Errorf("repeat = id %s, link %q, emailed %v; want id %s, no link, emailed %v", again.Invitation.ID, again.Link, again.Emailed, first.Invitation.ID, first.Emailed)
	}
	if res, _ := w.look(tokenOf(first.Link)); res.StatusCode != http.StatusOK {
		t.Errorf("the first link after a repeat = %d, want 200", res.StatusCode)
	}
	other := in
	other.Email = "second@example.com"
	if _, err := w.links.Create(ctx, other); !errors.Is(err, invite.ErrInvalid) {
		t.Errorf("the same key with another email: %v, want ErrInvalid", err)
	}
	noBase := in
	noBase.IdempotencyKey, noBase.BaseURL = "", ""
	if _, err := w.links.Create(ctx, noBase); !errors.Is(err, invite.ErrInvalid) {
		t.Errorf("an empty BaseURL: %v, want ErrInvalid", err)
	}
}

// Acceptance check 4.
func TestAcceptInvitationOnce(t *testing.T) {
	f := newFixture(t)
	f.addUser("admin@example.com", "org-a", "admin")
	w := f.web(nil)
	admin := w.signIn("admin@example.com")
	token := tokenOf(w.link(admin, "org-a", "new@example.com", "Viewer"))
	before := w.signIn("admin@example.com") // the browser's session before the accept
	res, _ := w.accept(token, newPassword, request{tls: true, cookies: []*http.Cookie{before}})
	c := sessionCookie(res)
	if res.StatusCode != http.StatusNoContent || c == nil || c.Value == before.Value {
		t.Fatalf("accept = %d, cookie %v; want 204 with a new session", res.StatusCode, c)
	}
	if status, roles := w.session(c); status != http.StatusOK || len(roles) != 1 || roles["org-a"] != "viewer" {
		t.Errorf("session = %d %v, want org-a as viewer", status, roles)
	}
	if items := w.list(admin, "org-a"); len(items) != 1 || items[0].State != "accepted" {
		t.Errorf("getInvitations = %+v, want it accepted", items)
	}
	if res, body := w.accept(token, newPassword, request{tls: true}); res.StatusCode != http.StatusNotFound || !strings.Contains(body, "invitation_invalid") {
		t.Errorf("second accept = %d %s, want 404 invitation_invalid", res.StatusCode, body)
	}
	fresh := tokenOf(w.link(admin, "org-a", "parallel@example.com", "Member"))
	statuses := make(chan int, 10)
	var wg sync.WaitGroup
	for range 10 {
		wg.Go(func() {
			res, _ := w.accept(fresh, newPassword, request{tls: true})
			statuses <- res.StatusCode
		})
	}
	wg.Wait()
	close(statuses)
	got := map[int]int{}
	for s := range statuses {
		got[s]++
	}
	if got[http.StatusNoContent] != 1 || got[http.StatusNotFound] != 9 {
		t.Errorf("10 parallel accepts = %v, want 1 success and 9 404s", got)
	}
}

// Acceptance check 5.
func TestAcceptInvitationExistingAccount(t *testing.T) {
	f := newFixture(t)
	f.addUser("admin@example.com", "org-a", "admin")
	f.addUser("bob@example.com", "org-b", "member")
	w := f.web(nil)
	admin := w.signIn("admin@example.com")
	token := tokenOf(w.link(admin, "org-a", "bob@example.com", "Member"))
	newcomer := tokenOf(w.link(admin, "org-a", "nobody-yet@example.com", "Member"))
	fields := func(token string) []string {
		res, body := w.look(token)
		var m map[string]any
		if err := json.Unmarshal([]byte(body), &m); err != nil || res.StatusCode != http.StatusOK {
			t.Fatalf("GET /auth/invitation = %d %s", res.StatusCode, body)
		}
		var keys []string
		for k := range m {
			keys = append(keys, k)
		}
		return keys
	}
	if a, b := fields(token), fields(newcomer); len(a) != len(b) || len(a) != 4 {
		t.Errorf("fields for an existing account %v and a new email %v differ", a, b)
	}

	// Wrong tries through accept and sign-in, mixed, count together for bob's email.
	for i := range 10 {
		var res *http.Response
		if i%2 == 0 {
			res, _ = w.accept(token, "a wrong password", request{tls: true})
		} else {
			res = f.do(w.h, http.MethodPost, "/auth/sign-in", signIn("bob@example.com", "a wrong password"), request{tls: true})
		}
		if res.StatusCode != http.StatusUnauthorized {
			t.Fatalf("wrong try %d = %d, want 401", i+1, res.StatusCode)
		}
	}
	if f.role("bob@example.com", "org-a") != "" {
		t.Fatal("a wrong password added bob to org-a")
	}
	if res, _ := w.accept(token, password, request{tls: true}); res.StatusCode != http.StatusTooManyRequests {
		t.Errorf("11th try through accept = %d, want 429", res.StatusCode)
	}
	if res := f.do(w.h, http.MethodPost, "/auth/sign-in", signIn("bob@example.com", password), request{tls: true}); res.StatusCode != http.StatusTooManyRequests {
		t.Errorf("11th try through sign-in = %d, want 429", res.StatusCode)
	}
	if _, err := f.pool.Exec(context.Background(), `DELETE FROM sign_in_attempts`); err != nil {
		t.Fatal(err)
	}
	res, _ := w.accept(token, password, request{tls: true})
	if res.StatusCode != http.StatusNoContent {
		t.Fatalf("accept with bob's password = %d, want 204", res.StatusCode)
	}
	if f.role("bob@example.com", "org-a") != "member" || f.role("bob@example.com", "org-b") != "member" {
		t.Error("bob does not belong to both organisations")
	}
	if n := f.count(`SELECT count(*) FROM users WHERE email = 'bob@example.com'`); n != 1 {
		t.Errorf("%d accounts for bob, want 1", n)
	}

	// An invite for someone who joined A since: used up, their role kept, signed in.
	f.addUser("carol@example.com", "org-c", "member")
	carol := tokenOf(w.link(admin, "org-a", "carol@example.com", "Admin"))
	if _, err := f.pg.AddMembership(context.Background(), "carol@example.com", "org-a", "viewer"); err != nil {
		t.Fatal(err)
	}
	res, _ = w.accept(carol, password, request{tls: true})
	if res.StatusCode != http.StatusNoContent || sessionCookie(res) == nil {
		t.Fatalf("accept for a member = %d, want 204 and a session", res.StatusCode)
	}
	if got := f.role("carol@example.com", "org-a"); got != "viewer" {
		t.Errorf("carol's role in org-a = %q, want viewer kept", got)
	}
	if res, _ := w.look(carol); res.StatusCode != http.StatusNotFound {
		t.Errorf("carol's link after use = %d, want 404", res.StatusCode)
	}
}

// Acceptance check 6.
func TestInvitationInvalidLooksAlike(t *testing.T) {
	f := newFixture(t)
	f.addUser("admin@example.com", "org-a", "admin")
	w := f.web(nil)
	admin := w.signIn("admin@example.com")
	w.links.Now = func() time.Time { return time.Now().Add(-8 * 24 * time.Hour) }
	expired := tokenOf(w.link(admin, "org-a", "expired@example.com", "Member"))
	w.links.Now = time.Now
	revoked := w.link(admin, "org-a", "revoked@example.com", "Member")
	for _, i := range w.list(admin, "org-a") {
		if i.Email == "revoked@example.com" {
			if status, _ := f.send(w.h, call{method: http.MethodDelete, path: "/api/v1/org/org-a/invitations/" + i.ID, cookie: admin}); status != http.StatusNoContent {
				t.Fatalf("revoke = %d", status)
			}
		}
	}
	replaced := tokenOf(w.link(admin, "org-a", "replaced@example.com", "Member"))
	w.link(admin, "org-a", "replaced@example.com", "Member")
	accepted := tokenOf(w.link(admin, "org-a", "accepted@example.com", "Member"))
	if res, _ := w.accept(accepted, newPassword, request{tls: true}); res.StatusCode != http.StatusNoContent {
		t.Fatalf("accept = %d", res.StatusCode)
	}
	cases := map[string]string{"expired": expired, "revoked": tokenOf(revoked), "replaced": replaced, "accepted": accepted, "random": randomToken(t)}
	var lookBody, acceptBody string
	for name, token := range cases {
		res, body := w.look(token)
		if res.StatusCode != http.StatusNotFound || !strings.Contains(body, `"invitation_invalid"`) {
			t.Errorf("%s: GET = %d %s, want 404 invitation_invalid", name, res.StatusCode, body)
		}
		if lookBody != "" && body != lookBody {
			t.Errorf("%s: GET body %q differs from %q", name, body, lookBody)
		}
		lookBody = body
		res, body = w.accept(token, newPassword, request{tls: true})
		if res.StatusCode != http.StatusNotFound || !strings.Contains(body, `"invitation_invalid"`) {
			t.Errorf("%s: accept = %d %s, want 404 invitation_invalid", name, res.StatusCode, body)
		}
		if acceptBody != "" && body != acceptBody {
			t.Errorf("%s: accept body %q differs from %q", name, body, acceptBody)
		}
		acceptBody = body
	}
}

// Acceptance check 7.
func TestInvitationRules(t *testing.T) {
	f := newFixture(t)
	f.addUser("admin@example.com", "org-a", "admin")
	f.addUser("member@example.com", "org-a", "member")
	f.addUser("viewer@example.com", "org-a", "viewer")
	f.addUser("admin-b@example.com", "org-b", "admin")
	w := f.web(nil)
	admin := w.signIn("admin@example.com")
	org := "/api/v1/org/org-a/invitations"

	for _, body := range []string{`{"email":"not-an-email","role":"Viewer"}`, `{"email":"Ada <ada@example.com>","role":"Viewer"}`, `{"email":"new@example.com","role":"Owner"}`, `{"email":"new@example.com"}`} {
		if status, _ := f.send(w.h, call{method: http.MethodPost, path: org, body: body, cookie: admin}); status != http.StatusBadRequest {
			t.Errorf("createInvitation %s = %d, want 400", body, status)
		}
	}
	if status, body := f.send(w.h, call{method: http.MethodPost, path: org, body: `{"email":"Member@Example.com","role":"Viewer"}`, cookie: admin}); status != http.StatusConflict || !strings.Contains(string(body), "already_member") {
		t.Errorf("inviting a member = %d %s, want 409 already_member", status, body)
	}
	token := tokenOf(w.link(admin, "org-a", "new@example.com", "Viewer"))
	if res, _ := w.accept(token, "eleven char", request{tls: true}); res.StatusCode != http.StatusBadRequest {
		t.Errorf("an 11-character password = %d, want 400", res.StatusCode)
	}
	if res, body := w.accept(token, newPassword, request{from: "10.0.0.5"}); res.StatusCode != http.StatusBadRequest || !strings.Contains(body, "https_required") {
		t.Errorf("plain HTTP away from localhost = %d %s, want 400 https_required", res.StatusCode, body)
	}
	if res, _ := w.accept(token, newPassword, request{tls: true, headers: map[string]string{"Origin": "https://elsewhere.example", "Sec-Fetch-Site": "cross-site"}}); res.StatusCode != http.StatusForbidden {
		t.Errorf("an accept from another site = %d, want 403", res.StatusCode)
	}
	if n := f.count(`SELECT count(*) FROM users WHERE email = 'new@example.com'`); n != 0 {
		t.Error("a refused cross-site accept made an account")
	}
	id := w.list(admin, "org-a")[0].ID
	for i := range 2 {
		if status, _ := f.send(w.h, call{method: http.MethodDelete, path: org + "/" + id, cookie: admin}); status != http.StatusNoContent {
			t.Errorf("revoke %d = %d, want 204", i+1, status)
		}
	}
	w.link(w.signIn("admin-b@example.com"), "org-b", "b-invite@example.com", "Viewer")
	bID := w.list(w.signIn("admin-b@example.com"), "org-b")[0].ID
	if status, _ := f.send(w.h, call{method: http.MethodDelete, path: org + "/" + bID, cookie: admin}); status != http.StatusNotFound {
		t.Errorf("revoking another organisation's invitation = %d, want 404", status)
	}
	if status, _ := f.send(w.h, call{method: http.MethodDelete, path: org + "/not-a-uuid", cookie: admin}); status != http.StatusNotFound {
		t.Errorf("revoking a malformed id = %d, want 404", status)
	}
	memberID := f.userID("member@example.com")
	for _, who := range []string{"member@example.com", "viewer@example.com"} {
		c := w.signIn(who)
		for _, op := range []call{
			{method: http.MethodGet, path: org},
			{method: http.MethodPost, path: org, body: `{"email":"x@example.com","role":"Viewer"}`},
			{method: http.MethodDelete, path: org + "/" + id},
			{method: http.MethodPost, path: "/api/v1/org/org-a/members/" + memberID + "/password-reset"},
		} {
			op.cookie = c
			if status, _ := f.send(w.h, op); status != http.StatusForbidden {
				t.Errorf("%s %s %s = %d, want 403", who, op.method, op.path, status)
			}
		}
	}
}

// Acceptance check 8.
func TestParallelAcceptsOneAccount(t *testing.T) {
	f := newFixture(t)
	f.addUser("admin-a@example.com", "org-a", "admin")
	f.addUser("admin-b@example.com", "org-b", "admin")
	w := f.web(nil)
	tokens := []string{
		tokenOf(w.link(w.signIn("admin-a@example.com"), "org-a", "dup@example.com", "Member")),
		tokenOf(w.link(w.signIn("admin-b@example.com"), "org-b", "dup@example.com", "Viewer")),
	}
	statuses := make([]int, 2)
	var wg sync.WaitGroup
	for i, token := range tokens {
		wg.Go(func() {
			res, _ := w.accept(token, newPassword, request{tls: true})
			statuses[i] = res.StatusCode
		})
	}
	wg.Wait()
	for i, s := range statuses {
		if s != http.StatusNoContent && s != http.StatusNotFound {
			t.Errorf("accept %d = %d, want 204 or 404, never 500", i+1, s)
		}
	}
	if n := f.count(`SELECT count(*) FROM users WHERE email = 'dup@example.com'`); n != 1 {
		t.Errorf("%d accounts for dup@example.com, want 1", n)
	}
}

// resetLink calls createPasswordReset and returns its status and answer.
func (w *inviteWeb) resetLink(admin *http.Cookie, org, userID string) (int, *string, bool, []byte) {
	w.f.t.Helper()
	status, body := w.f.send(w.h, call{method: http.MethodPost, path: "/api/v1/org/" + org + "/members/" + userID + "/password-reset", cookie: admin})
	var out struct {
		Link    *string
		Emailed bool
	}
	_ = json.Unmarshal(body, &out)
	return status, out.Link, out.Emailed, body
}

// Acceptance check 9.
func TestPasswordResetLink(t *testing.T) {
	f := newFixture(t)
	f.addUser("admin@example.com", "org-a", "admin")
	f.addUser("member@example.com", "org-a", "member")
	f.addUser("outsider@example.com", "org-b", "member")
	w := f.web(nil)
	admin := w.signIn("admin@example.com")
	one, two := w.signIn("member@example.com"), w.signIn("member@example.com")
	versionBefore := f.count(`SELECT password_version FROM users WHERE email = 'member@example.com'`)
	status, link, emailed, body := w.resetLink(admin, "org-a", f.userID("member@example.com"))
	if status != http.StatusCreated || link == nil || !linkPattern.MatchString(*link) || emailed {
		t.Fatalf("createPasswordReset = %d %s", status, body)
	}
	if res, body := w.look(tokenOf(*link)); res.StatusCode != http.StatusOK || !strings.Contains(body, `"kind":"reset"`) || !strings.Contains(body, `"role":null`) {
		t.Errorf("GET /auth/invitation for a reset = %d %s", res.StatusCode, body)
	}
	const fresh = "a new password for the member"
	res, _ := w.accept(tokenOf(*link), fresh, request{tls: true})
	if res.StatusCode != http.StatusNoContent {
		t.Fatalf("accept the reset = %d", res.StatusCode)
	}
	for i, c := range []*http.Cookie{one, two} {
		if status, _ := w.session(c); status != http.StatusUnauthorized {
			t.Errorf("old session %d = %d, want 401", i+1, status)
		}
	}
	if res := f.do(w.h, http.MethodPost, "/auth/sign-in", signIn("member@example.com", fresh), request{tls: true}); res.StatusCode != http.StatusNoContent {
		t.Errorf("the new password = %d, want 204", res.StatusCode)
	}
	if v := f.count(`SELECT password_version FROM users WHERE email = 'member@example.com'`); v != versionBefore+1 {
		t.Errorf("password_version = %d, want %d", v, versionBefore+1)
	}
	if res, _ := w.accept(tokenOf(*link), fresh+" again", request{tls: true}); res.StatusCode != http.StatusNotFound {
		t.Errorf("the used reset link = %d, want 404", res.StatusCode)
	}
	if status, _, _, _ := w.resetLink(admin, "org-a", f.userID("outsider@example.com")); status != http.StatusNotFound {
		t.Errorf("a reset for a user outside org-a = %d, want 404", status)
	}
}

// Acceptance check 10.
func TestResetRefusedAcrossOrganisations(t *testing.T) {
	f := newFixture(t)
	f.addUser("admin-a@example.com", "org-a", "admin")
	f.addUser("admin-b@example.com", "org-b", "admin")
	f.addUser("both@example.com", "org-a", "member")
	if _, err := f.pg.AddMembership(context.Background(), "both@example.com", "org-b", "viewer"); err != nil {
		t.Fatal(err)
	}
	w := f.web(nil)
	member := f.userID("both@example.com")
	status, _, _, body := w.resetLink(w.signIn("admin-a@example.com"), "org-a", member)
	if status != http.StatusConflict || !strings.Contains(string(body), "member_of_other_organisations") {
		t.Errorf("reset by an admin of A only = %d %s, want 409 member_of_other_organisations", status, body)
	}
	if n := f.count(`SELECT count(*) FROM invitations WHERE kind = 'reset'`); n != 0 {
		t.Errorf("%d reset links stored after a refusal, want 0", n)
	}
	if _, err := f.pg.AddMembership(context.Background(), "admin-a@example.com", "org-b", "admin"); err != nil {
		t.Fatal(err)
	}
	if status, link, _, body := w.resetLink(w.signIn("admin-a@example.com"), "org-a", member); status != http.StatusCreated || link == nil {
		t.Errorf("reset by an admin of A and B = %d %s, want 201 with a link", status, body)
	}

	// The guard runs again at use: a member who joins B after the link was made keeps their
	// password, and the link is revoked.
	f.addUser("later@example.com", "org-a", "member")
	_, link, _, _ := w.resetLink(w.signIn("admin-b@example.com"), "org-a", f.userID("later@example.com"))
	if link != nil {
		t.Fatal("an admin of B alone made a reset link for a member of A")
	}
	f.addUser("only-a@example.com", "org-c", "admin")
	if _, err := f.pg.AddMembership(context.Background(), "only-a@example.com", "org-a", "admin"); err != nil {
		t.Fatal(err)
	}
	if _, err := f.pool.Exec(context.Background(), `DELETE FROM memberships WHERE user_id = (SELECT id FROM users WHERE email = 'only-a@example.com') AND organisation_id = 'org-c'`); err != nil {
		t.Fatal(err)
	}
	_, link, _, _ = w.resetLink(w.signIn("only-a@example.com"), "org-a", f.userID("later@example.com"))
	if link == nil {
		t.Fatal("no reset link for a member of A only")
	}
	if _, err := f.pg.AddMembership(context.Background(), "later@example.com", "org-b", "viewer"); err != nil {
		t.Fatal(err)
	}
	if res, _ := w.accept(tokenOf(*link), "a takeover password", request{tls: true}); res.StatusCode != http.StatusNotFound {
		t.Errorf("a reset link used after the member joined B = %d, want 404", res.StatusCode)
	}
	if res := f.do(w.h, http.MethodPost, "/auth/sign-in", signIn("later@example.com", password), request{tls: true}); res.StatusCode != http.StatusNoContent {
		t.Errorf("the member's own password after the refused reset = %d, want 204", res.StatusCode)
	}
	if n := f.count(`SELECT count(*) FROM invitations WHERE user_id = (SELECT id FROM users WHERE email = 'later@example.com') AND revoked_at IS NOT NULL AND accepted_at IS NULL`); n != 1 {
		t.Errorf("%d revoked reset links for the member, want the refused one", n)
	}
}

// mailpit is a running mailpit container.
type mailpit struct {
	c        testcontainers.Container
	smtpPort string
	api      string
}

func startMailpit(t *testing.T) *mailpit {
	t.Helper()
	ctx := context.Background()
	c, err := testcontainers.GenericContainer(ctx, testcontainers.GenericContainerRequest{
		ContainerRequest: testcontainers.ContainerRequest{
			Image:        mailpitImage,
			ExposedPorts: []string{"1025/tcp", "8025/tcp"},
			WaitingFor:   wait.ForHTTP("/livez").WithPort("8025/tcp"),
		},
		Started: true,
	})
	testcontainers.CleanupContainer(t, c)
	if err != nil {
		t.Fatal(err)
	}
	m := &mailpit{c: c}
	m.ports(t)
	return m
}

// ports reads the mapped ports, which change when the container restarts.
func (m *mailpit) ports(t *testing.T) {
	t.Helper()
	ctx := context.Background()
	smtp, err := m.c.MappedPort(ctx, "1025/tcp")
	if err != nil {
		t.Fatal(err)
	}
	web, err := m.c.MappedPort(ctx, "8025/tcp")
	if err != nil {
		t.Fatal(err)
	}
	m.smtpPort, m.api = smtp.Port(), "http://localhost:"+web.Port()
}

// smtpPassword is the SMTP password in the failing mailers' URLs; it must never reach a log.
// Mailpit runs without TLS, and go-mail never offers a password over plain SMTP, so the mailer that
// reaches it carries none.
const smtpPassword = "smtp-secret-password"

func (m *mailpit) mailer(t *testing.T) *mailer.Mailer {
	t.Helper()
	mm, err := mailer.New("smtp://localhost:"+m.smtpPort, "voicegateway@example.com")
	if err != nil {
		t.Fatal(err)
	}
	return mm
}

// downMailer is a mailer, with a password, for the port mailpit listened on before it stopped.
func (m *mailpit) downMailer(t *testing.T) *mailer.Mailer {
	t.Helper()
	mm, err := mailer.New("smtp://vg:"+smtpPassword+"@localhost:"+m.smtpPort, "voicegateway@example.com")
	if err != nil {
		t.Fatal(err)
	}
	return mm
}

// messages returns the text of every message mailpit holds for to.
func (m *mailpit) messages(t *testing.T, to string) []string {
	t.Helper()
	var list struct {
		Messages []struct {
			ID string
			To []struct{ Address string }
		}
	}
	m.get(t, "/api/v1/messages", &list)
	var out []string
	for _, msg := range list.Messages {
		for _, a := range msg.To {
			if a.Address == to {
				var full struct{ Text string }
				m.get(t, "/api/v1/message/"+msg.ID, &full)
				out = append(out, full.Text)
			}
		}
	}
	return out
}

func (m *mailpit) get(t *testing.T, path string, v any) {
	t.Helper()
	res, err := http.Get(m.api + path)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = res.Body.Close() }()
	if err := json.NewDecoder(res.Body).Decode(v); err != nil {
		t.Fatal(err)
	}
}

var mailedLink = regexp.MustCompile(`https://voicegateway\.example/invite#[A-Za-z0-9_-]{43}`)

// unreachableMailer is a mailer for a loopback port nothing listens on.
func unreachableMailer(t *testing.T) *mailer.Mailer {
	t.Helper()
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	addr := ln.Addr().String()
	_ = ln.Close()
	m, err := mailer.New("smtp://vg:"+smtpPassword+"@"+addr, "voicegateway@example.com")
	if err != nil {
		t.Fatal(err)
	}
	return m
}

// Acceptance check 11.
func TestInvitationEmail(t *testing.T) {
	f := newFixture(t)
	f.addUser("admin@example.com", "org-a", "admin")
	f.addUser("member@example.com", "org-a", "member")
	mp := startMailpit(t)
	w := f.web(mp.mailer(t))
	admin := w.signIn("admin@example.com")

	status, c := w.invite(admin, "org-a", "new@example.com", "Member")
	if status != http.StatusCreated || !c.Emailed || c.Link != nil {
		t.Fatalf("createInvitation with SMTP = %d, emailed %v, link %v; want 201, emailed, no link", status, c.Emailed, c.Link)
	}
	texts := mp.messages(t, "new@example.com")
	if len(texts) != 1 || !strings.Contains(texts[0], "org-a") || !mailedLink.MatchString(texts[0]) {
		t.Fatalf("mailpit holds %d messages for new@example.com: %q", len(texts), texts)
	}
	firstLink := mailedLink.FindString(texts[0])
	if res, _ := w.look(tokenOf(firstLink)); res.StatusCode != http.StatusOK {
		t.Errorf("the emailed link = %d, want 200", res.StatusCode)
	}

	ctx := context.Background()
	if err := mp.c.Stop(ctx, nil); err != nil {
		t.Fatal(err)
	}
	w.links.Mailer = mp.downMailer(t)
	status, c = w.invite(admin, "org-a", "while-down@example.com", "Viewer")
	if status != http.StatusCreated || c.Emailed || c.Link != nil {
		t.Errorf("createInvitation with mailpit stopped = %d, emailed %v, link %v; want 201, not emailed, no link", status, c.Emailed, c.Link)
	}
	stood := false
	for _, i := range w.list(admin, "org-a") {
		stood = stood || i.Email == "while-down@example.com" && i.State == "pending"
	}
	if !stood {
		t.Error("the invitation did not stand after a failed send")
	}
	logs := f.logs.String()
	if !strings.Contains(logs, `"class":"unreachable"`) {
		t.Errorf("the failed send did not log its class: %s", logs)
	}
	for _, secret := range []string{smtpPassword, firstLink, tokenOf(firstLink)} {
		if strings.Contains(logs, secret) {
			t.Errorf("a log line holds %q", secret)
		}
	}

	if err := mp.c.Start(ctx); err != nil {
		t.Fatal(err)
	}
	mp.ports(t)
	w.links.Mailer = mp.mailer(t)
	if status, c := w.invite(admin, "org-a", "new@example.com", "Member"); status != http.StatusCreated || !c.Emailed || c.Link != nil {
		t.Errorf("Resend = %d, emailed %v, link %v", status, c.Emailed, c.Link)
	}
	texts = mp.messages(t, "new@example.com")
	if len(texts) != 1 || !mailedLink.MatchString(texts[0]) || mailedLink.FindString(texts[0]) == firstLink {
		t.Errorf("after Resend mailpit holds %q, want one new link", texts)
	}
	if res, _ := w.look(tokenOf(firstLink)); res.StatusCode != http.StatusNotFound {
		t.Errorf("the first link after Resend = %d, want 404", res.StatusCode)
	}

	status, link, emailed, body := w.resetLink(admin, "org-a", f.userID("member@example.com"))
	if status != http.StatusCreated || !emailed || link != nil {
		t.Errorf("createPasswordReset with SMTP = %d %s, want emailed and no link", status, body)
	}
	if texts := mp.messages(t, "member@example.com"); len(texts) != 1 || !mailedLink.MatchString(texts[0]) {
		t.Errorf("mailpit holds %q for the member, want one reset link", texts)
	}
}

// Acceptance check 12.
func TestNoLinkWithSMTP(t *testing.T) {
	f := newFixture(t)
	f.addUser("admin@example.com", "org-a", "admin")
	f.addUser("member@example.com", "org-a", "member")
	mp := startMailpit(t)
	ctx := context.Background()
	for name, m := range map[string]*mailer.Mailer{"sent": mp.mailer(t), "failed": unreachableMailer(t)} {
		t.Run(name, func(t *testing.T) {
			w := f.web(m)
			admin := w.signIn("admin@example.com")
			if status, c := w.invite(admin, "org-a", name+"@example.com", "Viewer"); status != http.StatusCreated || c.Link != nil || c.Emailed != (name == "sent") {
				t.Errorf("createInvitation = %d, link %v, emailed %v", status, c.Link, c.Emailed)
			}
			if status, link, emailed, _ := w.resetLink(admin, "org-a", f.userID("member@example.com")); status != http.StatusCreated || link != nil || emailed != (name == "sent") {
				t.Errorf("createPasswordReset = %d, link %v, emailed %v", status, link, emailed)
			}
			c, err := w.links.Create(ctx, invite.Invite{OrganisationID: "org-a", Email: name + "-direct@example.com", Role: "member", BaseURL: "https://vg.example"})
			if err != nil || c.Link != "" {
				t.Errorf("Create = link %q, %v; want no link", c.Link, err)
			}
			c, err = w.links.ResetLink(ctx, "org-a", f.userID("member@example.com"), nil, "https://vg.example")
			if err != nil || c.Link != "" {
				t.Errorf("ResetLink = link %q, %v; want no link", c.Link, err)
			}
		})
	}
}

// Acceptance check 13.
func TestCreateWithoutInviter(t *testing.T) {
	f := newFixture(t)
	f.addUser("admin@example.com", "org-a", "admin")
	w := f.web(nil)
	c, err := w.links.Create(context.Background(), invite.Invite{OrganisationID: "org-a", Email: "first-admin@example.com", Role: "admin", BaseURL: "https://vg.example"})
	if err != nil {
		t.Fatal(err)
	}
	if n := f.count(`SELECT count(*) FROM invitations WHERE id = $1 AND invited_by IS NULL`, c.Invitation.ID); n != 1 {
		t.Error("invited_by is not null")
	}
	status, body := f.send(w.h, call{method: http.MethodGet, path: "/api/v1/org/org-a/invitations", cookie: w.signIn("admin@example.com")})
	if status != http.StatusOK || !strings.Contains(string(body), `"invitedBy":null`) {
		t.Errorf("getInvitations = %d %s, want invitedBy null", status, body)
	}
}

// Acceptance check 14, for the /auth/invitation endpoints; internal/app checks the /invite page.
func TestInvitationHeaders(t *testing.T) {
	f := newFixture(t)
	f.addUser("admin@example.com", "org-a", "admin")
	w := f.web(nil)
	token := tokenOf(w.link(w.signIn("admin@example.com"), "org-a", "new@example.com", "Viewer"))
	look, _ := w.look(token)
	missing, _ := w.look(randomToken(t))
	refused, _ := w.accept(randomToken(t), newPassword, request{tls: true})
	accepted, _ := w.accept(token, newPassword, request{tls: true})
	for name, res := range map[string]*http.Response{"GET": look, "GET invalid": missing, "accept refused": refused, "accept": accepted} {
		if res.Header.Get("Referrer-Policy") != "no-referrer" || res.Header.Get("Cache-Control") != "no-store" {
			t.Errorf("%s: Referrer-Policy %q, Cache-Control %q", name, res.Header.Get("Referrer-Policy"), res.Header.Get("Cache-Control"))
		}
	}
}
