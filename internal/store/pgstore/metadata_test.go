package pgstore

import (
	"context"
	"errors"
	"reflect"
	"strings"
	"testing"

	"github.com/mahimailabs/voicegateway/internal/store"
)

func TestCallMetadataPersistenceIdentityAndConflicts(t *testing.T) {
	s, pool := migrated(t)
	ctx := context.Background()
	for _, p := range []string{"one", "two"} {
		if _, err := s.CreateProject(ctx, project(p, "key-"+p)); err != nil {
			t.Fatal(err)
		}
	}
	create := func(projectID, sid string) string {
		t.Helper()
		id, err := s.UpsertCall(ctx, store.CallUpsert{OrganisationID: "default", ProjectID: projectID, RoomSID: sid, RoomName: "same-room"})
		if err != nil {
			t.Fatal(err)
		}
		return id
	}
	first, second, other := create("one", "RM_first"), create("one", "RM_second"), create("two", "RM_other")
	saved, err := s.UpdateCallMetadata(ctx, "default", "one", first, 0, []string{" Demo ", "demo", "Regression"}, true)
	if err != nil || !saved.Archived || saved.Revision != 1 || !reflect.DeepEqual(saved.Tags, []string{"demo", "regression"}) {
		t.Fatalf("save: %+v %v", saved, err)
	}
	if again := create("one", "RM_first"); again != first {
		t.Fatalf("worker retry changed call identity: %s", again)
	}
	for _, revision := range []int64{-1, 9007199254740991} {
		if _, err := s.UpdateCallMetadata(ctx, "default", "one", first, revision, nil, false); !errors.Is(err, store.ErrInvalidMetadata) {
			t.Fatalf("invalid revision: %v", err)
		}
	}
	reopened := NewStore(pool, s.sealer)
	got, err := reopened.CallMetadata(ctx, "default", "one", first)
	if err != nil || !reflect.DeepEqual(got, saved) {
		t.Fatalf("reopen: %+v %v", got, err)
	}
	for _, pair := range [][2]string{{"one", second}, {"two", other}} {
		got, err := s.CallMetadata(ctx, "default", pair[0], pair[1])
		if err != nil || got.Archived || len(got.Tags) != 0 || got.Revision != 0 {
			t.Fatalf("same room contaminated: %+v %v", got, err)
		}
	}
	for _, scope := range [][2]string{{"default", "two"}, {"other", "one"}} {
		if _, err := s.UpdateCallMetadata(ctx, scope[0], scope[1], first, 1, nil, false); !errors.Is(err, store.ErrNotFound) {
			t.Fatalf("wrong scope: %v", err)
		}
	}
	for _, tags := range [][]string{{""}, {"bad\n"}, {strings.Repeat("界", 65)}, make([]string, 33)} {
		if _, err := s.UpdateCallMetadata(ctx, "default", "one", first, 1, tags, false); !errors.Is(err, store.ErrInvalidMetadata) {
			t.Fatalf("invalid tags: %v", err)
		}
	}
	results := make(chan error, 2)
	for _, tag := range []string{"a", "b"} {
		go func() {
			_, err := s.UpdateCallMetadata(ctx, "default", "one", first, 1, []string{tag}, false)
			results <- err
		}()
	}
	successes, conflicts := 0, 0
	for range 2 {
		err := <-results
		if err == nil {
			successes++
		} else if errors.Is(err, store.ErrMetadataConflict) {
			conflicts++
		} else {
			t.Fatal(err)
		}
	}
	if successes != 1 || conflicts != 1 {
		t.Fatalf("CAS: %d successes, %d conflicts", successes, conflicts)
	}
	if _, err := s.UpdateCallMetadata(ctx, "default", "one", first, 1, saved.Tags, true); !errors.Is(err, store.ErrMetadataConflict) {
		t.Fatalf("stale Undo: %v", err)
	}
	cleared, err := s.UpdateCallMetadata(ctx, "default", "one", first, 2, nil, false)
	if err != nil || cleared.Archived || len(cleared.Tags) != 0 || cleared.Revision != 3 {
		t.Fatalf("clear/unarchive: %+v %v", cleared, err)
	}
}
