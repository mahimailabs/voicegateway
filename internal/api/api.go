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
)

// API serves the console's /api/v1 reads.
type API struct {
	Projects Projects
	Calls    Calls
	Traces   Traces
	Layers   Layers
	Log      *slog.Logger
	Now      func() time.Time
	// Gate runs before every /api/v1 route: BearerToken until the sign-in decision (0027) lands.
	// Nil denies every request, so a router wired without a gate never serves openly.
	Gate func(http.Handler) http.Handler
}

// Routes returns the /api/v1 handler, to mount at the server's root.
func (a *API) Routes() http.Handler {
	r := chi.NewRouter()
	r.Use(requestID)
	// The gate runs on the whole router, so an unknown /api/v1 path is gated too; what is not a
	// route answers in the contract's Error shape.
	gate := a.Gate
	if gate == nil {
		gate = denyAll
	}
	r.Use(gate)
	r.NotFound(func(w http.ResponseWriter, r *http.Request) {
		writeError(w, r, http.StatusNotFound, codeNotFound, "no such API path")
	})
	r.MethodNotAllowed(func(w http.ResponseWriter, r *http.Request) {
		writeError(w, r, http.StatusMethodNotAllowed, codeInvalidRequest, "method not allowed")
	})
	r.Route("/api/v1/org/{orgId}/project/{projectId}", func(r chi.Router) {
		r.Get("/calls/{callId}", a.getCall)
		r.Get("/media-server", a.mediaServer)
	})
	return r
}

// denyAll is the gate of a router wired without one: every request is 401.
func denyAll(http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		unauthenticated(w, r, "the API has no access gate configured")
	})
}

// requestID gives every request an id the server makes, for the Error body and the log. A client's
// X-Request-Id is ignored, so a caller cannot choose the id an operator searches the log for.
func requestID(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var b [12]byte
		if _, err := rand.Read(b[:]); err != nil {
			http.Error(w, `{"code":"internal_error","message":"no request id","requestId":""}`, http.StatusInternalServerError)
			return
		}
		next.ServeHTTP(w, r.WithContext(context.WithValue(r.Context(), middleware.RequestIDKey, hex.EncodeToString(b[:]))))
	})
}
