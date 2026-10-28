package auth_test

import (
	"context"
	"crypto/rand"
	"crypto/rsa"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"io"
	"net/http"
	"net/http/cookiejar"
	"net/http/httptest"
	"net/url"
	"slices"
	"sort"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/coreos/go-oidc/v3/oidc"
	"github.com/coreos/go-oidc/v3/oidc/oidctest"

	"github.com/mahimailabs/voicegateway/internal/auth"
	"github.com/mahimailabs/voicegateway/internal/oauth"
)

// fakeProvider stands in for Google or GitHub at sign-in's seam: the flow against real providers
// is internal/oauth's tests. It answers the identity set on it and counts code exchanges.
type fakeProvider struct {
	mu        sync.Mutex
	who       oauth.Identity
	err       error
	exchanges atomic.Int64
	nonce     bool
}

func (p *fakeProvider) AuthCodeURL(state, _, nonce string) string {
	return "https://idp.example/authorize?" + url.Values{"state": {state}, "nonce": {nonce}}.Encode()
}

func (p *fakeProvider) UsesNonce() bool { return p.nonce }

func (p *fakeProvider) Identify(context.Context, string, string, string) (oauth.Identity, error) {
	p.exchanges.Add(1)
	p.mu.Lock()
	defer p.mu.Unlock()
	return p.who, p.err
}

func (p *fakeProvider) set(who oauth.Identity, err error) {
	p.mu.Lock()
	defer p.mu.Unlock()
	p.who, p.err = who, err
}

// socialWeb is web with invitations and a fake Google turned on.
func (f *fixture) socialWeb() (*inviteWeb, *fakeProvider) {
	f.t.Helper()
	w := f.web(nil)
	google := &fakeProvider{nonce: true}
	w.s.Providers = map[string]auth.Provider{"google": google}
	w.s.Identities = f.pg
	return w, google
}

// started is a provider sign-in in flight: the session cookie and the state it holds.
type started struct {
	cookie *http.Cookie
	state  string
}

// start calls GET /auth/oauth/google/start, with an invitation token when one is given.
func (w *inviteWeb) start(token string) started {
	w.f.t.Helper()
	rq := request{tls: true}
	const path = "/auth/oauth/google/start"
	if token != "" {
		rq.headers = map[string]string{"X-Invitation-Token": token}
	}
	res := w.f.do(w.h, http.MethodGet, path, "", rq)
	var body struct{ URL string }
	if err := json.NewDecoder(res.Body).Decode(&body); err != nil || res.StatusCode != http.StatusOK {
		w.f.t.Fatalf("start = %d, %v", res.StatusCode, err)
	}
	u, err := url.Parse(body.URL)
	if err != nil || u.Query().Get("state") == "" || u.Query().Get("nonce") == "" {
		w.f.t.Fatalf("start URL %q lacks a state or a nonce", body.URL)
	}
	return started{cookie: sessionCookie(res), state: u.Query().Get("state")}
}

// callback calls the callback with state, in st's session, and returns the response.
func (w *inviteWeb) callback(st started, state string) *http.Response {
	w.f.t.Helper()
	return w.f.do(w.h, http.MethodGet, "/auth/oauth/google/callback?"+url.Values{"state": {state}, "code": {"c0de"}}.Encode(), "",
		request{tls: true, cookies: []*http.Cookie{st.cookie}})
}

// signedInBy reports whether a callback signed someone in: a redirect to / with a new session.
func signedInBy(res *http.Response) (*http.Cookie, bool) {
	c := sessionCookie(res)
	return c, res.StatusCode == http.StatusSeeOther && res.Header.Get("Location") == "/" && c != nil
}

func refused(t *testing.T, res *http.Response) {
	t.Helper()
	if res.StatusCode != http.StatusSeeOther || res.Header.Get("Location") != "/sign-in?error=oauth_refused" {
		t.Fatalf("callback = %d to %q; want the oauth_refused redirect", res.StatusCode, res.Header.Get("Location"))
	}
}

// Acceptance check 1.
func TestProvidersOff(t *testing.T) {
	f := newFixture(t)
	w := f.web(nil)
	res := f.do(w.h, http.MethodGet, "/auth/providers", "", request{tls: true})
	b, _ := io.ReadAll(res.Body)
	if res.StatusCode != http.StatusOK || string(b) != `{"auth0":false,"github":false,"google":false}` {
		t.Fatalf("providers = %d %s", res.StatusCode, b)
	}
	for _, p := range []string{"google", "github", "auth0"} {
		if res := f.do(w.h, http.MethodGet, "/auth/oauth/"+p+"/start", "", request{tls: true}); res.StatusCode != http.StatusNotFound {
			t.Fatalf("%s start = %d, want 404", p, res.StatusCode)
		}
	}
}

// Decision 0036: an Auth0 tenant signs in like Google, and its identity is linked under "auth0",
// apart from a Google identity of the same person.
func TestAuth0SignIn(t *testing.T) {
	f := newFixture(t)
	f.addUser("ada@example.com", "org-a", "admin")
	w := f.web(nil)
	a0 := &fakeProvider{nonce: true}
	w.s.Providers = map[string]auth.Provider{"auth0": a0}
	w.s.Identities = f.pg
	if res := f.do(w.h, http.MethodGet, "/auth/providers", "", request{tls: true}); res.StatusCode != http.StatusOK {
		t.Fatalf("providers = %d", res.StatusCode)
	} else if b, _ := io.ReadAll(res.Body); string(b) != `{"auth0":true,"github":false,"google":false}` {
		t.Fatalf("providers = %s", b)
	}

	a0.set(oauth.Identity{Subject: "google-oauth2|1", Email: "ada@example.com"}, nil)
	res := f.do(w.h, http.MethodGet, "/auth/oauth/auth0/start", "", request{tls: true})
	var body struct{ URL string }
	if err := json.NewDecoder(res.Body).Decode(&body); err != nil || res.StatusCode != http.StatusOK {
		t.Fatalf("start = %d, %v", res.StatusCode, err)
	}
	u, _ := url.Parse(body.URL)
	cb := f.do(w.h, http.MethodGet, "/auth/oauth/auth0/callback?"+url.Values{"state": {u.Query().Get("state")}, "code": {"c0de"}}.Encode(), "",
		request{tls: true, cookies: []*http.Cookie{sessionCookie(res)}})
	if _, ok := signedInBy(cb); !ok {
		t.Fatalf("callback = %d to %q; want signed in", cb.StatusCode, cb.Header.Get("Location"))
	}
	_, ids, err := f.pg.SignInMethods(context.Background(), f.userID("ada@example.com"))
	if err != nil || len(ids) != 1 || ids[0].Provider != "auth0" {
		t.Fatalf("SignInMethods = %+v, %v; want one auth0 identity", ids, err)
	}
}

// Acceptance check 3.
func TestProviderSignInLinksBySubject(t *testing.T) {
	f := newFixture(t)
	f.addUser("ada@example.com", "org-a", "admin")
	w, google := f.socialWeb()

	google.set(oauth.Identity{Subject: "g-1", Email: "Ada@Example.com"}, nil)
	if _, ok := signedInBy(w.callback(w.start(""), "")); ok { // state "" never matches
		t.Fatal("a callback without the session's state signed in")
	}
	st := w.start("")
	c, ok := signedInBy(w.callback(st, st.state))
	if !ok {
		t.Fatal("an existing account's verified email did not sign in")
	}
	if status, roles := w.session(c); status != http.StatusOK || roles["org-a"] != "admin" {
		t.Fatalf("session = %d %v", status, roles)
	}
	if n := f.count(`SELECT count(*) FROM identities WHERE provider = 'google' AND subject = 'g-1' AND user_id = $1`, f.userID("ada@example.com")); n != 1 {
		t.Fatalf("identities = %d, want 1", n)
	}

	// The provider's email changes: the link, not the email, signs the same person in.
	google.set(oauth.Identity{Subject: "g-1", Email: "ada@elsewhere.example"}, nil)
	st = w.start("")
	c, ok = signedInBy(w.callback(st, st.state))
	if !ok {
		t.Fatal("a linked subject with a new email did not sign in")
	}
	if status, roles := w.session(c); status != http.StatusOK || roles["org-a"] != "admin" {
		t.Fatalf("session after the email change = %d %v", status, roles)
	}

	// Settings reads the link, and the password the account still has.
	res := f.do(w.h, http.MethodGet, "/auth/profile", "", request{tls: true, cookies: []*http.Cookie{c}})
	var profile struct {
		HasPassword bool
		Identities  []struct{ Provider, LinkedAt string }
	}
	if err := json.NewDecoder(res.Body).Decode(&profile); err != nil || !profile.HasPassword || len(profile.Identities) != 1 || profile.Identities[0].Provider != "google" {
		t.Fatalf("profile = %+v, %v", profile, err)
	}
}

// Acceptance checks 4 and 5: every refusal is the same redirect, and writes nothing.
func TestProviderSignInRefusals(t *testing.T) {
	f := newFixture(t)
	f.addUser("ada@example.com", "org-a", "admin")
	w, google := f.socialWeb()
	users := f.count(`SELECT count(*) FROM users`)
	for _, tc := range []struct {
		name string
		who  oauth.Identity
		err  error
	}{
		{"unverified email", oauth.Identity{}, oauth.ErrNoVerifiedEmail},
		{"no account and no invitation", oauth.Identity{Subject: "g-9", Email: "nobody@example.com"}, nil},
	} {
		t.Run(tc.name, func(t *testing.T) {
			google.set(tc.who, tc.err)
			st := w.start("")
			res := w.callback(st, st.state)
			refused(t, res)
			if f.count(`SELECT count(*) FROM identities`) != 0 || f.count(`SELECT count(*) FROM users`) != users {
				t.Fatal("a refused sign-in wrote an identity or a user")
			}
			// The session the callback answered with signs nobody in.
			if c := sessionCookie(res); c != nil {
				if status, _ := w.session(c); status != http.StatusUnauthorized {
					t.Fatalf("session after a refusal = %d, want 401", status)
				}
			}
		})
	}
}

// Acceptance check 7.
func TestProviderStateIsCheckedAndSingleUse(t *testing.T) {
	f := newFixture(t)
	f.addUser("ada@example.com", "org-a", "admin")
	w, google := f.socialWeb()
	google.set(oauth.Identity{Subject: "g-1", Email: "ada@example.com"}, nil)

	st := w.start("")
	off := []byte(st.state)
	off[len(off)-1] ^= 1
	refused(t, w.callback(st, string(off)))
	// The state was dropped by that try, so even the right one is refused now.
	refused(t, w.callback(st, st.state))
	if n := google.exchanges.Load(); n != 0 {
		t.Fatalf("code exchanges = %d, want 0", n)
	}
	// A fresh start works, once.
	st = w.start("")
	if _, ok := signedInBy(w.callback(st, st.state)); !ok {
		t.Fatal("a fresh state did not sign in")
	}
	refused(t, w.callback(st, st.state))
	if n := google.exchanges.Load(); n != 1 {
		t.Fatalf("code exchanges = %d, want 1", n)
	}
}

// Acceptance check 6.
func TestProviderAcceptsInvitation(t *testing.T) {
	f := newFixture(t)
	f.addUser("admin@example.com", "org-a", "admin")
	w, google := f.socialWeb()
	admin := w.signIn("admin@example.com")

	// Another verified email: nothing is written, and the invitation stays open.
	token := tokenOf(w.link(admin, "org-a", "a@example.com", "Member"))
	google.set(oauth.Identity{Subject: "g-b", Email: "b@example.com"}, nil)
	st := w.start(token)
	refused(t, w.callback(st, st.state))
	if f.count(`SELECT count(*) FROM users WHERE email IN ('a@example.com', 'b@example.com')`) != 0 || f.count(`SELECT count(*) FROM identities`) != 0 ||
		f.count(`SELECT count(*) FROM invitations WHERE accepted_at IS NOT NULL`) != 0 {
		t.Fatal("an invitation accepted with another email wrote something")
	}

	// Ten callbacks at once for one invitation: one user, one accepted invitation.
	google.set(oauth.Identity{Subject: "g-a", Email: "A@example.com"}, nil)
	flows := make([]started, 10)
	for i := range flows {
		flows[i] = w.start(token)
	}
	var wins atomic.Int64
	var wg sync.WaitGroup
	for _, st := range flows {
		wg.Go(func() {
			if _, ok := signedInBy(w.callback(st, st.state)); ok {
				wins.Add(1)
			}
		})
	}
	wg.Wait()
	if wins.Load() != 1 {
		t.Fatalf("signed in %d times, want 1", wins.Load())
	}
	if f.count(`SELECT count(*) FROM users WHERE email = 'a@example.com' AND password_hash IS NULL`) != 1 ||
		f.count(`SELECT count(*) FROM invitations WHERE accepted_at IS NOT NULL`) != 1 ||
		f.count(`SELECT count(*) FROM memberships m JOIN users u ON u.id = m.user_id WHERE u.email = 'a@example.com' AND m.organisation_id = 'org-a' AND m.role = 'member'`) != 1 ||
		f.count(`SELECT count(*) FROM identities WHERE subject = 'g-a'`) != 1 {
		t.Fatal("want one passwordless member, one accepted invitation and one identity")
	}
	// The used invitation is refused, but the linked subject still signs in without it.
	st = w.start(token)
	refused(t, w.callback(st, st.state))
	st = w.start("")
	c, ok := signedInBy(w.callback(st, st.state))
	if !ok {
		t.Fatal("the new member's linked subject did not sign in")
	}
	res := f.do(w.h, http.MethodGet, "/auth/profile", "", request{tls: true, cookies: []*http.Cookie{c}})
	var profile struct{ HasPassword bool }
	if err := json.NewDecoder(res.Body).Decode(&profile); err != nil || profile.HasPassword {
		t.Fatalf("profile hasPassword = %v, %v; want false", profile.HasPassword, err)
	}
}

// An existing account invited to another organisation joins it with a provider.
func TestProviderInvitationForExistingAccount(t *testing.T) {
	f := newFixture(t)
	f.addUser("admin@example.com", "org-b", "admin")
	f.addUser("ada@example.com", "org-a", "viewer")
	w, google := f.socialWeb()
	token := tokenOf(w.link(w.signIn("admin@example.com"), "org-b", "ada@example.com", "Member"))
	google.set(oauth.Identity{Subject: "g-ada", Email: "ada@example.com"}, nil)
	st := w.start(token)
	c, ok := signedInBy(w.callback(st, st.state))
	if !ok {
		t.Fatal("the invitation was not accepted")
	}
	if status, roles := w.session(c); status != http.StatusOK || roles["org-a"] != "viewer" || roles["org-b"] != "member" {
		t.Fatalf("session = %d %v; want viewer of org-a and member of org-b", status, roles)
	}
	if f.count(`SELECT count(*) FROM users WHERE email = 'ada@example.com' AND password_hash IS NOT NULL`) != 1 {
		t.Fatal("joining with a provider changed the account's password")
	}
}

// Acceptance check 8.
func TestPasswordSignInWithoutPassword(t *testing.T) {
	f := newFixture(t)
	f.addUser("ada@example.com", "org-a", "admin")
	if _, err := f.pool.Exec(context.Background(), `INSERT INTO users (email, password_hash) VALUES ('nopw@example.com', NULL)`); err != nil {
		t.Fatal(err)
	}
	routes := f.service().Routes()
	try := func(email string) (time.Duration, int, string) {
		// The tries would reach the per-email limit; each starts with none counted.
		if _, err := f.pool.Exec(context.Background(), `DELETE FROM sign_in_attempts`); err != nil {
			t.Fatal(err)
		}
		begin := time.Now()
		res := f.do(routes, http.MethodPost, "/auth/sign-in", signIn(email, "some password guess"), request{tls: true})
		took := time.Since(begin)
		return took, res.StatusCode, code(t, res)
	}
	var none, wrong []time.Duration
	for range 50 {
		d, status, c := try("nopw@example.com")
		if status != http.StatusUnauthorized || c != "unauthenticated" {
			t.Fatalf("no-password sign-in = %d %s, want 401 unauthenticated", status, c)
		}
		none = append(none, d)
		d, status, c = try("ada@example.com")
		if status != http.StatusUnauthorized || c != "unauthenticated" {
			t.Fatalf("wrong password = %d %s", status, c)
		}
		wrong = append(wrong, d)
	}
	a, b := median(none), median(wrong)
	if ratio := float64(a) / float64(b); ratio < 0.8 || ratio > 1.2 {
		t.Fatalf("median no-password %v vs wrong password %v: ratio %.2f, want within 20%%", a, b, ratio)
	}
}

func median(ds []time.Duration) time.Duration {
	s := slices.Clone(ds)
	sort.Slice(s, func(i, j int) bool { return s[i] < s[j] })
	return s[len(s)/2]
}

// Acceptance check 10, server side: the whole flow a browser drives from an /invite link, against
// a test Google issuer through the real provider (go-oidc verifying a signed ID token), real
// sessions and Postgres. The button's click is the console's own test.
func TestGoogleInvitationEndToEnd(t *testing.T) {
	f := newFixture(t)
	f.addUser("admin@example.com", "org-a", "admin")
	w := f.web(nil)
	token := tokenOf(w.link(w.signIn("admin@example.com"), "org-a", "new@example.com", "Viewer"))

	web := httptest.NewTLSServer(w.h)
	defer web.Close()
	google := newTestGoogle(t, "new@example.com")
	w.s.Providers = map[string]auth.Provider{"google": oauth.NewGoogle(oauth.Config{
		ClientID: "vg-test", ClientSecret: "s3cret", RedirectURL: web.URL + "/auth/oauth/google/callback",
		Endpoints: oauth.Endpoints{AuthURL: google.URL + "/authorize", TokenURL: google.URL + "/token", Issuer: google.URL, JWKSURL: google.URL + "/keys"},
	}, google.Client())}
	w.s.Identities = f.pg

	jar, err := cookiejar.New(nil)
	if err != nil {
		t.Fatal(err)
	}
	browser := web.Client()
	browser.Jar = jar
	browser.CheckRedirect = func(req *http.Request, _ []*http.Request) error {
		if req.URL.Host == strings.TrimPrefix(web.URL, "https://") && req.URL.Path == "/" {
			return http.ErrUseLastResponse // the console's page, which this test does not serve
		}
		return nil
	}
	req, _ := http.NewRequest(http.MethodGet, web.URL+"/auth/oauth/google/start", nil)
	req.Header.Set("X-Invitation-Token", token)
	res, err := browser.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	var started struct{ URL string }
	if err := json.NewDecoder(res.Body).Decode(&started); err != nil {
		t.Fatal(err)
	}
	_ = res.Body.Close()
	res, err = browser.Get(started.URL) // Google, then back to the callback, then to /
	if err != nil {
		t.Fatal(err)
	}
	_ = res.Body.Close()
	if res.StatusCode != http.StatusSeeOther || res.Header.Get("Location") != "/" {
		t.Fatalf("the flow ended %d at %q; want the redirect to /", res.StatusCode, res.Header.Get("Location"))
	}
	res, err = browser.Get(web.URL + "/auth/session")
	if err != nil {
		t.Fatal(err)
	}
	var s struct {
		Email         string
		Organisations []struct{ ID, Role string }
	}
	_ = json.NewDecoder(res.Body).Decode(&s)
	_ = res.Body.Close()
	if res.StatusCode != http.StatusOK || s.Email != "new@example.com" || len(s.Organisations) != 1 || s.Organisations[0].ID != "org-a" || s.Organisations[0].Role != "viewer" {
		t.Fatalf("session = %d %+v; want new@example.com, viewer of org-a", res.StatusCode, s)
	}
}

// newTestGoogle is a test Google: go-oidc's test issuer for discovery and keys, an authorize
// endpoint that sends the browser straight back with a code, and a token endpoint that checks PKCE
// and answers an ID token for email, verified, carrying the authorization request's nonce.
func newTestGoogle(t *testing.T, email string) *httptest.Server {
	t.Helper()
	key, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	issuer := &oidctest.Server{PublicKeys: []oidctest.PublicKey{{PublicKey: key.Public(), KeyID: "k1", Algorithm: oidc.RS256}}}
	var mu sync.Mutex
	var challenge, nonce string
	var srv *httptest.Server
	mux := http.NewServeMux()
	mux.Handle("/", issuer)
	mux.HandleFunc("GET /authorize", func(w http.ResponseWriter, r *http.Request) {
		q := r.URL.Query()
		mu.Lock()
		challenge, nonce = q.Get("code_challenge"), q.Get("nonce")
		mu.Unlock()
		//nolint:gosec // a test Google, sending the test's browser back to the redirect_uri it was given
		http.Redirect(w, r, q.Get("redirect_uri")+"?"+url.Values{"code": {"c0de"}, "state": {q.Get("state")}}.Encode(), http.StatusFound)
	})
	mux.HandleFunc("POST /token", func(w http.ResponseWriter, r *http.Request) {
		_ = r.ParseForm()
		sum := sha256.Sum256([]byte(r.PostForm.Get("code_verifier")))
		mu.Lock()
		ok, n := base64.RawURLEncoding.EncodeToString(sum[:]) == challenge, nonce
		mu.Unlock()
		if !ok || r.PostForm.Get("code") != "c0de" {
			http.Error(w, `{"error":"invalid_grant"}`, http.StatusBadRequest)
			return
		}
		claims, _ := json.Marshal(map[string]any{
			"iss": srv.URL, "aud": "vg-test", "sub": "g-new", "nonce": n, "email": email, "email_verified": true,
			"iat": time.Now().Unix(), "exp": time.Now().Add(time.Hour).Unix(),
		})
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{"access_token": "at", "token_type": "Bearer", "id_token": oidctest.SignIDToken(key, "k1", oidc.RS256, string(claims))})
	})
	srv = httptest.NewServer(mux)
	t.Cleanup(srv.Close)
	issuer.SetIssuer(srv.URL)
	return srv
}
