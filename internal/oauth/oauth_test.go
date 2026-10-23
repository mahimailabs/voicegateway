package oauth_test

import (
	"context"
	"crypto/rand"
	"crypto/rsa"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"testing"
	"time"

	"github.com/coreos/go-oidc/v3/oidc"
	"github.com/coreos/go-oidc/v3/oidc/oidctest"

	"github.com/mahimailabs/voicegateway/internal/oauth"
)

const clientID = "vg-test-client"

// googleFake is a test Google: go-oidc's own test issuer for discovery and keys, plus a token
// endpoint that answers an ID token with the claims under test, once it has checked PKCE.
type googleFake struct {
	srv    *httptest.Server
	key    *rsa.PrivateKey
	claims func(nonce string) map[string]any
	// challenge is the S256 challenge the authorization URL carried; nonce the nonce it carried.
	challenge, nonce string
}

func newGoogleFake(t *testing.T) *googleFake {
	t.Helper()
	return newIssuerFake(t, "/token", "", httptest.NewServer)
}

// newIssuerFake serves an ID token at tokenPath with iss set to the server's URL plus issSuffix,
// and the keys at /keys and /.well-known/jwks.json.
func newIssuerFake(t *testing.T, tokenPath, issSuffix string, start func(http.Handler) *httptest.Server) *googleFake {
	t.Helper()
	key, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	f := &googleFake{key: key}
	issuer := &oidctest.Server{PublicKeys: []oidctest.PublicKey{{PublicKey: key.Public(), KeyID: "k1", Algorithm: oidc.RS256}}}
	mux := http.NewServeMux()
	mux.Handle("/", issuer)
	mux.HandleFunc("GET /.well-known/jwks.json", func(w http.ResponseWriter, r *http.Request) {
		r.URL.Path = "/keys"
		issuer.ServeHTTP(w, r)
	})
	mux.HandleFunc("POST "+tokenPath, func(w http.ResponseWriter, r *http.Request) {
		if err := r.ParseForm(); err != nil {
			http.Error(w, "bad form", http.StatusBadRequest)
			return
		}
		sum := sha256.Sum256([]byte(r.PostForm.Get("code_verifier")))
		if base64.RawURLEncoding.EncodeToString(sum[:]) != f.challenge || r.PostForm.Get("client_secret") != "s3cret" {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusBadRequest)
			_, _ = w.Write([]byte(`{"error":"invalid_grant"}`))
			return
		}
		claims := map[string]any{"iss": f.srv.URL + issSuffix, "aud": clientID, "sub": "g-123", "exp": time.Now().Add(time.Hour).Unix(), "iat": time.Now().Unix()}
		for k, v := range f.claims(f.nonce) {
			claims[k] = v
		}
		raw, _ := json.Marshal(claims)
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{
			"access_token": "at", "token_type": "Bearer", "expires_in": 3600,
			"id_token": oidctest.SignIDToken(key, "k1", oidc.RS256, string(raw)),
		})
	})
	f.srv = start(mux)
	t.Cleanup(f.srv.Close)
	issuer.SetIssuer(f.srv.URL)
	return f
}

func (f *googleFake) provider() *oauth.Provider {
	return oauth.NewGoogle(oauth.Config{
		ClientID: clientID, ClientSecret: "s3cret", RedirectURL: "https://vg.example/auth/oauth/google/callback",
		Endpoints: oauth.Endpoints{AuthURL: f.srv.URL + "/auth", TokenURL: f.srv.URL + "/token", Issuer: f.srv.URL, JWKSURL: f.srv.URL + "/keys"},
	}, f.srv.Client())
}

// authorize reads the challenge and nonce from the authorization URL, as Google would.
func (f *googleFake) authorize(t *testing.T, authURL string) {
	t.Helper()
	u, err := url.Parse(authURL)
	if err != nil {
		t.Fatal(err)
	}
	q := u.Query()
	if q.Get("code_challenge_method") != "S256" || q.Get("state") != "st" || q.Get("client_id") != clientID {
		t.Fatalf("authorization URL %s lacks PKCE, state or client id", authURL)
	}
	f.challenge, f.nonce = q.Get("code_challenge"), q.Get("nonce")
}

func TestGoogle(t *testing.T) {
	cases := []struct {
		name    string
		claims  func(nonce string) map[string]any
		verify  string // the verifier the callback presents; "" is the right one
		want    oauth.Identity
		wantErr error // nil with want empty: any error
	}{
		{
			name: "verified email", claims: func(n string) map[string]any {
				return map[string]any{"nonce": n, "email": "Ada@Example.com", "email_verified": true}
			},
			want: oauth.Identity{Subject: "g-123", Email: "Ada@Example.com"},
		},
		{
			name: "unverified email", claims: func(n string) map[string]any {
				return map[string]any{"nonce": n, "email": "ada@example.com", "email_verified": false}
			},
			wantErr: oauth.ErrNoVerifiedEmail,
		},
		{name: "another sign-in's nonce", claims: func(string) map[string]any {
			return map[string]any{"nonce": "other", "email": "ada@example.com", "email_verified": true}
		}},
		{name: "another audience", claims: func(n string) map[string]any {
			return map[string]any{"nonce": n, "aud": "someone-else", "email": "ada@example.com", "email_verified": true}
		}},
		{
			name: "wrong PKCE verifier", verify: "not-the-verifier-not-the-verifier-not-the-verifier",
			claims: func(n string) map[string]any {
				return map[string]any{"nonce": n, "email": "ada@example.com", "email_verified": true}
			},
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			f := newGoogleFake(t)
			f.claims = tc.claims
			p := f.provider()
			verifier := "right-verifier-right-verifier-right-verifier-0123"
			f.authorize(t, p.AuthCodeURL("st", verifier, "n-1"))
			if f.nonce != "n-1" {
				t.Fatalf("nonce = %q, want n-1", f.nonce)
			}
			got, err := p.Identify(context.Background(), "code", cmp(tc.verify, verifier), "n-1")
			switch {
			case tc.want != (oauth.Identity{}):
				if err != nil || got != tc.want {
					t.Fatalf("Identify = %+v, %v; want %+v", got, err, tc.want)
				}
			case tc.wantErr != nil:
				if !errors.Is(err, tc.wantErr) {
					t.Fatalf("Identify err = %v, want %v", err, tc.wantErr)
				}
			case err == nil:
				t.Fatalf("Identify = %+v, want an error", got)
			}
		})
	}
}

// TestAuth0 runs the tenant from its domain alone: the endpoints and the issuer, which ends in a
// slash, are Auth0's, and the subject is Auth0's user id.
func TestAuth0(t *testing.T) {
	cases := []struct {
		name    string
		claims  func(nonce string) map[string]any
		want    oauth.Identity
		wantErr error
	}{
		{
			name: "verified email", claims: func(n string) map[string]any {
				return map[string]any{"nonce": n, "sub": "github|42", "email": "ada@example.com", "email_verified": true}
			},
			want: oauth.Identity{Subject: "github|42", Email: "ada@example.com"},
		},
		{
			name: "unverified email", claims: func(n string) map[string]any {
				return map[string]any{"nonce": n, "sub": "github|42", "email": "ada@example.com", "email_verified": false}
			},
			wantErr: oauth.ErrNoVerifiedEmail,
		},
		{name: "no verified claim", claims: func(n string) map[string]any {
			return map[string]any{"nonce": n, "sub": "auth0|7", "email": "ada@example.com"}
		}, wantErr: oauth.ErrNoVerifiedEmail},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			f := newIssuerFake(t, "/oauth/token", "/", httptest.NewTLSServer)
			f.claims = tc.claims
			domain := strings.TrimPrefix(f.srv.URL, "https://")
			p := oauth.NewAuth0(domain, oauth.Config{ClientID: clientID, ClientSecret: "s3cret", RedirectURL: "https://vg.example/auth/oauth/auth0/callback"}, f.srv.Client())
			if !p.UsesNonce() {
				t.Fatal("Auth0 sign-in carries no nonce")
			}
			verifier := "right-verifier-right-verifier-right-verifier-0123"
			authURL := p.AuthCodeURL("st", verifier, "n-1")
			if !strings.HasPrefix(authURL, f.srv.URL+"/authorize?") {
				t.Fatalf("authorization URL %s is not the tenant's /authorize", authURL)
			}
			f.authorize(t, authURL)
			got, err := p.Identify(context.Background(), "code", verifier, "n-1")
			if tc.wantErr != nil {
				if !errors.Is(err, tc.wantErr) {
					t.Fatalf("Identify err = %v, want %v", err, tc.wantErr)
				}
				return
			}
			if err != nil || got != tc.want {
				t.Fatalf("Identify = %+v, %v; want %+v", got, err, tc.want)
			}
		})
	}

	// An ID token whose issuer lacks the slash is another issuer's, and is refused.
	f := newIssuerFake(t, "/oauth/token", "", httptest.NewTLSServer)
	f.claims = func(n string) map[string]any {
		return map[string]any{"nonce": n, "email": "ada@example.com", "email_verified": true}
	}
	p := oauth.NewAuth0(strings.TrimPrefix(f.srv.URL, "https://"), oauth.Config{ClientID: clientID, ClientSecret: "s3cret"}, f.srv.Client())
	f.authorize(t, p.AuthCodeURL("st", "right-verifier-right-verifier-right-verifier-0123", "n-1"))
	if got, err := p.Identify(context.Background(), "code", "right-verifier-right-verifier-right-verifier-0123", "n-1"); err == nil {
		t.Fatalf("Identify = %+v with the wrong issuer, want an error", got)
	}
}

func cmp(a, b string) string {
	if a != "" {
		return a
	}
	return b
}

func TestGitHub(t *testing.T) {
	cases := []struct {
		name    string
		emails  string
		want    oauth.Identity
		wantErr error
	}{
		{
			name: "primary verified", emails: `[{"email":"old@example.com","primary":false,"verified":true},{"email":"ada@example.com","primary":true,"verified":true}]`,
			want: oauth.Identity{Subject: "4242", Email: "ada@example.com"},
		},
		{
			name: "primary unverified", emails: `[{"email":"ada@example.com","primary":true,"verified":false},{"email":"other@example.com","primary":false,"verified":true}]`,
			wantErr: oauth.ErrNoVerifiedEmail,
		},
		{name: "no emails", emails: `[]`, wantErr: oauth.ErrNoVerifiedEmail},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			var challenge string
			mux := http.NewServeMux()
			mux.HandleFunc("POST /token", func(w http.ResponseWriter, r *http.Request) {
				_ = r.ParseForm()
				sum := sha256.Sum256([]byte(r.PostForm.Get("code_verifier")))
				if base64.RawURLEncoding.EncodeToString(sum[:]) != challenge {
					http.Error(w, "pkce", http.StatusBadRequest)
					return
				}
				w.Header().Set("Content-Type", "application/json")
				_, _ = w.Write([]byte(`{"access_token":"gh-at","token_type":"bearer","scope":"user:email"}`))
			})
			authed := func(h http.HandlerFunc) http.HandlerFunc {
				return func(w http.ResponseWriter, r *http.Request) {
					if r.Header.Get("Authorization") != "Bearer gh-at" {
						http.Error(w, "unauthorised", http.StatusUnauthorized)
						return
					}
					h(w, r)
				}
			}
			mux.HandleFunc("GET /user", authed(func(w http.ResponseWriter, _ *http.Request) { _, _ = w.Write([]byte(`{"id":4242,"login":"ada"}`)) }))
			mux.HandleFunc("GET /user/emails", authed(func(w http.ResponseWriter, _ *http.Request) { _, _ = fmt.Fprint(w, tc.emails) }))
			srv := httptest.NewServer(mux)
			defer srv.Close()
			p := oauth.NewGitHub(oauth.Config{
				ClientID: clientID, ClientSecret: "s3cret", RedirectURL: "https://vg.example/auth/oauth/github/callback",
				Endpoints: oauth.Endpoints{AuthURL: srv.URL + "/login", TokenURL: srv.URL + "/token", APIURL: srv.URL},
			}, srv.Client())
			u, _ := url.Parse(p.AuthCodeURL("st", "gh-verifier-gh-verifier-gh-verifier-gh-verifier-01", ""))
			challenge = u.Query().Get("code_challenge")
			if u.Query().Get("scope") != "user:email" || u.Query().Get("nonce") != "" {
				t.Fatalf("authorization URL %s: want scope user:email and no nonce", u)
			}
			got, err := p.Identify(context.Background(), "code", "gh-verifier-gh-verifier-gh-verifier-gh-verifier-01", "")
			if tc.wantErr != nil {
				if !errors.Is(err, tc.wantErr) {
					t.Fatalf("Identify err = %v, want %v", err, tc.wantErr)
				}
				return
			}
			if err != nil || got != tc.want {
				t.Fatalf("Identify = %+v, %v; want %+v", got, err, tc.want)
			}
		})
	}
}
