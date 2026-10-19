package api

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/mahimailabs/voicegateway/internal/store"
)

func TestSlugMatchesTheConsole(t *testing.T) {
	tests := map[string]string{
		"Default project":              "default-project",
		"  Production  ":               "production",
		"EU / Staging #2":              "eu-staging-2",
		strings.Repeat("a", 70):        strings.Repeat("a", 63),
		strings.Repeat("a", 62) + " b": strings.Repeat("a", 62),
	}
	for in, want := range tests {
		if got := Slug(in); got != want {
			t.Errorf("Slug(%q) = %q, want %q", in, got, want)
		}
	}
}

// CheckProject is the one rule createProject and voicegateway project add share (phase 06f).
func TestCheckProject(t *testing.T) {
	ok := store.NewProject{OrganisationID: "org-a", Name: "  Production  ", LiveKitURL: " wss://lk.example.com ", LiveKitAPIKey: " APIkey ", LiveKitAPISecret: " kept as given "}
	got, err := CheckProject(ok)
	if err != nil {
		t.Fatal(err)
	}
	if want := (store.NewProject{OrganisationID: "org-a", ID: "production", Name: "Production", LiveKitURL: "wss://lk.example.com", LiveKitAPIKey: "APIkey", LiveKitAPISecret: " kept as given "}); got != want {
		t.Errorf("CheckProject = %+v, want %+v", got, want)
	}
	for _, scheme := range []string{"ws", "wss", "http", "https"} {
		p := ok
		p.LiveKitURL = scheme + "://lk.example.com:7880"
		if _, err := CheckProject(p); err != nil {
			t.Errorf("%s URL refused: %v", scheme, err)
		}
	}
	with := func(change func(*store.NewProject)) store.NewProject {
		p := ok
		change(&p)
		return p
	}
	for _, tt := range []struct {
		name string
		in   store.NewProject
		want string
	}{
		{"every field missing", store.NewProject{LiveKitAPISecret: "   "}, "missing name, livekitUrl, livekitApiKey, livekitApiSecret"},
		{"no letter or digit", with(func(p *store.NewProject) { p.Name = "!!!" }), "name must be"},
		{"name too long", with(func(p *store.NewProject) { p.Name = strings.Repeat("x", 65) }), "name must be"},
		{"control character", with(func(p *store.NewProject) { p.Name = "a\x00b" }), "name must be"},
		{"bad id", with(func(p *store.NewProject) { p.ID = "-x" }), "id must be"},
		{"other scheme", with(func(p *store.NewProject) { p.LiveKitURL = "ftp://lk.example.com" }), "livekitUrl must be"},
		{"no host", with(func(p *store.NewProject) { p.LiveKitURL = "wss://" }), "livekitUrl must be"},
		{"not a URL", with(func(p *store.NewProject) { p.LiveKitURL = "lk.example.com" }), "livekitUrl must be"},
		{"user information", with(func(p *store.NewProject) { p.LiveKitURL = "wss://u:p@lk.example.com" }), "livekitUrl must be"},
		{"URL too long", with(func(p *store.NewProject) { p.LiveKitURL = "wss://lk.example.com/" + strings.Repeat("x", 2048) }), "livekitUrl must be"},
		{"space in key", with(func(p *store.NewProject) { p.LiveKitAPIKey = "API key" }), "livekitApiKey must be"},
		{"key too long", with(func(p *store.NewProject) { p.LiveKitAPIKey = strings.Repeat("k", 257) }), "livekitApiKey must be"},
	} {
		t.Run(tt.name, func(t *testing.T) {
			_, err := CheckProject(tt.in)
			var invalid *ProjectInputError
			if !errors.As(err, &invalid) || !strings.HasPrefix(err.Error(), tt.want) {
				t.Errorf("CheckProject = %v, want a ProjectInputError starting %q", err, tt.want)
			}
		})
	}
}

// The body limit applies to the whole request, including bytes after a valid object.
func TestCreateProjectRejectsTrailingBody(t *testing.T) {
	body := `{"name":"Production","livekitUrl":"wss://lk.example.com","livekitApiKey":"APIkey","livekitApiSecret":"test-secret"}`
	for name, suffix := range map[string]string{"second object": "{}", "garbage": "x", "oversize whitespace": strings.Repeat(" ", maxProjectBody)} {
		t.Run(name, func(t *testing.T) {
			creator := &countProjectCreates{}
			a := &API{NewProjects: creator}
			w := httptest.NewRecorder()
			a.createProject(w, httptest.NewRequest(http.MethodPost, "/", strings.NewReader(body+suffix)))
			if w.Code != http.StatusBadRequest || creator.calls != 0 {
				t.Fatalf("status=%d creates=%d, want 400 and no write", w.Code, creator.calls)
			}
		})
	}
}

type countProjectCreates struct{ calls int }

func (c *countProjectCreates) CreateProject(_ context.Context, p store.NewProject) (store.Project, error) {
	c.calls++
	return store.Project{ID: p.ID, Name: p.Name}, nil
}

func TestCreateProjectDisabled(t *testing.T) {
	w := httptest.NewRecorder()
	(&API{}).createProject(w, httptest.NewRequest(http.MethodPost, "/", strings.NewReader(`{"name":"Production","livekitUrl":"http://127.0.0.1","livekitApiKey":"key","livekitApiSecret":"secret"}`)))
	if w.Code != http.StatusForbidden {
		t.Fatalf("status=%d, want 403", w.Code)
	}
}
