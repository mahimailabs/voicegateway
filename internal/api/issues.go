package api

import (
	"net/http"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/go-chi/chi/v5"

	"github.com/mahimailabs/voicegateway/internal/store"
)

func (a *API) listIssues(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Cache-Control", "no-store")
	if a.Issues == nil {
		writeError(w, r, http.StatusServiceUnavailable, codeSourceUnavailable, "issue source unavailable")
		return
	}
	q := r.URL.Query()
	from, err := time.Parse(time.RFC3339Nano, q.Get("from"))
	if err != nil {
		writeError(w, r, http.StatusBadRequest, codeInvalidRequest, "from must be an RFC 3339 date-time")
		return
	}
	to, err := time.Parse(time.RFC3339Nano, q.Get("to"))
	if err != nil || !from.Before(to) {
		writeError(w, r, http.StatusBadRequest, codeInvalidRequest, "to must be an RFC 3339 date-time after from")
		return
	}
	search := strings.TrimSpace(q.Get("q"))
	if !utf8.ValidString(search) || len(search) > 512 || strings.ContainsRune(search, 0) {
		writeError(w, r, http.StatusBadRequest, codeInvalidRequest, "search must be UTF-8 text at most 512 bytes")
		return
	}
	tags, err := store.NormalizeTags(q["tag"])
	if err != nil {
		writeError(w, r, http.StatusBadRequest, codeInvalidRequest, err.Error())
		return
	}
	var archived *bool
	if q.Has("archived") {
		value := q.Get("archived")
		if value != "true" && value != "false" {
			writeError(w, r, http.StatusBadRequest, codeInvalidRequest, "archived must be true or false")
			return
		}
		valueBool := value == "true"
		archived = &valueBool
	}
	result, err := a.Issues.ListIssues(r.Context(), chi.URLParam(r, "orgId"), chi.URLParam(r, "projectId"), store.IssueFilter{From: from.UTC(), To: to.UTC(), Search: search, Tags: tags, Archived: archived})
	if err != nil {
		a.fail(w, r, err, "project not found")
		return
	}
	writeJSON(w, http.StatusOK, result)
}
