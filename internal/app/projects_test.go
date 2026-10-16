package app

import (
	"context"
	"strings"
	"testing"

	"github.com/mahimailabs/voicegateway/internal/config"
)

func TestSlugMatchesTheConsole(t *testing.T) {
	tests := map[string]string{
		"Default project": "default-project",
		"  Production  ":  "production",
		"EU / Staging #2": "eu-staging-2",
	}
	for in, want := range tests {
		if got := Slug(in); got != want {
			t.Errorf("Slug(%q) = %q, want %q", in, got, want)
		}
	}
}

func TestAddProjectNamesWhatIsMissing(t *testing.T) {
	_, err := AddProject(context.Background(), config.Config{}, ProjectInput{Name: "Production"})
	if err == nil {
		t.Fatal("AddProject() accepted a project without its LiveKit details")
	}
	for _, want := range []string{"--livekit-url", "--livekit-api-key", "LIVEKIT_API_SECRET"} {
		if !strings.Contains(err.Error(), want) {
			t.Errorf("error %q does not name %s", err, want)
		}
	}
}
