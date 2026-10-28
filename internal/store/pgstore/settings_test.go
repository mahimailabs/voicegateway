package pgstore

import (
	"context"
	"errors"
	"math"
	"reflect"
	"testing"

	"github.com/mahimailabs/voicegateway/internal/store"
)

func TestProjectSettingsPersistenceScopeAndConflicts(t *testing.T) {
	s, pool := migrated(t)
	ctx := context.Background()
	for _, id := range []string{"one", "two"} {
		if _, err := s.CreateProject(ctx, project(id, "key-"+id)); err != nil {
			t.Fatal(err)
		}
	}
	initial, err := s.ProjectSettings(ctx, "default", "one")
	if err != nil || initial.Revision != 0 || initial.Thresholds != (store.Thresholds{AnswerMs: 1500, JoinMs: 2000, TTSStartMs: 1000}) {
		t.Fatalf("default settings: %+v %v", initial, err)
	}
	next := store.Thresholds{AnswerMs: 2400, JoinMs: 3500, TTSStartMs: 1200}
	saved, err := s.UpdateProjectSettings(ctx, "default", "one", initial.Revision, next)
	if err != nil || saved.Revision != 1 || saved.Thresholds != next {
		t.Fatalf("save: %+v %v", saved, err)
	}
	reopened := NewStore(pool, s.sealer)
	got, err := reopened.ProjectSettings(ctx, "default", "one")
	if err != nil || !reflect.DeepEqual(got, saved) {
		t.Fatalf("persistence: %+v %v", got, err)
	}
	other, err := s.ProjectSettings(ctx, "default", "two")
	if err != nil || !reflect.DeepEqual(other, initial) {
		t.Fatalf("other project changed: %+v %v", other, err)
	}
	if _, err := s.ProjectSettings(ctx, "other-org", "one"); !errors.Is(err, store.ErrNotFound) {
		t.Fatalf("cross-org read: %v", err)
	}
	if _, err := s.UpdateProjectSettings(ctx, "other-org", "one", 0, next); !errors.Is(err, store.ErrNotFound) {
		t.Fatalf("cross-org write: %v", err)
	}
	if _, err := s.UpdateProjectSettings(ctx, "default", "one", 0, initial.Thresholds); !errors.Is(err, store.ErrSettingsConflict) {
		t.Fatalf("stale undo: %v", err)
	}
	for _, v := range []float64{0, 99.9, 60000.1, math.NaN(), math.Inf(1)} {
		bad := next
		bad.AnswerMs = v
		if _, err := s.UpdateProjectSettings(ctx, "default", "one", 1, bad); !errors.Is(err, store.ErrInvalidThresholds) {
			t.Fatalf("invalid threshold %v: %v", v, err)
		}
	}
	got, err = s.ProjectSettings(ctx, "default", "one")
	if err != nil || !reflect.DeepEqual(got, saved) {
		t.Fatalf("rejected save changed settings: %+v %v", got, err)
	}
	results := make(chan error, 2)
	for _, value := range []float64{4000, 5000} {
		go func(answer float64) {
			t := next
			t.AnswerMs = answer
			_, err := s.UpdateProjectSettings(ctx, "default", "one", saved.Revision, t)
			results <- err
		}(value)
	}
	successes, conflicts := 0, 0
	for range 2 {
		err := <-results
		if err == nil {
			successes++
		} else if errors.Is(err, store.ErrSettingsConflict) {
			conflicts++
		} else {
			t.Fatal(err)
		}
	}
	got, err = s.ProjectSettings(ctx, "default", "one")
	if err != nil || successes != 1 || conflicts != 1 || got.Revision != 2 {
		t.Fatalf("concurrent writes: success=%d conflicts=%d settings=%+v err=%v", successes, conflicts, got, err)
	}
}
