package api

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"unicode/utf8"

	"github.com/go-chi/chi/v5"

	"github.com/mahimailabs/voicegateway/internal/store"
)

type CallMetadataStore interface {
	CallMetadata(context.Context, string, string, string) (store.CallMetadata, error)
	UpdateCallMetadata(context.Context, string, string, string, int64, []string, bool) (store.CallMetadata, error)
}

func (a *API) getCallMetadata(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Cache-Control", "no-store")
	if a.Metadata == nil {
		writeError(w, r, http.StatusServiceUnavailable, codeSourceUnavailable, "call metadata unavailable")
		return
	}
	result, err := a.Metadata.CallMetadata(r.Context(), chi.URLParam(r, "orgId"), chi.URLParam(r, "projectId"), chi.URLParam(r, "callId"))
	if err != nil {
		a.fail(w, r, err, "call not found")
		return
	}
	writeJSON(w, http.StatusOK, result)
}

func (a *API) updateCallMetadata(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Cache-Control", "no-store")
	if a.Metadata == nil {
		writeError(w, r, http.StatusServiceUnavailable, codeSourceUnavailable, "call metadata unavailable")
		return
	}
	raw, err := io.ReadAll(http.MaxBytesReader(w, r.Body, 16384))
	if err != nil || !utf8.Valid(raw) {
		writeError(w, r, http.StatusBadRequest, codeInvalidRequest, "expected UTF-8 JSON at most 16 KiB")
		return
	}
	var body struct {
		Tags     *[]string `json:"tags"`
		Archived *bool     `json:"archived"`
		Revision *int64    `json:"revision"`
	}
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.DisallowUnknownFields()
	var trailing any
	if err := dec.Decode(&body); err != nil || !errors.Is(dec.Decode(&trailing), io.EOF) || body.Tags == nil || body.Archived == nil || body.Revision == nil {
		writeError(w, r, http.StatusBadRequest, codeInvalidRequest, "expected one JSON object with tags, archived and revision")
		return
	}
	result, err := a.Metadata.UpdateCallMetadata(r.Context(), chi.URLParam(r, "orgId"), chi.URLParam(r, "projectId"), chi.URLParam(r, "callId"), *body.Revision, *body.Tags, *body.Archived)
	switch {
	case errors.Is(err, store.ErrInvalidMetadata):
		writeError(w, r, http.StatusBadRequest, codeInvalidRequest, err.Error())
	case errors.Is(err, store.ErrMetadataConflict):
		writeError(w, r, http.StatusConflict, "revision_conflict", "call metadata changed; reload before saving")
	case err != nil:
		a.fail(w, r, err, "call not found")
	default:
		writeJSON(w, http.StatusOK, result)
	}
}
