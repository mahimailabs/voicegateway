package auth

import (
	"context"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"math"
	"net/http"
	"net/netip"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/alexedwards/scs/v2"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// Users is the account store sign-in reads and writes.
type Users interface {
	AccountProfile(context.Context, string) (store.AccountProfile, error)
	UpdateAccountProfile(context.Context, string, int64, string) (store.AccountProfile, error)
	CountUsers(ctx context.Context) (int64, error)
	UserByEmail(ctx context.Context, email string) (store.User, error)
	Principal(ctx context.Context, userID string) (store.Principal, error)
	ReserveSignIn(ctx context.Context, keys []store.SignInKey) (ids []int64, wait time.Duration, err error)
	ForgetSignInAttempts(ctx context.Context, ids []int64) error
	ForgetSignIns(ctx context.Context) error
	ProjectKey(ctx context.Context, keyID string) (store.StoredKey, error)
	TouchProjectKey(ctx context.Context, keyID string) error
	// EmbedGrantActive reports whether an embed token's grant still holds: its minting key is
	// unrevoked (or its minting user still a member) and the project still allows its origin.
	EmbedGrantActive(ctx context.Context, g store.EmbedScope) (bool, error)
	UpdatePasswordHash(ctx context.Context, userID, verifiedHash, passwordHash string) (bool, error)
}

// Sign-in limits (spec 06a): attempts counted per email and per client address over a 15-minute
// window, which the store keeps by the Postgres clock.
const (
	limitPerEmail   = 10
	limitPerAddress = 50
	maxSignInBody   = 4 << 10
)

// sessionUser is the session key holding the signed-in user's id; sessionPasswordVersion holds
// the user's password_version at sign-in. A 06a session has none, which reads as 0, the version
// of a password never reset.
const (
	sessionUser            = "user"
	sessionPasswordVersion = "password_version"
)

// Service answers /auth and gates /api/v1.
type Service struct {
	Users Users
	// Secure and Plain are two session managers over one store, the same but for the cookie's
	// Secure flag; the request's transport picks one, since scs fixes the flag per manager.
	Secure, Plain *scs.SessionManager
	// Trusted are the proxies whose X-Forwarded-For and X-Forwarded-Proto are believed.
	Trusted []netip.Prefix
	// PublicHTTPS is true when VOICEGATEWAY_PUBLIC_URL is https: every session cookie is then
	// Secure. It says nothing of how a request arrived, so it never lifts https_required.
	PublicHTTPS bool
	// PublicURL is VOICEGATEWAY_PUBLIC_URL, an invitation link's base when set.
	PublicURL string
	// Links reads and uses invitation links; nil serves no /auth/invitation routes.
	Links Links
	// Compare compares two SHA-256 hashes in constant time and reports 1 when they are equal; nil
	// is subtle.ConstantTimeCompare. Tests count the comparisons through it.
	Compare func(x, y []byte) int
	// InstanceTokenSHA256 is the SHA-256 of VOICEGATEWAY_INSTANCE_TOKEN (phase 06e); nil when it is
	// unset. A Bearer equal to the token is the instance principal, which names no user.
	InstanceTokenSHA256 []byte
	// Embeds mints and checks embed tokens (decision 0034); nil refuses every embed token.
	Embeds *EmbedTokens
	Log    *slog.Logger
	Now    func() time.Time

	proxyWarning sync.Once
}

// NewSessions returns the Secure and Plain managers over one store: HttpOnly, SameSite=Lax, a
// 12-hour idle and 7-day absolute lifetime.
func NewSessions(st scs.Store) (secure, plain *scs.SessionManager) {
	mk := func(isSecure bool) *scs.SessionManager {
		m := scs.New()
		m.Store = st
		m.IdleTimeout = 12 * time.Hour
		m.Lifetime = 7 * 24 * time.Hour
		m.Cookie.Name = "vg_session"
		m.Cookie.HttpOnly = true
		m.Cookie.SameSite = http.SameSiteLaxMode
		m.Cookie.Path = "/"
		m.Cookie.Secure = isSecure
		m.ErrorFunc = func(w http.ResponseWriter, r *http.Request, err error) {
			writeError(w, http.StatusInternalServerError, "internal_error", "the session could not be read")
		}
		return m
	}
	return mk(true), mk(false)
}

// https reports whether the request's session cookie is Secure: the console's public address is
// https, or the request itself came over HTTPS.
func (s *Service) https(r *http.Request) bool {
	return s.PublicHTTPS || overHTTPS(r, s.Trusted)
}

func (s *Service) manager(r *http.Request) *scs.SessionManager {
	if s.https(r) {
		return s.Secure
	}
	return s.Plain
}

// passwordMayTravel reports whether a password may be sent on this request: it came over HTTPS,
// or it was addressed to localhost by a browser on this machine, where a session cookie survives
// plain HTTP. The Host header decides localhost, not the peer: behind Docker's port forwarding the
// peer is a bridge address, and a client lying about Host only exposes its own password. A
// forwarded request is never local, whatever its Host: a proxy that passes plain HTTP on to
// 127.0.0.1 would otherwise let any client sign in without HTTPS.
func (s *Service) passwordMayTravel(r *http.Request) bool {
	return overHTTPS(r, s.Trusted) || (localHost(r.Host) && !forwarded(r))
}

// Sessions loads and saves the session of every request through h, with the manager the
// request's transport picks.
func (s *Service) Sessions(h http.Handler) http.Handler {
	secure, plain := s.Secure.LoadAndSave(h), s.Plain.LoadAndSave(h)
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if s.https(r) {
			secure.ServeHTTP(w, r)
			return
		}
		plain.ServeHTTP(w, r)
	})
}

// Routes returns /auth/sign-in, /auth/sign-out, /auth/session and, with Links set, the two
// /auth/invitation routes, with sessions loaded. It starts
// building the dummy hash in the background, so web listens at once and the first unknown email
// takes no longer than the rest; a sign-in that comes first waits for the same hash.
func (s *Service) Routes() http.Handler {
	go func() {
		if _, err := dummyHash(); err != nil {
			s.Log.Error("auth: could not build the dummy password hash", "err", err)
		}
	}()
	mux := http.NewServeMux()
	mux.HandleFunc("POST /auth/sign-in", s.signIn)
	mux.HandleFunc("POST /auth/sign-out", s.signOut)
	mux.HandleFunc("GET /auth/session", s.session)
	mux.HandleFunc("GET /auth/profile", s.profile)
	mux.HandleFunc("PUT /auth/profile", s.updateProfile)
	if s.Links != nil {
		mux.Handle("GET /auth/invitation", LinkHeaders(http.HandlerFunc(s.invitation)))
		mux.Handle("POST /auth/invitation/accept", LinkHeaders(http.HandlerFunc(s.acceptInvitation)))
	}
	return s.Sessions(mux)
}

type credentials struct {
	Email    string `json:"email"`
	Password string `json:"password"`
}

func (s *Service) signIn(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	// A Secure cookie set over plain HTTP is dropped by the browser, so sign-in would loop
	// silently, and the password would cross the network in clear; away from localhost, say HTTPS
	// is required instead.
	// Before the HTTPS check: an untrusted proxy that terminates TLS forwards plain HTTP, and is the
	// one most worth naming.
	s.warnUntrustedProxy(r)
	if !s.passwordMayTravel(r) {
		msg := "sign-in needs HTTPS when the console is not on localhost"
		if strings.EqualFold(r.Header.Get("X-Forwarded-Proto"), "https") && !isTrusted(peerAddress(r), s.Trusted) {
			msg += "; a proxy says it received HTTPS, but it is not in VOICEGATEWAY_TRUSTED_PROXIES"
		}
		writeError(w, http.StatusBadRequest, "https_required", msg)
		return
	}
	var c credentials
	if err := json.NewDecoder(io.LimitReader(r.Body, maxSignInBody)).Decode(&c); err != nil || c.Email == "" || c.Password == "" {
		writeError(w, http.StatusBadRequest, "invalid_request", "email and password are required")
		return
	}
	if err := s.Users.ForgetSignIns(ctx); err != nil {
		s.fail(w, err)
		return
	}
	// The attempt is reserved before the password is checked, atomically, so a burst of requests
	// cannot all pass the limit while the count is still low.
	ids, wait, err := s.Users.ReserveSignIn(ctx, []store.SignInKey{
		{Kind: "email", Key: c.Email, Max: limitPerEmail},
		{Kind: "address", Key: addressKey(clientAddress(r, s.Trusted)), Max: limitPerAddress},
	})
	if err != nil {
		s.fail(w, err)
		return
	}
	if wait > 0 {
		w.Header().Set("Retry-After", strconv.Itoa(int(math.Ceil(wait.Seconds()))))
		writeError(w, http.StatusTooManyRequests, "too_many_attempts", "too many failed sign-ins; try again later")
		return
	}

	u, err := s.Users.UserByEmail(ctx, c.Email)
	known := err == nil
	if err != nil && !errors.Is(err, store.ErrNotFound) {
		s.fail(w, err)
		return
	}
	hash := u.PasswordHash
	if !known {
		if hash, err = dummyHash(); err != nil {
			s.fail(w, err)
			return
		}
	}
	ok, err := VerifyPassword(ctx, c.Password, hash)
	if err != nil {
		s.fail(w, err)
		return
	}
	if !known || !ok {
		// The reserved attempt stays counted.
		writeError(w, http.StatusUnauthorized, "unauthenticated", "email or password is wrong")
		return
	}
	if err := s.Users.ForgetSignInAttempts(ctx, ids); err != nil {
		s.fail(w, err)
		return
	}
	// A hash made with other parameters is made again now, while the password is known, so a
	// change to the parameters applies at each user's next sign-in. A failure only logs: the sign-in
	// itself stands.
	if NeedsRehash(u.PasswordHash) {
		if hash, err := HashPassword(ctx, c.Password); err != nil {
			s.Log.Warn("auth: could not rehash a password", "err", err)
		} else if _, err := s.Users.UpdatePasswordHash(ctx, u.ID, u.PasswordHash, hash); err != nil {
			// Not stored is fine too: a reset since the check changed the hash, and it wins.
			s.Log.Warn("auth: could not store a rehashed password", "err", err)
		}
	}
	m := s.manager(r)
	if err := m.RenewToken(ctx); err != nil {
		s.fail(w, err)
		return
	}
	m.Put(ctx, sessionUser, u.ID)
	m.Put(ctx, sessionPasswordVersion, u.PasswordVersion)
	w.WriteHeader(http.StatusNoContent)
}

// warnUntrustedProxy logs once when a request forwards for someone from a peer that is not a
// trusted proxy: a reverse proxy VOICEGATEWAY_TRUSTED_PROXIES does not name, on the same host or,
// under Docker, arriving from the bridge gateway. Every client behind it then shares the proxy's
// address for sign-in limits.
func (s *Service) warnUntrustedProxy(r *http.Request) {
	if isTrusted(peerAddress(r), s.Trusted) {
		return
	}
	if r.Header.Get("X-Forwarded-For") == "" && r.Header.Get("Forwarded") == "" {
		return
	}
	s.proxyWarning.Do(func() {
		s.Log.Warn("auth: a proxy on this host forwards requests but is not trusted; set VOICEGATEWAY_TRUSTED_PROXIES so each client is counted on its own address")
	})
}

func (s *Service) signOut(w http.ResponseWriter, r *http.Request) {
	if err := s.manager(r).Destroy(r.Context()); err != nil {
		s.fail(w, err)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

type sessionBody struct {
	Profile       store.AccountProfile `json:"profile"`
	Email         string               `json:"email"`
	Organisations []orgRoleBody        `json:"organisations"`
}

type orgRoleBody struct {
	ID   string `json:"id"`
	Role string `json:"role"`
}

func (s *Service) session(w http.ResponseWriter, r *http.Request) {
	p, ok, err := s.userPrincipal(r)
	if err != nil {
		s.fail(w, err)
		return
	}
	if !ok {
		n, err := s.Users.CountUsers(r.Context())
		if err != nil {
			s.fail(w, err)
			return
		}
		// With no account at all, the console shows how to create the first admin.
		writeJSON(w, http.StatusUnauthorized, map[string]any{"code": "unauthenticated", "message": "not signed in", "needsAdmin": n == 0})
		return
	}
	profile, err := s.Users.AccountProfile(r.Context(), p.UserID)
	if err != nil {
		s.fail(w, err)
		return
	}
	body := sessionBody{Email: p.Email, Organisations: []orgRoleBody{}, Profile: profile}
	for org, role := range p.Roles {
		body.Organisations = append(body.Organisations, orgRoleBody{ID: org, Role: role})
	}
	writeJSON(w, http.StatusOK, body)
}

// userPrincipal returns the signed-in user of the request's session, when there is one. A session
// from before the user's last password change is deleted and reads as signed out.
func (s *Service) userPrincipal(r *http.Request) (store.Principal, bool, error) {
	m := s.manager(r)
	id := m.GetString(r.Context(), sessionUser)
	if id == "" {
		return store.Principal{}, false, nil
	}
	p, err := s.Users.Principal(r.Context(), id)
	if errors.Is(err, store.ErrNotFound) {
		return store.Principal{}, false, nil
	}
	if err != nil {
		return store.Principal{}, false, err
	}
	if m.GetInt(r.Context(), sessionPasswordVersion) != p.PasswordVersion {
		if err := m.Destroy(r.Context()); err != nil {
			return store.Principal{}, false, err
		}
		return store.Principal{}, false, nil
	}
	return p, true, nil
}

// Gate identifies a /api/v1 request's caller, with its session loaded: a signed-in user, or a
// project API key, the instance token or an embed token sent as a Bearer token. It puts the principal on the context and refuses nothing
// itself: the API router answers any request without a principal 401, and checks access per route.
func (s *Service) Gate(next http.Handler) http.Handler {
	inner := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		p, ok, err := s.userPrincipal(r)
		if err != nil {
			s.fail(w, err)
			return
		}
		if v, isBearer := bearer(r.Header.Get("Authorization")); !ok && isBearer {
			if s.isInstanceToken(v) {
				p, ok = store.Principal{Instance: true}, true
			} else if IsEmbedToken(v) {
				if p, ok, err = s.embedPrincipal(r.Context(), v); err != nil {
					s.fail(w, err)
					return
				}
			} else if p, ok, err = s.keyPrincipal(r.Context(), v); err != nil {
				s.fail(w, err)
				return
			}
		}
		if !ok {
			// No credential: the API router answers 401 in the contract's Error shape.
			next.ServeHTTP(w, r)
			return
		}
		next.ServeHTTP(w, r.WithContext(store.WithPrincipal(r.Context(), p)))
	})
	return s.Sessions(inner)
}

// now is the service's clock, time.Now when none is set.
func (s *Service) now() time.Time {
	if s.Now != nil {
		return s.Now()
	}
	return time.Now()
}

// isInstanceToken reports whether a Bearer value is the instance token, comparing SHA-256 hashes
// in constant time; always false when the token is unset.
func (s *Service) isInstanceToken(v string) bool {
	if s.InstanceTokenSHA256 == nil {
		return false
	}
	sum := sha256.Sum256([]byte(v))
	return subtle.ConstantTimeCompare(sum[:], s.InstanceTokenSHA256) == 1
}

// dummyKeyHash stands in for the stored hash when a presented key is malformed, unknown or revoked,
// so every refusal runs the same comparison as an accepted key.
var dummyKeyHash = sha256.Sum256([]byte("voicegateway: no such project key"))

// keyPrincipal checks a Bearer value as a project API key. Any value that is not a valid, unrevoked
// key reads as no credential, the same 401 for each, and runs the one comparison either way.
func (s *Service) keyPrincipal(ctx context.Context, v string) (store.Principal, bool, error) {
	keyID, presented, parsed := parseKey(v)
	stored := dummyKeyHash[:]
	var found *store.StoredKey
	if parsed {
		k, err := s.Users.ProjectKey(ctx, keyID)
		switch {
		case err == nil && !k.Revoked:
			stored, found = k.SecretSHA256, &k
		case err != nil && !errors.Is(err, store.ErrNotFound):
			return store.Principal{}, false, err
		}
	}
	compare := s.Compare
	if compare == nil {
		compare = subtle.ConstantTimeCompare
	}
	if compare(presented[:], stored) != 1 || found == nil {
		return store.Principal{}, false, nil
	}
	// Last used is updated at most once a minute. Skipping the write when the stored time is recent
	// keeps a busy key at one round trip; the conditional update still guards against a race.
	if found.LastUsedAt == nil || s.now().Sub(*found.LastUsedAt) >= time.Minute {
		if err := s.Users.TouchProjectKey(ctx, found.KeyID); err != nil {
			// Last used is a convenience; a failed write does not refuse the key. The key id logged
			// is the stored one, never text from the request's Authorization header.
			s.Log.Warn("auth: could not record a project key's use", "key_id", found.KeyID, "err", err)
		}
	}
	return store.Principal{Key: &store.KeyScope{KeyID: found.KeyID, OrganisationID: found.OrganisationID, ProjectID: found.ProjectID}}, true, nil
}

// bearer returns the credentials of a Bearer Authorization header; the scheme is case-insensitive.
func bearer(header string) (string, bool) {
	const scheme = "bearer "
	if len(header) <= len(scheme) || !strings.EqualFold(header[:len(scheme)], scheme) {
		return "", false
	}
	return header[len(scheme):], true
}

// fail answers 500 and logs the error, never a credential: the errors here carry none.
func (s *Service) fail(w http.ResponseWriter, err error) {
	s.Log.Error("auth: request failed", "err", err)
	writeError(w, http.StatusInternalServerError, "internal_error", "the request could not be completed")
}

func writeError(w http.ResponseWriter, status int, code, message string) {
	writeJSON(w, status, map[string]string{"code": code, "message": message})
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	body, err := json.Marshal(v)
	if err != nil {
		http.Error(w, `{"code":"internal_error","message":"response could not be encoded"}`, http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_, _ = w.Write(body)
}
