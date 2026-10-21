package api

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"strings"

	"github.com/go-chi/chi/v5"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// maxCredentialsBody bounds an updateProjectLiveKitCredentials body: a key and a secret.
const maxCredentialsBody = 4 << 10

// LiveKitCredentials replaces a project's LiveKit key and secret, the secret sealed.
type LiveKitCredentials interface {
	UpdateLiveKitCredentials(ctx context.Context, organisationID, projectID, apiKey, apiSecret string) error
}

// CheckLiveKitCredentials applies createProject's rules to a key and secret alone: the key trimmed,
// at most 256 characters with no spaces or control characters, the secret kept as given and more
// than blanks. The error is a *ProjectInputError.
func CheckLiveKitCredentials(key, secret string) (string, error) {
	key = strings.TrimSpace(key)
	var missing []string
	if key == "" {
		missing = append(missing, FieldLiveKitAPIKey)
	}
	if strings.TrimSpace(secret) == "" {
		missing = append(missing, FieldLiveKitAPISecret)
	}
	if len(missing) > 0 {
		return "", &ProjectInputError{Missing: missing}
	}
	if len(key) > maxLiveKitKey || !plainToken(key) {
		return "", &ProjectInputError{Field: FieldLiveKitAPIKey, Rule: "must be at most 256 characters, with no spaces or control characters"}
	}
	return key, nil
}

// updateLiveKitCredentials serves updateProjectLiveKitCredentials: a new key and secret for the
// project's stored LiveKit URL, proven with one signed LiveKit read before they are stored. The
// secret is never logged or answered.
func (a *API) updateLiveKitCredentials(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Cache-Control", "no-store")
	if a.Credentials == nil || a.VerifyLiveKit == nil {
		writeError(w, r, http.StatusForbidden, "forbidden", "changing LiveKit credentials from the console is off on this install")
		return
	}
	var body struct {
		LiveKitAPIKey    string `json:"livekitApiKey"`
		LiveKitAPISecret string `json:"livekitApiSecret"`
	}
	dec := json.NewDecoder(http.MaxBytesReader(w, r.Body, maxCredentialsBody))
	dec.DisallowUnknownFields()
	var trailing any
	if err := dec.Decode(&body); err != nil || !errors.Is(dec.Decode(&trailing), io.EOF) {
		writeError(w, r, http.StatusBadRequest, codeInvalidRequest, "the body must be one JSON object of livekitApiKey and livekitApiSecret, at most 4 KiB")
		return
	}
	key, err := CheckLiveKitCredentials(body.LiveKitAPIKey, body.LiveKitAPISecret)
	if err != nil {
		writeError(w, r, http.StatusBadRequest, codeInvalidRequest, err.Error())
		return
	}
	org, id := chi.URLParam(r, "orgId"), chi.URLParam(r, "projectId")
	p, err := a.Projects.Project(r.Context(), org, id)
	if err != nil {
		a.failWith(w, r, err, "project not found", "the project could not be read")
		return
	}
	// As at create: the cause is not logged or answered, since it can name addresses the guard refused.
	switch err := a.VerifyLiveKit.VerifyLiveKit(r.Context(), p.LiveKitURL, key, body.LiveKitAPISecret); {
	case errors.Is(err, store.ErrLiveKitSecretInvalid):
		writeError(w, r, http.StatusUnprocessableEntity, codeLiveKitSecretInvalid, "the LiveKit API secret did not verify for this key")
		return
	case err != nil:
		writeError(w, r, http.StatusUnprocessableEntity, codeLiveKitUnreachable, "could not reach this project's LiveKit URL")
		return
	}
	if err := a.Credentials.UpdateLiveKitCredentials(r.Context(), org, id, key, body.LiveKitAPISecret); err != nil {
		a.failWith(w, r, err, "project not found", "the credentials could not be stored")
		return
	}
	w.WriteHeader(http.StatusNoContent)
}
