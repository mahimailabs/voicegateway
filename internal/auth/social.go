package auth

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/base64"
	"errors"
	"net/http"
	"time"

	"golang.org/x/oauth2"

	"github.com/mahimailabs/voicegateway/internal/oauth"
	"github.com/mahimailabs/voicegateway/internal/store"
)

// Identities are the provider links Google and GitHub sign-in read and write (phase 06h).
type Identities interface {
	UserByIdentity(ctx context.Context, provider, subject string) (store.User, error)
	LinkIdentity(ctx context.Context, provider, subject, userID, email string) error
	TouchIdentity(ctx context.Context, provider, subject string) error
	SignInMethods(ctx context.Context, userID string) (hasPassword bool, identities []store.Identity, err error)
}

// Provider is Google, GitHub or Auth0 as sign-in uses it: *oauth.Provider.
type Provider interface {
	AuthCodeURL(state, verifier, nonce string) string
	UsesNonce() bool
	Identify(ctx context.Context, code, verifier, nonce string) (oauth.Identity, error)
}

// The session keys of a provider sign-in in flight, dropped by its callback. No new cookie: they
// ride in the scs session, server side.
const (
	oauthProvider   = "oauth_provider"
	oauthState      = "oauth_state"
	oauthVerifier   = "oauth_verifier"
	oauthNonce      = "oauth_nonce"
	oauthInvitation = "oauth_invitation" // the invitation token's SHA-256, never the token
	oauthStarted    = "oauth_started"    // Unix seconds
)

// oauthWindow is how long a provider sign-in may take from start to callback.
const oauthWindow = 10 * time.Minute

// refusedPath is where every refused provider sign-in lands, whatever the cause, so the console
// says nothing of who has an account.
const refusedPath = "/sign-in?error=oauth_refused"

// providers serves GET /auth/providers: which provider buttons the console shows.
func (s *Service) providers(w http.ResponseWriter, _ *http.Request) {
	w.Header().Set("Cache-Control", "public, max-age=60")
	writeJSON(w, http.StatusOK, map[string]bool{"google": s.Providers["google"] != nil, "github": s.Providers["github"] != nil, "auth0": s.Providers["auth0"] != nil})
}

// oauthStart serves GET /auth/oauth/{provider}/start: it keeps a fresh state, PKCE verifier and,
// for Google, nonce in the session, with the invitation token's hash when X-Invitation-Token
// brings one (never a URL, as 06d), and answers the provider's URL for the console to follow.
func (s *Service) oauthStart(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Cache-Control", "no-store")
	name := r.PathValue("provider")
	p := s.Providers[name]
	if p == nil {
		writeError(w, http.StatusNotFound, "not_found", "this sign-in provider is not configured")
		return
	}
	// The session the callback sets is the same cookie a password sign-in sets, so the same HTTPS
	// rule applies.
	if !s.passwordMayTravel(r) {
		writeError(w, http.StatusBadRequest, "https_required", "sign-in needs HTTPS when the console is not on localhost")
		return
	}
	var invitation []byte
	if token := r.Header.Get(invitationTokenHeader); token != "" {
		sum := sha256.Sum256([]byte(token))
		invitation = sum[:]
	}
	state, err := random()
	if err != nil {
		s.fail(w, err)
		return
	}
	nonce := ""
	if p.UsesNonce() {
		if nonce, err = random(); err != nil {
			s.fail(w, err)
			return
		}
	}
	verifier := oauth2.GenerateVerifier()
	ctx := r.Context()
	m := s.manager(r)
	m.Put(ctx, oauthProvider, name)
	m.Put(ctx, oauthState, state)
	m.Put(ctx, oauthVerifier, verifier)
	m.Put(ctx, oauthNonce, nonce)
	m.Put(ctx, oauthStarted, s.now().Unix())
	if invitation != nil {
		m.Put(ctx, oauthInvitation, invitation)
	} else {
		m.Remove(ctx, oauthInvitation)
	}
	writeJSON(w, http.StatusOK, map[string]string{"url": p.AuthCodeURL(state, verifier, nonce)})
}

// oauthCallback serves GET /auth/oauth/{provider}/callback. It checks state against the session's
// copy in constant time and drops it (single use), exchanges the code, then signs in:
//  1. with an invitation from the start, the invited account, when the provider verified its email;
//  2. else the account the provider subject is linked to;
//  3. else the account with the verified email, linking the subject to it;
//  4. else nobody.
//
// Every refusal redirects to the same place; its cause is logged as a class, never with an email.
func (s *Service) oauthCallback(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	name := r.PathValue("provider")
	p := s.Providers[name]
	if p == nil {
		writeError(w, http.StatusNotFound, "not_found", "this sign-in provider is not configured")
		return
	}
	m := s.manager(r)
	stored := m.PopString(ctx, oauthState)
	verifier := m.PopString(ctx, oauthVerifier)
	nonce := m.PopString(ctx, oauthNonce)
	started := m.GetInt64(ctx, oauthStarted)
	m.Remove(ctx, oauthStarted)
	invitation := m.PopBytes(ctx, oauthInvitation)
	inFlight := m.PopString(ctx, oauthProvider)
	q := r.URL.Query()
	presented := q.Get("state")
	if stored == "" || inFlight != name || subtle.ConstantTimeCompare([]byte(stored), []byte(presented)) != 1 ||
		s.now().Sub(time.Unix(started, 0)) > oauthWindow {
		s.refuse(w, r, name, "state")
		return
	}
	if q.Get("error") != "" || q.Get("code") == "" {
		// The person cancelled at the provider, or it refused them.
		s.refuse(w, r, name, "provider_refused")
		return
	}
	if err := s.Users.ForgetSignIns(ctx); err != nil {
		s.fail(w, err)
		return
	}
	// Each callback counts against the client address, as a password try does; a success forgets it.
	ids, wait, err := s.Users.ReserveSignIn(ctx, []store.SignInKey{
		{Kind: "address", Key: addressKey(clientAddress(r, s.Trusted)), Max: limitPerAddress},
	})
	if err != nil {
		s.fail(w, err)
		return
	}
	if wait > 0 {
		s.refuse(w, r, name, "limited")
		return
	}
	who, err := p.Identify(ctx, q.Get("code"), verifier, nonce)
	if errors.Is(err, oauth.ErrNoVerifiedEmail) {
		s.refuse(w, r, name, "no_verified_email")
		return
	}
	if err != nil {
		s.Log.Warn("auth: provider sign-in failed", "provider", name, "err", err)
		s.refuse(w, r, name, "provider_error")
		return
	}
	userID, version, class, err := s.account(ctx, name, who, invitation)
	if err != nil {
		s.fail(w, err)
		return
	}
	if class != "" {
		s.refuse(w, r, name, class)
		return
	}
	if err := s.Users.ForgetSignInAttempts(ctx, ids); err != nil {
		s.fail(w, err)
		return
	}
	if err := m.RenewToken(ctx); err != nil {
		s.fail(w, err)
		return
	}
	m.Put(ctx, sessionUser, userID)
	m.Put(ctx, sessionPasswordVersion, version)
	http.Redirect(w, r, "/", http.StatusSeeOther)
}

// account decides who a provider identity signs in as: the user and their password version, or
// a refusal's class.
func (s *Service) account(ctx context.Context, provider string, who oauth.Identity, invitation []byte) (string, int, string, error) {
	if invitation != nil {
		if s.Links == nil {
			return "", 0, "invitation_invalid", nil
		}
		pr, err := s.Links.AcceptVerified(ctx, invitation, who.Email)
		switch {
		case errors.Is(err, store.ErrEmailMismatch):
			return "", 0, "invitation_mismatch", nil
		case errors.Is(err, store.ErrInvalidLink):
			return "", 0, "invitation_invalid", nil
		case err != nil:
			return "", 0, "", err
		}
		// The provider proved the invited email, so the invited account is signed in. A subject
		// already linked to another account keeps that link.
		if err := s.Identities.LinkIdentity(ctx, provider, who.Subject, pr.UserID, who.Email); errors.Is(err, store.ErrIdentityLinked) {
			s.Log.Info("auth: provider subject is linked to another account; not linked again", "provider", provider)
		} else if err != nil {
			return "", 0, "", err
		}
		return pr.UserID, pr.PasswordVersion, "", nil
	}
	u, err := s.Identities.UserByIdentity(ctx, provider, who.Subject)
	if err == nil {
		if err := s.Identities.TouchIdentity(ctx, provider, who.Subject); err != nil {
			// Last used is a convenience; a failed write does not refuse the sign-in.
			s.Log.Warn("auth: could not record a provider sign-in", "provider", provider, "err", err)
		}
		return u.ID, u.PasswordVersion, "", nil
	}
	if !errors.Is(err, store.ErrNotFound) {
		return "", 0, "", err
	}
	u, err = s.Users.UserByEmail(ctx, who.Email)
	if errors.Is(err, store.ErrNotFound) {
		return "", 0, "no_account", nil
	}
	if err != nil {
		return "", 0, "", err
	}
	if err := s.Identities.LinkIdentity(ctx, provider, who.Subject, u.ID, who.Email); errors.Is(err, store.ErrIdentityLinked) {
		// Linked to another account meanwhile, by a parallel callback for another email: refused
		// rather than sign in by email past a link.
		return "", 0, "identity_linked", nil
	} else if errors.Is(err, store.ErrNotFound) {
		return "", 0, "no_account", nil
	} else if err != nil {
		return "", 0, "", err
	}
	return u.ID, u.PasswordVersion, "", nil
}

// refuse redirects a refused provider sign-in, the same for every cause, and logs the cause's class.
func (s *Service) refuse(w http.ResponseWriter, r *http.Request, provider, class string) {
	s.Log.Info("auth: provider sign-in refused", "provider", provider, "class", class)
	http.Redirect(w, r, refusedPath, http.StatusSeeOther)
}

// random is 32 random bytes, base64url: a state or a nonce.
func random() (string, error) {
	b := make([]byte, 32)
	if _, err := rand.Read(b); err != nil {
		return "", err
	}
	return base64.RawURLEncoding.EncodeToString(b), nil
}
