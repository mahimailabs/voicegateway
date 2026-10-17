package api

import (
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
	// Gate runs before every /api/v1 route: BearerToken until the sign-in decision (#321) lands.
	// Nil passes every request through, for tests; reads stay scoped to the organisation and
	// project in the path either way.
	Gate func(http.Handler) http.Handler
}

// Routes returns the /api/v1 handler, to mount at the server's root.
func (a *API) Routes() http.Handler {
	r := chi.NewRouter()
	r.Use(middleware.RequestID)
	// The gate runs on the whole router, so an unknown /api/v1 path is gated too; what is not a
	// route answers in the contract's Error shape.
	if a.Gate != nil {
		r.Use(a.Gate)
	}
	r.NotFound(func(w http.ResponseWriter, r *http.Request) {
		writeError(w, r, http.StatusNotFound, codeNotFound, "no such API path")
	})
	r.MethodNotAllowed(func(w http.ResponseWriter, r *http.Request) {
		writeError(w, r, http.StatusMethodNotAllowed, codeInvalidRequest, "method not allowed")
	})
	r.Route("/api/v1/org/{orgId}/project/{projectId}", func(r chi.Router) {
		r.Get("/calls/{callId}", a.getCall)
		r.Get("/media-server", a.mediaServer)
		r.Get("/sip", a.sip)
	})
	return r
}
