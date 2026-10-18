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

// API serves the console's /api/v1 reads.
type API struct {
	Projects Projects
	Calls    Calls
	Traces   Traces
	Layers   Layers
	Accounts Accounts
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
	r.Get("/api/v1/workspaces", a.getWorkspaces)
	r.Route("/api/v1/org/{orgId}/project/{projectId}", func(r chi.Router) {
		r.Use(member)
		r.Get("/calls", a.listCalls)
		r.Get("/calls/{callId}", a.getCall)
		r.Get("/media-server", a.mediaServer)
		r.Get("/sip", a.sip)
		r.Get("/egress", a.egress)
		r.Get("/ingress", a.ingress)
	})
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

// member answers 403 a signed-in user outside the organisation in the path. The interim token
// names no user and reads every organisation until 06c replaces it with project keys.
func member(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		p, _ := store.PrincipalFrom(r.Context())
		if !p.Token && p.Roles[chi.URLParam(r, "orgId")] == "" {
			writeError(w, r, http.StatusForbidden, codeForbidden, "you are not a member of this organisation")
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
