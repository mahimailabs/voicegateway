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

type IssueMetadataStore interface {
	IssueMetadata(context.Context, string, string, string) (store.IssueMetadata, error)
	UpdateIssueMetadata(context.Context, string, string, string, int64, int64, *string, []string, bool) (store.IssueMetadata, error)
}

func (a *API) getIssueMetadata(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Cache-Control", "no-store")
	if a.Issues == nil {
		writeError(w, r, http.StatusServiceUnavailable, codeSourceUnavailable, "issue metadata unavailable")
		return
	}
	result, err := a.Issues.IssueMetadata(r.Context(), chi.URLParam(r, "orgId"), chi.URLParam(r, "projectId"), chi.URLParam(r, "issueId"))
	if err != nil {
		a.fail(w, r, err, "issue not found")
		return
	}
	writeJSON(w, http.StatusOK, result)
}

func (a *API) updateIssueMetadata(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Cache-Control", "no-store")
	if a.Issues == nil {
		writeError(w, r, http.StatusServiceUnavailable, codeSourceUnavailable, "issue metadata unavailable")
		return
	}
	raw, err := io.ReadAll(http.MaxBytesReader(w, r.Body, 16384))
	if err != nil || !utf8.Valid(raw) {
		writeError(w, r, http.StatusBadRequest, codeInvalidRequest, "expected UTF-8 JSON at most 16 KiB")
		return
	}
	var body struct {
		Tags             *[]string `json:"tags"`
		Archived         *bool     `json:"archived"`
		Revision         *int64    `json:"revision"`
		EvidenceRevision *int64    `json:"evidenceRevision"`
		State            *string   `json:"state"`
	}
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.DisallowUnknownFields()
	var trailing any
	if err := dec.Decode(&body); err != nil || !errors.Is(dec.Decode(&trailing), io.EOF) || body.Tags == nil || body.Archived == nil || body.Revision == nil || body.EvidenceRevision == nil {
		writeError(w, r, http.StatusBadRequest, codeInvalidRequest, "expected one JSON object with tags, archived, revision and evidenceRevision")
		return
	}
	result, err := a.Issues.UpdateIssueMetadata(r.Context(), chi.URLParam(r, "orgId"), chi.URLParam(r, "projectId"), chi.URLParam(r, "issueId"), *body.Revision, *body.EvidenceRevision, body.State, *body.Tags, *body.Archived)
	switch {
	case errors.Is(err, store.ErrInvalidMetadata), errors.Is(err, store.ErrInvalidIssueState):
		writeError(w, r, http.StatusBadRequest, codeInvalidRequest, err.Error())
	case errors.Is(err, store.ErrIssueConflict):
		writeError(w, r, http.StatusConflict, "revision_conflict", "issue metadata changed; reload before saving")
	case err != nil:
		a.fail(w, r, err, "issue not found")
	default:
		writeJSON(w, http.StatusOK, result)
	}
}
