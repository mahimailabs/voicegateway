package api

import (
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"time"
	"unicode/utf8"

	"github.com/go-chi/chi/v5"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// maxKeyBody bounds a createProjectKey body, which holds one name.
const maxKeyBody = 1 << 10

// projectKey is the contract's ProjectKey: never the secret.
type projectKey struct {
	KeyID      string     `json:"keyId"`
	Name       string     `json:"name"`
	Prefix     string     `json:"prefix"`
	CreatedAt  time.Time  `json:"createdAt"`
	CreatedBy  *string    `json:"createdBy"`
	LastUsedAt *time.Time `json:"lastUsedAt"`
	RevokedAt  *time.Time `json:"revokedAt"`
}

// createdProjectKey is the contract's CreatedProjectKey: the list fields and the full key, which
// this answer is the only place to show.
type createdProjectKey struct {
	projectKey
	Secret string `json:"secret"`
}

type projectKeys struct {
	Items []projectKey `json:"items"`
}

func toProjectKey(k store.ProjectKey) projectKey {
	return projectKey{KeyID: k.KeyID, Name: k.Name, Prefix: "vgk_" + k.KeyID, CreatedAt: k.CreatedAt, CreatedBy: k.CreatedBy, LastUsedAt: k.LastUsedAt, RevokedAt: k.RevokedAt}
}

// listProjectKeys serves operationId listProjectKeys: a project's keys, newest first.
func (a *API) listProjectKeys(w http.ResponseWriter, r *http.Request) {
	org, proj := chi.URLParam(r, "orgId"), chi.URLParam(r, "projectId")
	if _, err := a.Projects.Project(r.Context(), org, proj); err != nil {
		a.fail(w, r, err, "project not found")
		return
	}
	keys, err := a.Keys.ProjectKeys(r.Context(), org, proj)
	if err != nil {
		a.fail(w, r, err, "")
		return
	}
	out := projectKeys{Items: make([]projectKey, 0, len(keys))}
	for _, k := range keys {
		out.Items = append(out.Items, toProjectKey(k))
	}
	writeJSON(w, http.StatusOK, out)
}

// createProjectKey serves operationId createProjectKey. The full key is in this answer only; the
// store keeps its SHA-256. A drawn key_id that is already taken (60 random bits) is drawn again,
// once.
func (a *API) createProjectKey(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Name string `json:"name"`
	}
	dec := json.NewDecoder(io.LimitReader(r.Body, maxKeyBody))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&body); err != nil || body.Name == "" || utf8.RuneCountInString(body.Name) > 64 {
		writeError(w, r, http.StatusBadRequest, codeInvalidRequest, "name must be 1 to 64 characters")
		return
	}
	org, proj := chi.URLParam(r, "orgId"), chi.URLParam(r, "projectId")
	if _, err := a.Projects.Project(r.Context(), org, proj); err != nil {
		a.fail(w, r, err, "project not found")
		return
	}
	p, _ := store.PrincipalFrom(r.Context())
	for attempt := 0; ; attempt++ {
		keyID, sum, full, err := a.NewKey()
		if err != nil {
			a.fail(w, r, err, "")
			return
		}
		err = a.Keys.CreateProjectKey(r.Context(), org, proj, keyID, body.Name, sum, p.UserID)
		if errors.Is(err, store.ErrKeyExists) && attempt == 0 {
			continue
		}
		if err != nil {
			a.fail(w, r, err, "project not found")
			return
		}
		var by *string
		if p.Email != "" {
			e := p.Email
			by = &e
		}
		writeJSON(w, http.StatusCreated, createdProjectKey{
			projectKey: projectKey{KeyID: keyID, Name: body.Name, Prefix: "vgk_" + keyID, CreatedAt: a.Now().UTC(), CreatedBy: by},
			Secret:     full,
		})
		return
	}
}

// revokeProjectKey serves operationId revokeProjectKey; revoking twice is 204 both times.
func (a *API) revokeProjectKey(w http.ResponseWriter, r *http.Request) {
	err := a.Keys.RevokeProjectKey(r.Context(), chi.URLParam(r, "orgId"), chi.URLParam(r, "projectId"), chi.URLParam(r, "keyId"))
	if err != nil {
		a.fail(w, r, err, "no such key in this project")
		return
	}
	w.WriteHeader(http.StatusNoContent)
}
