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
	gate := a.Gate
	if gate == nil {
		gate = func(next http.Handler) http.Handler { return next }
	}
	r.Route("/api/v1/org/{orgId}/project/{projectId}", func(r chi.Router) {
		r.Use(gate)
		r.Get("/calls/{callId}", a.getCall)
	})
	return r
}
