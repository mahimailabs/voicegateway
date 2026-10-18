package scrape

import (
	"encoding/json"
	"strings"
	"testing"
	"time"

	"github.com/livekit/protocol/livekit"
	"google.golang.org/protobuf/types/known/timestamppb"
)

func TestIsLiveKitCloud(t *testing.T) {
	for url, want := range map[string]bool{
		"wss://my-project-abc123.livekit.cloud": true,
		"https://My-Project.LIVEKIT.cloud:443":  true,
		"http://localhost:7880":                 false,
		"wss://livekit.example.com":             false,
		"wss://livekit.cloud.example.com":       false,
		"wss://evil.example.com/.livekit.cloud": false,
		"::not a url":                           false,
	} {
		if got := IsLiveKitCloud(url); got != want {
			t.Errorf("IsLiveKitCloud(%q) = %t, want %t", url, got, want)
		}
	}
}

// What lk shows as placeholders, and proto3 defaults, stay null; secrets, attributes and owners are
// never part of the stored projection.
func TestCloudAgentProjection(t *testing.T) {
	deployed := time.Date(2026, 10, 12, 22, 48, 24, 160959000, time.UTC)
	agent := &livekit.AgentInfo{
		AgentId: "CA_1", Version: "---", DeployedAt: timestamppb.New(time.Time{}),
		Secrets: []*livekit.AgentSecret{{Name: "OPENAI_API_KEY", Value: []byte("sk-agent-secret")}},
		AgentDeployments: []*livekit.AgentDeployment{{
			Region: "us-east", ServerRegion: "osbxash1b", Status: "Sleeping", MinReplicas: 1, MaxReplicas: 2,
			CpuReq: "1", Version: "v1", Events: []*livekit.AgentEvent{{Type: livekit.AgentEventType_AGENT_EVENT_TYPE_APPLICATION_CRASHED}},
		}},
	}
	versions := []*livekit.AgentVersion{
		{
			Version: "v1", Current: true, Active: true, Status: "Available", CreatedAt: timestamppb.New(deployed),
			Attributes: map[string]string{"build": "attr-secret"}, Owner: "owner@example.com",
		},
		{Version: "v0", Status: "Available"},
	}
	got := projectCloudAgent(agent, versions)
	if got.Name != nil || got.Version != nil || got.DeployedAt != nil {
		t.Errorf("name %v, version %v, deployedAt %v; want null for unset, --- and zero time", got.Name, got.Version, got.DeployedAt)
	}
	d := got.Deployments[0]
	if d.Replicas != nil || d.Deployment != nil || d.CurCPU != nil || d.LastScrapedAt != nil {
		t.Errorf("deployment %+v: unreported values must be null", d)
	}
	if d.MinReplicas == nil || *d.MinReplicas != 1 || d.CPUReq == nil || *d.CPUReq != "1" {
		t.Errorf("deployment %+v: reported values lost", d)
	}
	if v := got.Versions[0]; v.CreatedAt == nil || !v.CreatedAt.Equal(deployed) || v.DeployedAt != nil || !*v.Current {
		t.Errorf("version %+v", v)
	}
	if v := got.Versions[1]; v.CreatedAt != nil || *v.Current {
		t.Errorf("version without times: %+v", v)
	}
	body, err := json.Marshal(got)
	if err != nil {
		t.Fatal(err)
	}
	for _, secret := range []string{"sk-agent-secret", "OPENAI_API_KEY", "attr-secret", "owner@example.com", "APPLICATION_CRASHED"} {
		if strings.Contains(string(body), secret) {
			t.Errorf("stored projection contains %q", secret)
		}
	}
}
