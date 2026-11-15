package chstore

import (
	"context"
	"testing"
	"time"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// Trimming to a plan's retention deletes one organisation's call data older than the cutoff, at
// once for reads, and keeps its newer rows and every other organisation's (decision 0042).
func TestTrimTelemetryIsPerOrganisation(t *testing.T) {
	s := openMigrated(t)
	ctx := context.Background()
	old, current, other := export("old", nil), export("now", nil), export("other", nil)
	old.ReceivedAt = time.Now().Add(-10 * 24 * time.Hour)
	other.OrganisationID = "other"
	other.ReceivedAt = old.ReceivedAt
	if err := s.Insert(ctx, []store.Event{old, current, other}); err != nil {
		t.Fatal(err)
	}
	for range 2 { // safe to repeat
		if err := s.TrimTelemetry(ctx, "default", time.Now().Add(-7*24*time.Hour)); err != nil {
			t.Fatal(err)
		}
	}
	got := storedEvents(ctx, t, s)
	if len(got) != 2 || got[0] != current.DedupeKey || got[1] != other.DedupeKey {
		t.Fatalf("events %v, want [%s %s]", got, current.DedupeKey, other.DedupeKey)
	}
}
