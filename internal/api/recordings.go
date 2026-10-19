package api

import (
	"errors"
	"net/http"

	"github.com/go-chi/chi/v5"
	"github.com/go-chi/chi/v5/middleware"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// getRecordingMedia serves operationId getRecordingMedia: a stored recording's Ogg audio, with byte
// ranges for the console's player. The id is looked up within the project the gate authorised, so
// an id from another project is not found; the key is the stored one, never one from the request.
func (a *API) getRecordingMedia(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	org, project, id := chi.URLParam(r, "orgId"), chi.URLParam(r, "projectId"), chi.URLParam(r, "recordingId")
	rec, err := a.Recordings.Recording(ctx, org, project, id)
	if err != nil {
		a.failWith(w, r, err, "recording not found", "the recording could not be read")
		return
	}
	// The worker forgets expired recordings once an hour; one past retention is gone already.
	if rec.StoredAt.Before(a.Now().Add(-store.RecordingKeep)) {
		writeError(w, r, http.StatusNotFound, codeNotFound, "recording not found")
		return
	}
	media, err := a.Media.Open(ctx, rec.ObjectKey)
	if errors.Is(err, store.ErrNotFound) {
		writeError(w, r, http.StatusNotFound, codeNotFound, "recording not found")
		return
	}
	if err != nil {
		a.Log.Error("api: recording unavailable", "path", r.URL.Path, "request_id", middleware.GetReqID(ctx), "err", err)
		writeError(w, r, http.StatusServiceUnavailable, codeSourceUnavailable, "the recording store is unavailable")
		return
	}
	defer func() { _ = media.Close() }()
	w.Header().Set("Content-Type", "audio/ogg")
	w.Header().Set("Cache-Control", "private, max-age=300")
	// ServeContent answers Range and If-Modified-Since, and sets Accept-Ranges.
	http.ServeContent(w, r, "", rec.StoredAt, media)
}
