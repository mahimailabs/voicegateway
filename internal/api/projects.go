package api

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"regexp"
	"strings"
	"unicode"
	"unicode/utf8"

	"github.com/go-chi/chi/v5"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// ProjectCreator creates a project with its LiveKit secret sealed: pgstore.Store.CreateProject, the
// path voicegateway project add takes too. Its errors are store.ErrProjectExists,
// store.ErrLiveKitKeyInUse and store.ErrNotFound for an unknown organisation.
type ProjectCreator interface {
	CreateProject(ctx context.Context, p store.NewProject) (store.Project, error)
}

// maxProjectBody bounds a createProject body, which holds a name, a URL, a key and a secret.
const maxProjectBody = 4 << 10

// Contract Error codes for createProject.
const (
	codeProjectExists   = "project_exists"
	codeLiveKitKeyInUse = "livekit_key_in_use"
)

// The fields of a new project, by their contract names; ProjectInputError names them this way, and
// voicegateway project add maps them to its flags.
const (
	FieldName             = "name"
	FieldID               = "id"
	FieldLiveKitURL       = "livekitUrl"
	FieldLiveKitAPIKey    = "livekitApiKey"
	FieldLiveKitAPISecret = "livekitApiSecret"
)

// ProjectInputError says which fields of a new project are missing, or which one breaks its rule.
type ProjectInputError struct {
	// Missing are the empty fields, in the order the form and the command line ask for them.
	Missing []string
	// Field breaks Rule; both are empty when only fields are missing.
	Field, Rule string
}

func (e *ProjectInputError) Error() string { return e.Describe(nil) }

// Describe is the error with each field under the caller's own name for it, such as a flag; a field
// names maps nothing for keeps its contract name.
func (e *ProjectInputError) Describe(names map[string]string) string {
	name := func(f string) string {
		if n, ok := names[f]; ok {
			return n
		}
		return f
	}
	if len(e.Missing) > 0 {
		out := make([]string, len(e.Missing))
		for i, f := range e.Missing {
			out[i] = name(f)
		}
		return "missing " + strings.Join(out, ", ")
	}
	return name(e.Field) + " " + e.Rule
}

// projectIDPattern is a project's URL id, as the projects table checks it.
var projectIDPattern = regexp.MustCompile(`^[a-z0-9][a-z0-9-]{0,62}$`)

var nonSlug = regexp.MustCompile(`[^a-z0-9]+`)

// Slug turns a project name into its URL id, the same way the console does: "Default project"
// becomes "default-project". It is cut to the 63 characters a project id may have.
func Slug(name string) string {
	s := strings.Trim(nonSlug.ReplaceAllString(strings.ToLower(name), "-"), "-")
	if len(s) > 63 {
		s = strings.TrimRight(s[:63], "-")
	}
	return s
}

// Limits on what a new project's fields may hold, so a request cannot store an unbounded row.
const (
	maxProjectName = 64
	maxLiveKitURL  = 2048
	maxLiveKitKey  = 256
)

// CheckProject is the one rule for a new project, shared by createProject and voicegateway project
// add. Every field is required. The name is trimmed, then 1 to 64 characters with no control
// characters and at least one letter or digit, so it has an id. The id, when empty, is the name's
// Slug. The LiveKit URL parses with the scheme ws, wss, http or https and a host. The key is trimmed
// and at most 256 characters with no spaces or control characters. The secret is kept exactly as
// given; it only has to be more than blanks. The error is a *ProjectInputError.
func CheckProject(p store.NewProject) (store.NewProject, error) {
	p.Name = strings.TrimSpace(p.Name)
	p.LiveKitURL = strings.TrimSpace(p.LiveKitURL)
	p.LiveKitAPIKey = strings.TrimSpace(p.LiveKitAPIKey)
	var missing []string
	for _, f := range []struct{ name, value string }{
		{FieldName, p.Name}, {FieldLiveKitURL, p.LiveKitURL}, {FieldLiveKitAPIKey, p.LiveKitAPIKey}, {FieldLiveKitAPISecret, strings.TrimSpace(p.LiveKitAPISecret)},
	} {
		if f.value == "" {
			missing = append(missing, f.name)
		}
	}
	if len(missing) > 0 {
		return store.NewProject{}, &ProjectInputError{Missing: missing}
	}
	if utf8.RuneCountInString(p.Name) > maxProjectName || strings.ContainsFunc(p.Name, unicode.IsControl) || (p.ID == "" && Slug(p.Name) == "") {
		return store.NewProject{}, &ProjectInputError{Field: FieldName, Rule: "must be 1 to 64 characters, with no control characters and at least one letter or digit"}
	}
	if p.ID == "" {
		p.ID = Slug(p.Name)
	}
	if !projectIDPattern.MatchString(p.ID) {
		return store.NewProject{}, &ProjectInputError{Field: FieldID, Rule: "must be 1 to 63 characters of a to z, 0 to 9 and -, starting with a letter or digit"}
	}
	if !liveKitURL(p.LiveKitURL) {
		return store.NewProject{}, &ProjectInputError{Field: FieldLiveKitURL, Rule: "must be a ws, wss, http or https URL with a host"}
	}
	if len(p.LiveKitAPIKey) > maxLiveKitKey || strings.ContainsFunc(p.LiveKitAPIKey, func(r rune) bool { return unicode.IsSpace(r) || unicode.IsControl(r) }) {
		return store.NewProject{}, &ProjectInputError{Field: FieldLiveKitAPIKey, Rule: "must be at most 256 characters, with no spaces or control characters"}
	}
	return p, nil
}

// liveKitURL reports whether raw is a URL the LiveKit server SDK can dial: ws, wss, http or https,
// with a host and no user information.
func liveKitURL(raw string) bool {
	if len(raw) > maxLiveKitURL {
		return false
	}
	u, err := url.Parse(raw)
	if err != nil || u.Hostname() == "" || u.User != nil {
		return false
	}
	switch u.Scheme {
	case "ws", "wss", "http", "https":
		return true
	}
	return false
}

// createProject serves operationId createProject: an Admin adds a LiveKit project to the
// organisation, through the same check and create path as voicegateway project add. The secret is
// sealed by the store and never answered or logged.
func (a *API) createProject(w http.ResponseWriter, r *http.Request) {
	if a.NewProjects == nil {
		writeError(w, r, http.StatusForbidden, "forbidden", "adding projects from the console is off on this install")
		return
	}
	var body struct {
		Name             string `json:"name"`
		LiveKitURL       string `json:"livekitUrl"`
		LiveKitAPIKey    string `json:"livekitApiKey"`
		LiveKitAPISecret string `json:"livekitApiSecret"`
	}
	dec := json.NewDecoder(http.MaxBytesReader(w, r.Body, maxProjectBody))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&body); err != nil {
		writeError(w, r, http.StatusBadRequest, codeInvalidRequest, "the body must be one JSON object of name, livekitUrl, livekitApiKey and livekitApiSecret, at most 4 KiB")
		return
	}
	var trailing any
	if err := dec.Decode(&trailing); !errors.Is(err, io.EOF) {
		writeError(w, r, http.StatusBadRequest, codeInvalidRequest, "the body must be one JSON object at most 4 KiB")
		return
	}
	org := chi.URLParam(r, "orgId")
	in, err := CheckProject(store.NewProject{
		OrganisationID: org, Name: body.Name, LiveKitURL: body.LiveKitURL, LiveKitAPIKey: body.LiveKitAPIKey, LiveKitAPISecret: body.LiveKitAPISecret,
	})
	if err != nil {
		writeError(w, r, http.StatusBadRequest, codeInvalidRequest, err.Error())
		return
	}
	p, err := a.NewProjects.CreateProject(r.Context(), in)
	switch {
	case errors.Is(err, store.ErrProjectExists):
		writeError(w, r, http.StatusConflict, codeProjectExists, fmt.Sprintf("this organisation already has a project with the id %s; choose another name", in.ID))
		return
	case errors.Is(err, store.ErrLiveKitKeyInUse):
		// The project that holds the key may be another organisation's, so it is not named.
		writeError(w, r, http.StatusConflict, codeLiveKitKeyInUse, "this LiveKit API key is already used by a project on this server; each project needs its own key")
		return
	case err != nil:
		a.failWith(w, r, err, "organisation not found", "the project could not be stored")
		return
	}
	w.Header().Set("Cache-Control", "no-store")
	writeJSON(w, http.StatusCreated, workspaceProject{ID: p.ID, Name: p.Name})
}
