package oauth

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strconv"
	"time"

	"github.com/coreos/go-oidc/v3/oidc"
	"golang.org/x/oauth2"
	"golang.org/x/oauth2/endpoints"
)

// Identity is who a provider says signed in: its stable subject and one email it verified.
type Identity struct {
	Subject, Email string
}

// ErrNoVerifiedEmail means the provider proved no email: Google's email_verified is false, or
// GitHub has no primary email marked verified.
var ErrNoVerifiedEmail = errors.New("the provider proved no verified email")

// Endpoints are a provider's URLs; a zero field takes the real provider's. Tests point them at an
// httptest server.
type Endpoints struct {
	// AuthURL and TokenURL are the authorization and token endpoints.
	AuthURL, TokenURL string
	// Issuer and JWKSURL are the ID token's issuer and signing keys (Google, Auth0).
	Issuer, JWKSURL string
	// APIURL is GitHub's REST API base.
	APIURL string
}

// Config is one provider's OAuth app.
type Config struct {
	ClientID, ClientSecret string
	// RedirectURL is <VOICEGATEWAY_PUBLIC_URL>/auth/oauth/<provider>/callback, registered with the
	// provider exactly.
	RedirectURL string
	Endpoints   Endpoints
}

// timeout bounds each call to a provider: the token exchange, the key fetch, GitHub's two reads.
const timeout = 10 * time.Second

// maxBody bounds a GitHub API answer read into memory.
const maxBody = 1 << 20

// Provider is Google, GitHub or an Auth0 tenant, configured.
type Provider struct {
	// Name is "google", "github" or "auth0", the path segment of its routes.
	Name string
	// oidc is whether the provider answers an ID token, which carries the sign-in's nonce.
	oidc   bool
	oauth  oauth2.Config
	client *http.Client
	// identify reads the signed-in identity from a token the exchange returned; nonce is the one
	// the authorization request carried (Google's ID token repeats it).
	identify func(ctx context.Context, tok *oauth2.Token, nonce string) (Identity, error)
}

// NewGoogle returns Google: scopes openid and email, the ID token verified by go-oidc against
// Google's issuer and keys with the client id as audience.
func NewGoogle(c Config, client *http.Client) *Provider {
	e := c.Endpoints
	if e.AuthURL == "" {
		e.AuthURL = endpoints.Google.AuthURL
	}
	if e.TokenURL == "" {
		e.TokenURL = endpoints.Google.TokenURL
	}
	if e.Issuer == "" {
		e.Issuer = "https://accounts.google.com"
	}
	if e.JWKSURL == "" {
		e.JWKSURL = "https://www.googleapis.com/oauth2/v3/certs"
	}
	return newOIDC("google", c, e, client)
}

// NewAuth0 returns an Auth0 tenant (decision 0036) at domain, its tenant or custom domain such as
// "voicegateway.us.auth0.com": scopes openid and email, the ID token verified like Google's. The
// tenant's own connections (Google, GitHub, others) decide how people prove who they are; the
// subject is Auth0's user id, such as "github|123".
func NewAuth0(domain string, c Config, client *http.Client) *Provider {
	base := "https://" + domain
	e := c.Endpoints
	if e.AuthURL == "" {
		e.AuthURL = base + "/authorize"
	}
	if e.TokenURL == "" {
		e.TokenURL = base + "/oauth/token"
	}
	if e.Issuer == "" {
		// Auth0's issuer ends in a slash, and go-oidc compares it exactly.
		e.Issuer = base + "/"
	}
	if e.JWKSURL == "" {
		e.JWKSURL = base + "/.well-known/jwks.json"
	}
	return newOIDC("auth0", c, e, client)
}

// newOIDC is an OpenID provider: the ID token in the token answer is verified by go-oidc against
// the issuer and keys with the client id as audience, then its nonce and email_verified.
func newOIDC(name string, c Config, e Endpoints, client *http.Client) *Provider {
	client = clientOr(client)
	// The endpoints are fixed rather than discovered, so web makes no call to the provider until
	// someone signs in; the keys are fetched on the first verify and cached by go-oidc.
	op := (&oidc.ProviderConfig{
		IssuerURL: e.Issuer, AuthURL: e.AuthURL, TokenURL: e.TokenURL, JWKSURL: e.JWKSURL,
		Algorithms: []string{oidc.RS256},
	}).NewProvider(oidc.ClientContext(context.Background(), client))
	verifier := op.Verifier(&oidc.Config{ClientID: c.ClientID})
	p := &Provider{
		Name:   name,
		client: client,
		oidc:   true,
		oauth: oauth2.Config{
			ClientID: c.ClientID, ClientSecret: c.ClientSecret, RedirectURL: c.RedirectURL,
			Endpoint: oauth2.Endpoint{AuthURL: e.AuthURL, TokenURL: e.TokenURL, AuthStyle: oauth2.AuthStyleInParams},
			Scopes:   []string{oidc.ScopeOpenID, "email"},
		},
	}
	p.identify = func(ctx context.Context, tok *oauth2.Token, nonce string) (Identity, error) {
		raw, ok := tok.Extra("id_token").(string)
		if !ok || raw == "" {
			return Identity{}, fmt.Errorf("%s: the token answer has no id_token", name)
		}
		// Signature, issuer, audience and expiry; the nonce is checked here, since go-oidc leaves it
		// to the caller.
		idt, err := verifier.Verify(ctx, raw)
		if err != nil {
			return Identity{}, fmt.Errorf("%s: verify id token: %w", name, err)
		}
		if nonce == "" || idt.Nonce != nonce {
			return Identity{}, fmt.Errorf("%s: the id token's nonce is not this sign-in's", name)
		}
		var claims struct {
			Email         string `json:"email"`
			EmailVerified bool   `json:"email_verified"`
		}
		if err := idt.Claims(&claims); err != nil {
			return Identity{}, fmt.Errorf("%s: id token claims: %w", name, err)
		}
		if !claims.EmailVerified || claims.Email == "" {
			return Identity{}, ErrNoVerifiedEmail
		}
		return Identity{Subject: idt.Subject, Email: claims.Email}, nil
	}
	return p
}

// NewGitHub returns GitHub: scope user:email. GitHub is not an OpenID provider for people, so the
// subject is the numeric id of GET /user and the email the primary, verified one of GET
// /user/emails.
func NewGitHub(c Config, client *http.Client) *Provider {
	e := c.Endpoints
	if e.AuthURL == "" {
		e.AuthURL = endpoints.GitHub.AuthURL
	}
	if e.TokenURL == "" {
		e.TokenURL = endpoints.GitHub.TokenURL
	}
	if e.APIURL == "" {
		e.APIURL = "https://api.github.com"
	}
	p := &Provider{
		Name:   "github",
		client: clientOr(client),
		oauth: oauth2.Config{
			ClientID: c.ClientID, ClientSecret: c.ClientSecret, RedirectURL: c.RedirectURL,
			Endpoint: oauth2.Endpoint{AuthURL: e.AuthURL, TokenURL: e.TokenURL, AuthStyle: oauth2.AuthStyleInParams},
			Scopes:   []string{"user:email"},
		},
	}
	p.identify = func(ctx context.Context, tok *oauth2.Token, _ string) (Identity, error) {
		api := p.oauth.Client(ctx, tok)
		var user struct {
			ID int64 `json:"id"`
		}
		if err := getJSON(ctx, api, e.APIURL+"/user", &user); err != nil {
			return Identity{}, err
		}
		if user.ID <= 0 {
			return Identity{}, errors.New("github: /user has no id")
		}
		var emails []struct {
			Email    string `json:"email"`
			Primary  bool   `json:"primary"`
			Verified bool   `json:"verified"`
		}
		if err := getJSON(ctx, api, e.APIURL+"/user/emails", &emails); err != nil {
			return Identity{}, err
		}
		for _, m := range emails {
			if m.Primary && m.Verified && m.Email != "" {
				return Identity{Subject: strconv.FormatInt(user.ID, 10), Email: m.Email}, nil
			}
		}
		return Identity{}, ErrNoVerifiedEmail
	}
	return p
}

// clientOr is client, or one with the call timeout.
func clientOr(client *http.Client) *http.Client {
	if client != nil {
		return client
	}
	return &http.Client{Timeout: timeout}
}

// AuthCodeURL is the provider's authorization URL for one sign-in: state, the PKCE challenge of
// verifier and, for an OpenID provider, nonce.
func (p *Provider) AuthCodeURL(state, verifier, nonce string) string {
	opts := []oauth2.AuthCodeOption{oauth2.S256ChallengeOption(verifier)}
	if nonce != "" {
		opts = append(opts, oidc.Nonce(nonce))
	}
	return p.oauth.AuthCodeURL(state, opts...)
}

// UsesNonce reports whether the provider's sign-in carries a nonce: an ID token (Google's,
// Auth0's) does.
func (p *Provider) UsesNonce() bool { return p.oidc }

// Identify exchanges code, with the PKCE verifier, and reads who signed in. Errors never carry the
// code or a token.
func (p *Provider) Identify(ctx context.Context, code, verifier, nonce string) (Identity, error) {
	ctx, cancel := context.WithTimeout(context.WithValue(ctx, oauth2.HTTPClient, p.client), timeout)
	defer cancel()
	tok, err := p.oauth.Exchange(ctx, code, oauth2.VerifierOption(verifier))
	if err != nil {
		var re *oauth2.RetrieveError
		if errors.As(err, &re) {
			// The answer's body may echo the code; only its error code is kept.
			return Identity{}, fmt.Errorf("%s: token exchange refused: %s", p.Name, re.ErrorCode)
		}
		return Identity{}, fmt.Errorf("%s: token exchange failed", p.Name)
	}
	return p.identify(ctx, tok, nonce)
}

// getJSON reads one GitHub API answer into v.
func getJSON(ctx context.Context, c *http.Client, url string, v any) error {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return fmt.Errorf("github: %w", err)
	}
	req.Header.Set("Accept", "application/vnd.github+json")
	req.Header.Set("X-GitHub-Api-Version", "2022-11-28")
	res, err := c.Do(req)
	if err != nil {
		return fmt.Errorf("github: read %s failed", req.URL.Path)
	}
	defer func() { _ = res.Body.Close() }()
	if res.StatusCode != http.StatusOK {
		return fmt.Errorf("github: %s answered %d", req.URL.Path, res.StatusCode)
	}
	if err := json.NewDecoder(io.LimitReader(res.Body, maxBody)).Decode(v); err != nil {
		return fmt.Errorf("github: decode %s: %w", req.URL.Path, err)
	}
	return nil
}
