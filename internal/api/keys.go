package api

import (
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"regexp"
	"strings"
	"time"
	"unicode"
	"unicode/utf8"

	"github.com/go-chi/chi/v5"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// maxKeyBody bounds a createProjectKey body, which holds one name.
const maxKeyBody = 1 << 10

// errKeyName says a key name breaks the rule KeyName checks.
var errKeyName = errors.New("must be 1 to 64 characters, with no control characters")

// KeyName is the one rule for a project key's name, shared by createProjectKey and voicegateway key
// create: trimmed, then 1 to 64 characters with no control characters.
func KeyName(name string) (string, error) {
	name = strings.TrimSpace(name)
	if name == "" || utf8.RuneCountInString(name) > 64 || strings.ContainsFunc(name, unicode.IsControl) {
		return "", errKeyName
	}
	return name, nil
}

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
		a.failWith(w, r, err, "project not found", "the project's API keys could not be read")
		return
	}
	keys, err := a.Keys.ProjectKeys(r.Context(), org, proj)
	if err != nil {
		a.failWith(w, r, err, "", "the project's API keys could not be read")
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
	err := dec.Decode(&body)
	name, nameErr := KeyName(body.Name)
	if err != nil || nameErr != nil {
		writeError(w, r, http.StatusBadRequest, codeInvalidRequest, "name must be 1 to 64 characters, with no control characters")
		return
	}
	body.Name = name
	org, proj := chi.URLParam(r, "orgId"), chi.URLParam(r, "projectId")
	if _, err := a.Projects.Project(r.Context(), org, proj); err != nil {
		a.failWith(w, r, err, "project not found", "the key could not be stored")
		return
	}
	p, _ := store.PrincipalFrom(r.Context())
	for attempt := 0; ; attempt++ {
		keyID, sum, full, err := a.NewKey()
		if err != nil {
			a.failWith(w, r, err, "", "the key could not be stored")
			return
		}
		createdAt, err := a.Keys.CreateProjectKey(r.Context(), org, proj, keyID, body.Name, sum, p.UserID)
		if errors.Is(err, store.ErrKeyExists) && attempt == 0 {
			continue
		}
		if err != nil {
			a.failWith(w, r, err, "project not found", "the key could not be stored")
			return
		}
		var by *string
		if p.Email != "" {
			e := p.Email
			by = &e
		}
		writeJSON(w, http.StatusCreated, createdProjectKey{
			projectKey: projectKey{KeyID: keyID, Name: body.Name, Prefix: "vgk_" + keyID, CreatedAt: createdAt, CreatedBy: by},
			Secret:     full,
		})
		return
	}
}

// keyIDPattern is a key_id: 12 characters of lowercase base32.
var keyIDPattern = regexp.MustCompile(`^[a-z2-7]{12}$`)

// revokeProjectKey serves operationId revokeProjectKey; revoking twice is 204 both times.
func (a *API) revokeProjectKey(w http.ResponseWriter, r *http.Request) {
	keyID := chi.URLParam(r, "keyId")
	if !keyIDPattern.MatchString(keyID) {
		writeError(w, r, http.StatusBadRequest, codeInvalidRequest, "keyId must be 12 characters of a to z and 2 to 7")
		return
	}
	err := a.Keys.RevokeProjectKey(r.Context(), chi.URLParam(r, "orgId"), chi.URLParam(r, "projectId"), keyID)
	if err != nil {
		a.failWith(w, r, err, "no such key in this project", "the key could not be revoked")
		return
	}
	w.WriteHeader(http.StatusNoContent)
}
