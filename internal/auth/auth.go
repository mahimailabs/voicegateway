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
	"time"

	"github.com/alexedwards/scs/v2"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// Users is the account store sign-in reads and writes.
type Users interface {
	CountUsers(ctx context.Context) (int64, error)
	UserByEmail(ctx context.Context, email string) (store.User, error)
	Principal(ctx context.Context, userID string) (store.Principal, error)
	RecordFailedSignIn(ctx context.Context, email, address string) error
	FailedSignIns(ctx context.Context, kind, key string, since time.Time) (int64, time.Time, error)
	ForgetSignIns(ctx context.Context, before time.Time) error
}

// Sign-in limits (spec 06a): failures counted per email and per client address over a window.
const (
	limitWindow     = 15 * time.Minute
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
	// PublicHTTPS is true when VOICEGATEWAY_PUBLIC_URL is https: every request is then treated
	// as having come over HTTPS.
	PublicHTTPS bool
	// Token is the interim API token, empty when unset.
	Token string
	Log   *slog.Logger
	Now   func() time.Time
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

// https reports whether the request is treated as having come over HTTPS.
func (s *Service) https(r *http.Request) bool {
	return s.PublicHTTPS || overHTTPS(r, s.Trusted)
}

func (s *Service) manager(r *http.Request) *scs.SessionManager {
	if s.https(r) {
		return s.Secure
	}
	return s.Plain
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

// Routes returns /auth/sign-in, /auth/sign-out and /auth/session, with sessions loaded.
func (s *Service) Routes() http.Handler {
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
	address := clientAddress(r, s.Trusted)
	// A Secure cookie set over plain HTTP is dropped by the browser, so sign-in would loop
	// silently; away from localhost, say HTTPS is required instead.
	if !s.https(r) && !address.IsLoopback() {
		writeError(w, http.StatusBadRequest, "https_required", "sign-in needs HTTPS when the console is not on localhost")
		return
	}
	var c credentials
	if err := json.NewDecoder(io.LimitReader(r.Body, maxSignInBody)).Decode(&c); err != nil || c.Email == "" || c.Password == "" {
		writeError(w, http.StatusBadRequest, "invalid_request", "email and password are required")
		return
	}
	now := s.Now()
	if retry, err := s.limited(ctx, c.Email, address.String(), now); err != nil {
		s.fail(w, err)
		return
	} else if retry > 0 {
		w.Header().Set("Retry-After", strconv.Itoa(int(math.Ceil(retry.Seconds()))))
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
		if err := s.Users.RecordFailedSignIn(ctx, c.Email, address.String()); err != nil {
			s.fail(w, err)
			return
		}
		writeError(w, http.StatusUnauthorized, "unauthenticated", "email or password is wrong")
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

// limited returns how long sign-in must wait for an email or a client address, 0 when it need not.
func (s *Service) limited(ctx context.Context, email, address string, now time.Time) (time.Duration, error) {
	since := now.Add(-limitWindow)
	if err := s.Users.ForgetSignIns(ctx, since); err != nil {
		return 0, err
	}
	var wait time.Duration
	for _, l := range []struct {
		kind, key string
		max       int64
	}{{"email", email, limitPerEmail}, {"address", address, limitPerAddress}} {
		n, oldest, err := s.Users.FailedSignIns(ctx, l.kind, l.key, since)
		if err != nil {
			return 0, err
		}
		if n >= l.max {
			wait = max(wait, oldest.Add(limitWindow).Sub(now), time.Second)
		}
	}
	return wait, nil
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
