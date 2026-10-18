package auth

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"math"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// Links reads and uses invitation and reset links: the invite service.
type Links interface {
	Look(ctx context.Context, token string) (store.PendingLink, error)
	Accept(ctx context.Context, token, password string, limit func(ctx context.Context, email string) (success func(context.Context) error, wait time.Duration, err error)) (store.Principal, error)
}

// codeInvitationInvalid is the one answer for an unknown, accepted, revoked or expired link.
const codeInvitationInvalid = "invitation_invalid"

// invitationTokenHeader carries a link's token to GET /auth/invitation, never the URL, so no
// server or proxy log holds it.
const invitationTokenHeader = "X-Invitation-Token"

// LinkHeaders marks an answer that shows or takes a link's token: no Referer leaves the page,
// and nothing caches it. The /invite page and both /auth/invitation endpoints send them. They are
// set as the answer starts, so they win over a handler's own, such as the console's no-cache.
func LinkHeaders(h http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		h.ServeHTTP(&linkWriter{ResponseWriter: w}, r)
	})
}

// linkWriter sets the link headers once, just before the status line.
type linkWriter struct {
	http.ResponseWriter
	wrote bool
}

func (w *linkWriter) WriteHeader(status int) {
	if !w.wrote {
		w.wrote = true
		w.Header().Set("Referrer-Policy", "no-referrer")
		w.Header().Set("Cache-Control", "no-store")
	}
	w.ResponseWriter.WriteHeader(status)
}

func (w *linkWriter) Write(b []byte) (int, error) {
	if !w.wrote {
		w.WriteHeader(http.StatusOK)
	}
	return w.ResponseWriter.Write(b)
}

// Unwrap lets http.ResponseController reach the connection's writer.
func (w *linkWriter) Unwrap() http.ResponseWriter { return w.ResponseWriter }

// LinkBase is an invitation link's base for r: VOICEGATEWAY_PUBLIC_URL when set, otherwise the
// request's scheme and Host, the scheme read as sign-in reads it.
func (s *Service) LinkBase(r *http.Request) string {
	if s.PublicURL != "" {
		return strings.TrimRight(s.PublicURL, "/")
	}
	scheme := "http"
	if overHTTPS(r, s.Trusted) {
		scheme = "https"
	}
	return scheme + "://" + r.Host
}

type pendingBody struct {
	Kind             string  `json:"kind"`
	OrganisationName string  `json:"organisationName"`
	Role             *string `json:"role"`
	Email            string  `json:"email"`
}

// invitation serves GET /auth/invitation: what the accept screen shows, the same fields whether
// or not an account exists for the email, and one 404 for every link that does not work.
func (s *Service) invitation(w http.ResponseWriter, r *http.Request) {
	p, err := s.Links.Look(r.Context(), r.Header.Get(invitationTokenHeader))
	if errors.Is(err, store.ErrInvalidLink) {
		writeError(w, http.StatusNotFound, codeInvitationInvalid, "this invitation is no longer valid")
		return
	}
	if err != nil {
		s.fail(w, err)
		return
	}
	var role *string
	if p.Role != nil {
		r := titleRole(*p.Role)
		role = &r
	}
	writeJSON(w, http.StatusOK, pendingBody{Kind: p.Kind, OrganisationName: p.OrganisationName, Role: role, Email: p.Email})
}

// titleRole is a stored role as the console names it.
func titleRole(stored string) string {
	switch stored {
	case "admin":
		return "Admin"
	case "member":
		return "Member"
	case "viewer":
		return "Viewer"
	}
	return stored
}

// acceptInvitation serves POST /auth/invitation/accept: it uses the link and signs the user in,
// renewing the session id. Every rule of sign-in applies, and its password tries count in
// sign-in's own limiter, keyed by the invitation's email and the client address.
func (s *Service) acceptInvitation(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	s.warnUntrustedProxy(r)
	if !s.passwordMayTravel(r) {
		writeError(w, http.StatusBadRequest, "https_required", "accepting an invitation needs HTTPS when the console is not on localhost")
		return
	}
	var body struct {
		Token    string `json:"token"`
		Password string `json:"password"`
	}
	if err := json.NewDecoder(io.LimitReader(r.Body, maxSignInBody)).Decode(&body); err != nil || body.Token == "" || body.Password == "" {
		writeError(w, http.StatusBadRequest, "invalid_request", "token and password are required")
		return
	}
	if err := s.Users.ForgetSignIns(ctx); err != nil {
		s.fail(w, err)
		return
	}
	address := addressKey(clientAddress(r, s.Trusted))
	limit := func(ctx context.Context, email string) (func(context.Context) error, time.Duration, error) {
		ids, wait, err := s.Users.ReserveSignIn(ctx, []store.SignInKey{
			{Kind: "email", Key: email, Max: limitPerEmail},
			{Kind: "address", Key: address, Max: limitPerAddress},
		})
		return func(ctx context.Context) error { return s.Users.ForgetSignInAttempts(ctx, ids) }, wait, err
	}
	p, err := s.Links.Accept(ctx, body.Token, body.Password, limit)
	var many store.TooManyAttempts
	switch {
	case errors.As(err, &many):
		w.Header().Set("Retry-After", strconv.Itoa(int(math.Ceil(many.Wait.Seconds()))))
		writeError(w, http.StatusTooManyRequests, "too_many_attempts", "too many failed sign-ins; try again later")
		return
	case errors.Is(err, store.ErrInvalidInvitation):
		writeError(w, http.StatusBadRequest, "invalid_request", "the password must be at least 12 characters")
		return
	case errors.Is(err, store.ErrInvalidLink):
		writeError(w, http.StatusNotFound, codeInvitationInvalid, "this invitation is no longer valid")
		return
	case errors.Is(err, store.ErrWrongPassword):
		writeError(w, http.StatusUnauthorized, "unauthenticated", "email or password is wrong")
		return
	case err != nil:
		s.fail(w, err)
		return
	}
	m := s.manager(r)
	if err := m.RenewToken(ctx); err != nil {
		s.fail(w, err)
		return
	}
	m.Put(ctx, sessionUser, p.UserID)
	m.Put(ctx, sessionPasswordVersion, p.PasswordVersion)
	w.WriteHeader(http.StatusNoContent)
}
