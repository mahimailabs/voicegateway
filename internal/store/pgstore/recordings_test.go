package pgstore

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// Phase 07a, acceptance check 3: a recording links to its call once however often it is
// delivered, the call read carries the latest, listCalls filters and counts recorded calls, an id
// from another project is not found, and expired links are forgotten.
func TestRecordingLinks(t *testing.T) {
	s, _ := migrated(t)
	ctx := context.Background()
	for _, p := range []NewProject{project("production", "APIprod"), project("staging", "APIstage")} {
		if _, err := s.CreateProject(ctx, p); err != nil {
			t.Fatal(err)
		}
	}
	p, err := s.Project(ctx, "default", "production")
	if err != nil || p.Recordings {
		t.Fatalf("new project recordings = %v, %v; want off", p.Recordings, err)
	}
	if err := s.SetProjectRecordings(ctx, "default", "production", true); err != nil {
		t.Fatal(err)
	}
	if p, err := s.Project(ctx, "default", "production"); err != nil || !p.Recordings {
		t.Fatalf("recordings after on = %v, %v", p.Recordings, err)
	}
	if err := s.SetProjectRecordings(ctx, "default", "missing", true); !errors.Is(err, store.ErrNotFound) {
		t.Fatalf("missing project: %v", err)
	}

	start := time.Date(2026, 10, 19, 12, 0, 0, 0, time.UTC)
	call := func(sid string) string {
		t.Helper()
		id, err := s.UpsertCall(ctx, store.CallUpsert{OrganisationID: "default", ProjectID: "production", RoomSID: sid, RoomName: "room-" + sid, StartedAt: &start})
		if err != nil {
			t.Fatal(err)
		}
		return id
	}
	recorded, plain := call("RM_rec"), call("RM_plain")

	recStart := start.Add(2 * time.Second)
	first := store.RecordingLink{CallID: recorded, ObjectKey: "recordings/2026/10/19/default/production/AJ_1.ogg", SizeBytes: 10, StartedAt: &recStart, StoredAt: start}
	latest := store.RecordingLink{CallID: recorded, ObjectKey: "recordings/2026/10/19/default/production/AJ_2.ogg", SizeBytes: 20, StoredAt: start.Add(time.Minute)}
	for _, l := range []store.RecordingLink{first, first, latest} {
		if err := s.LinkRecording(ctx, l); err != nil {
			t.Fatal(err)
		}
	}

	c, err := s.Call(ctx, "default", "production", recorded)
	if err != nil || c.Recording == nil || c.Recording.StartedAt != nil {
		t.Fatalf("call recording = %+v, %v; want the latest, whose start is unknown", c.Recording, err)
	}
	r, err := s.Recording(ctx, "default", "production", c.Recording.ID)
	if err != nil || r.ObjectKey != latest.ObjectKey || r.SizeBytes != 20 {
		t.Fatalf("recording = %+v, %v", r, err)
	}
	if _, err := s.Recording(ctx, "default", "staging", c.Recording.ID); !errors.Is(err, store.ErrNotFound) {
		t.Fatalf("recording read through another project: %v", err)
	}
	if c, err := s.Call(ctx, "default", "production", plain); err != nil || c.Recording != nil {
		t.Fatalf("call without recording = %+v, %v", c.Recording, err)
	}

	yes, no := true, false
	for _, tc := range []struct {
		filter       *bool
		want         []string
		wantRecorded int64
	}{{&yes, []string{recorded}, 1}, {&no, []string{plain}, 0}, {nil, []string{recorded, plain}, 1}} {
		rows, totals, _, err := s.ListCalls(ctx, store.CallFilter{OrganisationID: "default", ProjectID: "production", SortBy: "startedAt", Recorded: tc.filter}, nil, 10, true, 10)
		if err != nil {
			t.Fatal(err)
		}
		got := map[string]bool{}
		for _, row := range rows {
			got[row.ID] = row.Recorded
		}
		if len(rows) != len(tc.want) || totals.Recorded != tc.wantRecorded {
			t.Fatalf("recorded=%v: rows %v, totals.Recorded %d", tc.filter, got, totals.Recorded)
		}
		for _, id := range tc.want {
			if recordedRow, ok := got[id]; !ok || recordedRow != (id == recorded) {
				t.Fatalf("recorded=%v: rows %v, want %v", tc.filter, got, tc.want)
			}
		}
	}

	n, err := s.ForgetRecordings(ctx, start.Add(30*time.Second))
	if err != nil || n != 1 {
		t.Fatalf("forgot %d, %v; want the first link only", n, err)
	}
	if _, err := s.Recording(ctx, "default", "production", c.Recording.ID); err != nil {
		t.Fatalf("latest recording forgotten too early: %v", err)
	}
}
