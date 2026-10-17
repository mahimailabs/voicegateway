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
	CountUsers(ctx context.Context) (int64, error)
	UserByEmail(ctx context.Context, email string) (store.User, error)
	Principal(ctx context.Context, userID string) (store.Principal, error)
	ReserveSignIn(ctx context.Context, keys []store.SignInKey) (ids []int64, wait time.Duration, err error)
	ForgetSignInAttempts(ctx context.Context, ids []int64) error
	ForgetSignIns(ctx context.Context) error
}

// Sign-in limits (spec 06a): attempts counted per email and per client address over a 15-minute
// window, which the store keeps by the Postgres clock.
const (
	limitPerEmail   = 10
	limitPerAddress = 50
	maxSignInBody   = 4 << 10
)

// sessionUser is the session key holding the signed-in user's id.
const sessionUser = "user"

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
	// Token is the interim API token, empty when unset.
	Token string
	Log   *slog.Logger
	Now   func() time.Time

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

// Routes returns /auth/sign-in, /auth/sign-out and /auth/session, with sessions loaded. It starts
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
	if !s.passwordMayTravel(r) {
		writeError(w, http.StatusBadRequest, "https_required", "sign-in needs HTTPS when the console is not on localhost")
		return
	}
	var c credentials
	if err := json.NewDecoder(io.LimitReader(r.Body, maxSignInBody)).Decode(&c); err != nil || c.Email == "" || c.Password == "" {
		writeError(w, http.StatusBadRequest, "invalid_request", "email and password are required")
		return
	}
	s.warnUntrustedProxy(r)
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
	m := s.manager(r)
	if err := m.RenewToken(ctx); err != nil {
		s.fail(w, err)
		return
	}
	m.Put(ctx, sessionUser, u.ID)
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
	Email         string        `json:"email"`
	Organisations []orgRoleBody `json:"organisations"`
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
	body := sessionBody{Email: p.Email, Organisations: []orgRoleBody{}}
	for org, role := range p.Roles {
		body.Organisations = append(body.Organisations, orgRoleBody{ID: org, Role: role})
	}
	writeJSON(w, http.StatusOK, body)
}

// userPrincipal returns the signed-in user of the request's session, when there is one.
func (s *Service) userPrincipal(r *http.Request) (store.Principal, bool, error) {
	id := s.manager(r).GetString(r.Context(), sessionUser)
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
	return p, true, nil
}

// Gate identifies a /api/v1 request's caller, with its session loaded: a signed-in user, or, until
// 06c, the interim bearer token. It puts the principal on the context and refuses nothing itself:
// the API router answers any request without a principal 401, and checks membership per route.
func (s *Service) Gate(next http.Handler) http.Handler {
	want := sha256.Sum256([]byte(s.Token))
	inner := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		p, ok, err := s.userPrincipal(r)
		if err != nil {
			s.fail(w, err)
			return
		}
		if !ok && s.Token != "" {
			got, isBearer := bearer(r.Header.Get("Authorization"))
			sum := sha256.Sum256([]byte(got))
			if isBearer && subtle.ConstantTimeCompare(sum[:], want[:]) == 1 {
				p, ok = store.Principal{Token: true}, true
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
