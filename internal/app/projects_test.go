package app

import (
	"context"
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
	if want := "missing --livekit-url, --livekit-api-key, LIVEKIT_API_SECRET"; err.Error() != want {
		t.Errorf("error = %q, want %q in that order", err, want)
	}
}
