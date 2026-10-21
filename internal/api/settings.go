package api

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"

	"github.com/go-chi/chi/v5"

	"github.com/mahimailabs/voicegateway/internal/store"
)

type ProjectSettingsStore interface {
	ProjectSettings(context.Context, string, string) (store.ProjectSettings, error)
	UpdateProjectSettings(context.Context, string, string, int64, store.Thresholds) (store.ProjectSettings, error)
}

type projectSettingsRead struct {
	store.ProjectSettings
	StorageBucket           *string   `json:"storageBucket"`
	TranscriptRetentionDays *int      `json:"transcriptRetentionDays"`
	TraceRetentionDays      *int      `json:"traceRetentionDays"`
	RecordingRetentionDays  *int      `json:"recordingRetentionDays"`
	AllowedOrigins          *[]string `json:"allowedOrigins"`
}

func (a *API) settingsResponse(settings store.ProjectSettings) projectSettingsRead {
	// Raw events, spans and metric points expire after 90 days (migration 00010).
	// Transcript and recording policies are not implemented here. Allowed origins are null until an
	// Admin sets them (decision 0033).
	days := 90
	out := projectSettingsRead{ProjectSettings: settings, TraceRetentionDays: &days}
	if settings.AllowedOrigins != nil {
		origins := settings.AllowedOrigins
		out.AllowedOrigins = &origins
	}
	if a.StorageBucket != "" {
		out.StorageBucket = &a.StorageBucket
	}
	return out
}

func (a *API) getProjectSettings(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Cache-Control", "no-store")
	if a.Settings == nil {
		writeError(w, r, http.StatusServiceUnavailable, codeSourceUnavailable, "project settings unavailable")
		return
	}
	settings, err := a.Settings.ProjectSettings(r.Context(), chi.URLParam(r, "orgId"), chi.URLParam(r, "projectId"))
	if err != nil {
		a.fail(w, r, err, "project not found")
		return
	}
	writeJSON(w, http.StatusOK, a.settingsResponse(settings))
}

func (a *API) updateProjectSettings(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Cache-Control", "no-store")
	if a.Settings == nil {
		writeError(w, r, http.StatusServiceUnavailable, codeSourceUnavailable, "project settings unavailable")
		return
	}
	var body struct {
		Thresholds *store.Thresholds `json:"thresholds"`
		Revision   *int64            `json:"revision"`
	}
	dec := json.NewDecoder(http.MaxBytesReader(w, r.Body, 4096))
	dec.DisallowUnknownFields()
	err := dec.Decode(&body)
	var trailing any
	if err != nil || !errors.Is(dec.Decode(&trailing), io.EOF) || body.Thresholds == nil || body.Revision == nil {
		writeError(w, r, http.StatusBadRequest, codeInvalidRequest, "expected one JSON object with thresholds and revision, at most 4 KiB")
		return
	}
	if body.Thresholds.Validate() != nil || *body.Revision < 0 || *body.Revision >= 9007199254740991 {
		writeError(w, r, http.StatusBadRequest, codeInvalidRequest, "thresholds must be between 100 and 60000 ms and revision must be valid")
		return
	}
	settings, err := a.Settings.UpdateProjectSettings(r.Context(), chi.URLParam(r, "orgId"), chi.URLParam(r, "projectId"), *body.Revision, *body.Thresholds)
	if errors.Is(err, store.ErrSettingsConflict) {
		writeError(w, r, http.StatusConflict, "revision_conflict", "settings changed; reload before saving")
		return
	}
	if err != nil {
		a.fail(w, r, err, "project not found")
		return
	}
	writeJSON(w, http.StatusOK, a.settingsResponse(settings))
}

// readSettings fails closed when settings cannot be read. Call queries must not
// silently evaluate against a different threshold during a storage failure.
func (a *API) readSettings(w http.ResponseWriter, r *http.Request) (store.ProjectSettings, bool) {
	if a.Settings == nil {
		writeError(w, r, http.StatusServiceUnavailable, codeSourceUnavailable, "project settings unavailable")
		return store.ProjectSettings{}, false
	}
	settings, err := a.Settings.ProjectSettings(r.Context(), chi.URLParam(r, "orgId"), chi.URLParam(r, "projectId"))
	if err != nil {
		a.fail(w, r, err, "project not found")
		return store.ProjectSettings{}, false
	}
	return settings, true
}
