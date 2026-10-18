package api

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"log/slog"
	"net/http"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/go-chi/chi/v5/middleware"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// API serves the console's /api/v1 reads and its member writes.
type API struct {
	Projects Projects
	Calls    Calls
	Traces   Traces
	Layers   Layers
	Accounts Accounts
	Stats    Stats
	Log      *slog.Logger
	// AnswerBudgetMs is the answer budget listCalls' totals count against; 0 is the console's
	// default, 1500 ms. Interim, until thresholds are stored per project.
	AnswerBudgetMs int
	Now            func() time.Time
	// Gate runs before every /api/v1 route: auth.Service.Gate, which identifies a signed-in user or
	// the interim token (decision 0027).
	// Nil denies every request, so a router wired without a gate never serves openly.
	Gate func(http.Handler) http.Handler
}

// Routes returns the /api/v1 handler, to mount at the server's root.
func (a *API) Routes() http.Handler {
	r := chi.NewRouter()
	r.Use(requestID)
	// The gate runs on the whole router, so an unknown /api/v1 path is gated too; what is not a
	// route answers in the contract's Error shape.
	// The gate identifies the caller; requirePrincipal refuses any request it could not identify,
	// so a router wired without a gate serves nothing.
	if a.Gate != nil {
		r.Use(a.Gate)
	}
	r.Use(requirePrincipal)
	r.NotFound(func(w http.ResponseWriter, r *http.Request) {
		writeError(w, r, http.StatusNotFound, codeNotFound, "no such API path")
	})
	r.MethodNotAllowed(func(w http.ResponseWriter, r *http.Request) {
		writeError(w, r, http.StatusMethodNotAllowed, codeInvalidRequest, "method not allowed")
	})
	// Every route goes through handle, which puts it behind its minimum role in routeAccess.
	const project = "/api/v1/org/{orgId}/project/{projectId}"
	handle(r, http.MethodGet, "/api/v1/workspaces", a.getWorkspaces)
	handle(r, http.MethodGet, project+"/calls", a.listCalls)
	handle(r, http.MethodGet, project+"/calls/{callId}", a.getCall)
	handle(r, http.MethodGet, project+"/call-stats", a.getCallStats)
	handle(r, http.MethodGet, project+"/media-server", a.mediaServer)
	handle(r, http.MethodGet, project+"/sip", a.sip)
	handle(r, http.MethodGet, project+"/egress", a.egress)
	handle(r, http.MethodGet, project+"/ingress", a.ingress)
	handle(r, http.MethodGet, "/api/v1/org/{orgId}/members", a.getMembers)
	handle(r, http.MethodPatch, "/api/v1/org/{orgId}/members/{userId}", a.updateMember)
	handle(r, http.MethodDelete, "/api/v1/org/{orgId}/members/{userId}", a.removeMember)
	return r
}

// requirePrincipal answers 401, with the Bearer challenge, any request the gate found no caller on.
func requirePrincipal(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if _, ok := store.PrincipalFrom(r.Context()); !ok {
			unauthenticated(w, r, "sign in, or send a valid API token")
			return
		}
		next.ServeHTTP(w, r)
	})
}

// requestID gives every request an id the server makes, for the Error body and the log. A client's
// X-Request-Id is ignored, so a caller cannot choose the id an operator searches the log for.
func requestID(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var b [12]byte
		if _, err := rand.Read(b[:]); err != nil {
			writeJSON(w, http.StatusInternalServerError, apiError{Code: codeInternal, Message: "no request id"})
			return
		}
		next.ServeHTTP(w, r.WithContext(context.WithValue(r.Context(), middleware.RequestIDKey, hex.EncodeToString(b[:]))))
	})
}
