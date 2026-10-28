package api

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"time"

	"github.com/go-chi/chi/v5"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// LiveChanges subscribes only after Redis acknowledges the project subscription. Closing the
// returned channel reports a lost connection, not an empty or healthy source.
type LiveChanges interface {
	Watch(context.Context, string, string) (<-chan struct{}, func(), error)
}

func (a *API) liveEvents(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Cache-Control", "no-store")
	org, project := chi.URLParam(r, "orgId"), chi.URLParam(r, "projectId")
	if _, err := a.Projects.Project(r.Context(), org, project); err != nil {
		if errors.Is(err, store.ErrNotFound) {
			writeError(w, r, http.StatusNotFound, codeNotFound, "no such project")
			return
		}
		a.internal(w, r, "find project", err)
		return
	}
	if a.Live == nil {
		writeError(w, r, http.StatusServiceUnavailable, codeSourceUnavailable, "live updates unavailable")
		return
	}
	// Bound every connection, including idle ones. A renewal re-enters the normal session/key and
	// project gates; no long-lived credential or telemetry payload is carried by this stream.
	ctx, cancel := context.WithTimeout(r.Context(), 15*time.Second)
	defer cancel()
	changes, closeWatch, err := a.Live.Watch(ctx, org, project)
	if err != nil {
		writeError(w, r, http.StatusServiceUnavailable, codeSourceUnavailable, "live updates unavailable")
		return
	}
	defer closeWatch()
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("X-Accel-Buffering", "no")
	controller := http.NewResponseController(w)
	send := func(event string) bool {
		_ = controller.SetWriteDeadline(time.Now().Add(5 * time.Second))
		if _, err := fmt.Fprintf(w, "event: %s\ndata: {}\n\n", event); err != nil {
			return false
		}
		return controller.Flush() == nil
	}
	// Refresh after subscription, including reconnect, to repair any missed pub/sub hints.
	if !send("refresh") {
		return
	}
	for {
		select {
		case <-ctx.Done():
			if r.Context().Err() == nil {
				send("renew")
			}
			return
		case _, ok := <-changes:
			if !ok {
				if ctx.Err() != nil && r.Context().Err() == nil {
					send("renew")
				}
				return
			}
			if !send("refresh") {
				return
			}
		}
	}
}
