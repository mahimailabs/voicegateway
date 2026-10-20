package pgstore

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/mahimailabs/voicegateway/internal/store"
)

func TestMetadataListsAndRevisionConsistency(t *testing.T) {
	s, _ := migrated(t)
	ctx := context.Background()
	for _, p := range []string{"one", "two"} {
		if _, err := s.CreateProject(ctx, project(p, "key-"+p)); err != nil {
			t.Fatal(err)
		}
	}
	start := time.Now().UTC()
	create := func(p, sid string) string {
		t.Helper()
		id, err := s.UpsertCall(ctx, store.CallUpsert{OrganisationID: "default", ProjectID: p, RoomSID: sid, RoomName: "same", StartedAt: &start})
		if err != nil {
			t.Fatal(err)
		}
		return id
	}
	first, second := create("one", "RM_first"), create("one", "RM_second")
	other := create("two", "RM_other")
	if _, err := s.UpdateCallMetadata(ctx, "default", "one", first, 0, []string{"support"}, true); err != nil {
		t.Fatal(err)
	}
	revision, err := s.CallMetadataRevision(ctx, "default", "one")
	if err != nil || revision != 1 {
		t.Fatalf("revision %d %v", revision, err)
	}
	f := store.CallFilter{OrganisationID: "default", ProjectID: "one", SortBy: "startedAt", BudgetMs: 1500, MetadataRevision: &revision}
	for _, tc := range []struct {
		tags     []string
		archived *bool
		count    int64
	}{
		{nil, nil, 2},
		{[]string{"support"}, nil, 1},
		{[]string{"missing", "support"}, nil, 1},
		{[]string{"missing"}, nil, 0},
		{nil, new(true), 1},
		{nil, new(false), 1},
		{[]string{"support"}, new(false), 0},
	} {
		f.Tags, f.Archived = tc.tags, tc.archived
		rows, totals, _, err := s.ListCalls(ctx, f, nil, 100, true, 100)
		if err != nil || totals.CallCount != tc.count || int64(len(rows)) != tc.count {
			t.Fatalf("query %+v rows=%d totals=%+v err=%v", tc, len(rows), totals, err)
		}
		for _, row := range rows {
			if row.ID == other {
				t.Fatal("cross project row")
			}
			if row.ID == first && (!row.Metadata.Archived || row.Metadata.Revision != 1 || len(row.Metadata.Tags) != 1) {
				t.Fatalf("list metadata %+v", row.Metadata)
			}
		}
	}
	c, err := s.Call(ctx, "default", "one", first)
	if err != nil || !c.Metadata.Archived || c.Metadata.Revision != 1 {
		t.Fatalf("detail %+v %v", c.Metadata, err)
	}
	// A concurrent edit between the API revision read and the list transaction must not serve
	// rows with a different generation, even for an unfiltered page.
	if _, err := s.UpdateCallMetadata(ctx, "default", "one", second, 0, []string{"support"}, false); err != nil {
		t.Fatal(err)
	}
	f.Tags, f.Archived = nil, nil
	if _, _, _, err := s.ListCalls(ctx, f, nil, 100, true, 100); !errors.Is(err, store.ErrMetadataSnapshot) {
		t.Fatalf("stale generation %v", err)
	}
	revision, err = s.CallMetadataRevision(ctx, "default", "one")
	if err != nil || revision != 2 {
		t.Fatalf("new revision %d %v", revision, err)
	}
	f.Tags = []string{"support"}
	rows, totals, _, err := s.ListCalls(ctx, f, nil, 100, true, 100)
	if err != nil || len(rows) != 2 || totals.CallCount != 2 {
		t.Fatalf("fresh generation %d %+v %v", len(rows), totals, err)
	}
	otherRevision, err := s.CallMetadataRevision(ctx, "default", "two")
	if err != nil || otherRevision != 0 {
		t.Fatalf("other project generation %d %v", otherRevision, err)
	}
	// A rejected stale write does not invalidate other readers.
	if _, err := s.UpdateCallMetadata(ctx, "default", "one", second, 0, nil, true); !errors.Is(err, store.ErrMetadataConflict) {
		t.Fatal(err)
	}
	after, err := s.CallMetadataRevision(ctx, "default", "one")
	if err != nil || after != revision {
		t.Fatalf("rejected write changed generation %d %v", after, err)
	}
}
