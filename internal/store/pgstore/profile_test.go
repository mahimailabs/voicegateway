package pgstore

import (
	"context"
	"errors"
	"strings"
	"testing"

	"github.com/mahimailabs/voicegateway/internal/store"
)

func TestAccountProfilePersistenceIsolationAndConflicts(t *testing.T) {
	s, pool := migrated(t)
	ctx := context.Background()
	first, err := s.AddUser(ctx, "first@example.com", "unused-test-hash", "default", "viewer")
	if err != nil {
		t.Fatal(err)
	}
	second, err := s.AddUser(ctx, "second@example.com", "unused-test-hash", "default", "admin")
	if err != nil {
		t.Fatal(err)
	}
	saved, err := s.UpdateAccountProfile(ctx, first, 0, "  José  ")
	if err != nil || saved.Name == nil || *saved.Name != "José" || saved.Revision != 1 {
		t.Fatalf("save: %+v %v", saved, err)
	}
	reopened := NewStore(pool, s.sealer)
	got, err := reopened.AccountProfile(ctx, first)
	if err != nil || got.Name == nil || *got.Name != "José" || got.Revision != 1 {
		t.Fatalf("persistence: %+v %v", got, err)
	}
	other, err := s.AccountProfile(ctx, second)
	if err != nil || other.Name != nil || other.Revision != 0 {
		t.Fatalf("other account changed: %+v %v", other, err)
	}
	for _, bad := range []string{strings.Repeat("界", 101), "bad\nname", "bad\x00name", string([]byte{0xff})} {
		if _, err := s.UpdateAccountProfile(ctx, first, 1, bad); !errors.Is(err, store.ErrInvalidProfile) {
			t.Fatalf("invalid name accepted: %v", err)
		}
	}
	if _, err := s.UpdateAccountProfile(ctx, first, 0, "stale"); !errors.Is(err, store.ErrProfileConflict) {
		t.Fatalf("stale write: %v", err)
	}
	results := make(chan error, 2)
	for _, name := range []string{"First update", "Second update"} {
		go func() { _, err := s.UpdateAccountProfile(ctx, first, 1, name); results <- err }()
	}
	success, conflict := 0, 0
	for range 2 {
		err := <-results
		if err == nil {
			success++
		} else if errors.Is(err, store.ErrProfileConflict) {
			conflict++
		} else {
			t.Fatal(err)
		}
	}
	if success != 1 || conflict != 1 {
		t.Fatalf("concurrent writes: %d successes, %d conflicts", success, conflict)
	}
	cleared, err := s.UpdateAccountProfile(ctx, first, 2, "   ")
	if err != nil || cleared.Name != nil || cleared.Revision != 3 {
		t.Fatalf("clear: %+v %v", cleared, err)
	}
}
