package auth

import (
	"bytes"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"unicode/utf8"

	"github.com/mahimailabs/voicegateway/internal/store"
)

func (s *Service) profile(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Cache-Control", "no-store")
	p, ok, err := s.userPrincipal(r)
	if err != nil {
		s.fail(w, err)
		return
	}
	if !ok {
		writeError(w, http.StatusUnauthorized, "unauthenticated", "not signed in")
		return
	}
	profile, err := s.Users.AccountProfile(r.Context(), p.UserID)
	if err != nil {
		s.fail(w, err)
		return
	}
	writeJSON(w, http.StatusOK, profile)
}

func (s *Service) updateProfile(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Cache-Control", "no-store")
	p, ok, err := s.userPrincipal(r)
	if err != nil {
		s.fail(w, err)
		return
	}
	if !ok {
		writeError(w, http.StatusUnauthorized, "unauthenticated", "not signed in")
		return
	}
	raw, err := io.ReadAll(http.MaxBytesReader(w, r.Body, 4096))
	if err != nil || !utf8.Valid(raw) {
		writeError(w, http.StatusBadRequest, "invalid_request", "expected UTF-8 JSON at most 4 KiB")
		return
	}
	var body struct {
		Name     *string `json:"name"`
		Revision *int64  `json:"revision"`
	}
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.DisallowUnknownFields()
	var trailing any
	if err := dec.Decode(&body); err != nil || !errors.Is(dec.Decode(&trailing), io.EOF) || body.Name == nil || body.Revision == nil {
		writeError(w, http.StatusBadRequest, "invalid_request", "expected one JSON object with name and revision")
		return
	}
	profile, err := s.Users.UpdateAccountProfile(r.Context(), p.UserID, *body.Revision, *body.Name)
	switch {
	case errors.Is(err, store.ErrInvalidProfile), errors.Is(err, store.ErrInvalidProfileRevision):
		writeError(w, http.StatusBadRequest, "invalid_request", err.Error())
	case errors.Is(err, store.ErrProfileConflict):
		writeError(w, http.StatusConflict, "revision_conflict", "profile changed; reload before saving")
	case err != nil:
		s.fail(w, err)
	default:
		writeJSON(w, http.StatusOK, profile)
	}
}
