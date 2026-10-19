package app

import (
	"context"
	"net"
	"strings"
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

// voicegateway project add proves the secret with the same LiveKit read as createProject, before it
// opens the store; --skip-verify leaves the read out. A closed loopback port stands in for a LiveKit
// server that is not reachable yet.
func TestAddProjectVerifiesUnlessSkipped(t *testing.T) {
	l, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	closed := "http://" + l.Addr().String()
	_ = l.Close()
	in := ProjectInput{Name: "Production", LiveKitURL: closed, LiveKitAPIKey: "APIkey", LiveKitAPISecret: "made-up-for-the-test"}
	for _, tt := range []struct {
		name string
		skip bool
		want string
	}{
		{"verified", false, "could not reach this LiveKit URL"},
		{"skipped", true, "seal"},
	} {
		t.Run(tt.name, func(t *testing.T) {
			in.SkipVerify = tt.skip
			_, err := AddProject(context.Background(), config.Config{}, in)
			if err == nil || !strings.Contains(err.Error(), tt.want) {
				t.Fatalf("AddProject = %v, want an error naming %q", err, tt.want)
			}
			if !tt.skip && !strings.Contains(err.Error(), "--skip-verify") {
				t.Errorf("the error %q does not name --skip-verify", err)
			}
			if strings.Contains(err.Error(), in.LiveKitAPISecret) {
				t.Error("the error holds the secret")
			}
		})
	}
}
