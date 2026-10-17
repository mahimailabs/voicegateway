package scrape

import (
	"encoding/json"
	"strings"
	"testing"
	"time"

	"github.com/livekit/protocol/livekit"
)

func TestUnreportedStaysNull(t *testing.T) {
	joined := time.Date(2026, 10, 17, 12, 0, 0, 0, time.UTC)
	tests := []struct {
		name       string
		in         *livekit.ParticipantInfo
		wantJoined *time.Time
		wantRegion *string
	}{
		{
			name: "neither reported",
			in:   &livekit.ParticipantInfo{Sid: "PA_1", Kind: livekit.ParticipantInfo_STANDARD, State: livekit.ParticipantInfo_ACTIVE},
		},
		{
			name:       "seconds only",
			in:         &livekit.ParticipantInfo{Sid: "PA_2", JoinedAt: joined.Unix(), Region: "us-east"},
			wantJoined: &joined,
			wantRegion: ptr("us-east"),
		},
		{
			name:       "milliseconds preferred",
			in:         &livekit.ParticipantInfo{Sid: "PA_3", JoinedAt: joined.Unix() - 5, JoinedAtMs: joined.UnixMilli()},
			wantJoined: &joined,
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			p, _, _ := projectParticipant(tt.in, nil)
			if !equalTime(p.JoinedAt, tt.wantJoined) {
				t.Errorf("joinedAt = %v, want %v", p.JoinedAt, tt.wantJoined)
			}
			if (p.Region == nil) != (tt.wantRegion == nil) || (p.Region != nil && *p.Region != *tt.wantRegion) {
				t.Errorf("region = %v, want %v", p.Region, tt.wantRegion)
			}
			body, err := json.Marshal(p)
			if err != nil {
				t.Fatal(err)
			}
			if tt.wantJoined == nil && !strings.Contains(string(body), `"joinedAt":null`) {
				t.Errorf("unreported joinedAt is not null: %s", body)
			}
			if tt.wantRegion == nil && !strings.Contains(string(body), `"region":null`) {
				t.Errorf("unreported region is not null: %s", body)
			}
		})
	}
}

func equalTime(a, b *time.Time) bool {
	if a == nil || b == nil {
		return a == b
	}
	return a.Equal(*b)
}

func TestRoomNameMasksPhoneNumbers(t *testing.T) {
	tests := []struct{ in, want string }{
		{"call-_+15551234567_aB3x", "call-_•••• 4567_aB3x"},
		{"playground-86a0b802", "playground-86a0b802"},
		{"room-123456", "room-123456"},
	}
	for _, tt := range tests {
		if got := projectRoom(&livekit.Room{Name: tt.in}, nil).Name; got != tt.want {
			t.Errorf("name %q stored as %q, want %q", tt.in, got, tt.want)
		}
	}
}
