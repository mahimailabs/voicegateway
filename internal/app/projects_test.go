package app

import (
	"context"
	"testing"

	"github.com/mahimailabs/voicegateway/internal/config"
)

func TestAddProjectNamesWhatIsMissing(t *testing.T) {
	_, err := AddProject(context.Background(), config.Config{}, ProjectInput{Name: "Production"})
	if err == nil {
		t.Fatal("AddProject() accepted a project without its LiveKit details")
	}
	if want := "missing --livekit-url, --livekit-api-key, LIVEKIT_API_SECRET"; err.Error() != want {
		t.Errorf("error = %q, want %q in that order", err, want)
	}
}

// The command line refuses what createProject refuses, under its own flag names (phase 06f).
func TestAddProjectChecksLikeTheAPI(t *testing.T) {
	for _, tt := range []struct {
		in   ProjectInput
		want string
	}{
		{ProjectInput{Name: "Production", LiveKitURL: "ftp://lk.example.com", LiveKitAPIKey: "APIkey", LiveKitAPISecret: "s"}, "--livekit-url must be a ws, wss, http or https URL with a host"},
		{ProjectInput{Name: "!!!", LiveKitURL: "wss://lk.example.com", LiveKitAPIKey: "APIkey", LiveKitAPISecret: "s"}, "--name must be 1 to 64 characters, with no control characters and at least one letter or digit"},
		{ProjectInput{Name: "Production", ID: "Not An Id", LiveKitURL: "wss://lk.example.com", LiveKitAPIKey: "APIkey", LiveKitAPISecret: "s"}, "--id must be 1 to 63 characters of a to z, 0 to 9 and -, starting with a letter or digit"},
	} {
		if _, err := AddProject(context.Background(), config.Config{}, tt.in); err == nil || err.Error() != tt.want {
			t.Errorf("AddProject(%+v) = %v, want %q", tt.in, err, tt.want)
		}
	}
}
