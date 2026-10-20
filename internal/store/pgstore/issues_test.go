package pgstore

import (
	"context"
	"errors"
	"sync"
	"testing"
	"time"

	"github.com/mahimailabs/voicegateway/internal/store"
)

func TestIssueEvidenceDeduplicationRegressionAndMetadataIsolation(t *testing.T) {
	s, pool := migrated(t)
	ctx := context.Background()
	for _, id := range []string{"one", "two"} {
		if _, err := s.CreateProject(ctx, project(id, "key-"+id)); err != nil {
			t.Fatal(err)
		}
	}
	create := func(sid string, at time.Time) string {
		t.Helper()
		id, err := s.UpsertCall(ctx, store.CallUpsert{OrganisationID: "default", ProjectID: "one", RoomSID: sid, RoomName: "reused-room", StartedAt: &at})
		if err != nil {
			t.Fatal(err)
		}
		return id
	}
	now := time.Now().UTC()
	first, late := create("first", now), create("late", now.Add(-time.Hour))
	findings := []store.Finding{{ID: "slow", Title: "Slow answers", Severity: "medium", Detail: "One turn exceeded the response budget", Turns: []int{0}}}
	if err := s.ApplyCallFindings(ctx, "default", "one", first, findings); err != nil {
		t.Fatal(err)
	}
	resolved := "resolved"
	m, err := s.UpdateIssueMetadata(ctx, "default", "one", "slow", 0, 1, &resolved, []string{" Support "}, false)
	if err != nil || m.State != "resolved" || m.Revision != 1 || m.Regressed || m.Tags[0] != "support" {
		t.Fatalf("resolve: %+v %v", m, err)
	}
	if err := s.ApplyCallFindings(ctx, "default", "one", first, findings); err != nil {
		t.Fatal(err)
	}
	m, err = NewStore(pool, s.sealer).IssueMetadata(ctx, "default", "one", "slow")
	if err != nil || m.EvidenceRevision != 1 || m.Regressed || m.State != "resolved" {
		t.Fatalf("replay/reopen: %+v %v", m, err)
	}
	// A newly observed occurrence may have an older source clock. It still regresses the issue.
	if err := s.ApplyCallFindings(ctx, "default", "one", late, findings); err != nil {
		t.Fatal(err)
	}
	m, err = s.IssueMetadata(ctx, "default", "one", "slow")
	if err != nil || m.EvidenceRevision != 2 || !m.Regressed || m.State != "open" {
		t.Fatalf("late evidence: %+v %v", m, err)
	}
	if _, err := s.UpdateIssueMetadata(ctx, "default", "one", "slow", 1, 1, &resolved, nil, false); !errors.Is(err, store.ErrIssueConflict) {
		t.Fatalf("stale evidence accepted: %v", err)
	}
	m, err = s.UpdateIssueMetadata(ctx, "default", "one", "slow", 1, 2, nil, []string{"follow-up"}, true)
	if err != nil || !m.Archived || !m.Regressed || m.ReviewedRevision != 1 || m.Revision != 2 {
		t.Fatalf("archive acknowledged evidence: %+v %v", m, err)
	}
	if _, err := s.IssueMetadata(ctx, "default", "two", "slow"); !errors.Is(err, store.ErrNotFound) {
		t.Fatalf("wrong-project read: %v", err)
	}
	if err := s.ApplyCallFindings(ctx, "default", "two", first, findings); !errors.Is(err, store.ErrNotFound) {
		t.Fatalf("wrong-project evidence write: %v", err)
	}
	if _, err := s.UpdateIssueMetadata(ctx, "default", "two", "slow", 2, 2, &resolved, nil, false); !errors.Is(err, store.ErrNotFound) {
		t.Fatalf("wrong-project metadata write: %v", err)
	}
	var wg sync.WaitGroup
	outcomes := make(chan error, 2)
	for range 2 {
		wg.Go(func() {
			_, err := s.UpdateIssueMetadata(ctx, "default", "one", "slow", 2, 2, &resolved, []string{"follow-up"}, false)
			outcomes <- err
		})
	}
	wg.Wait()
	close(outcomes)
	won, conflicted := 0, 0
	for err := range outcomes {
		switch {
		case err == nil:
			won++
		case errors.Is(err, store.ErrIssueConflict):
			conflicted++
		default:
			t.Fatal(err)
		}
	}
	if won != 1 || conflicted != 1 {
		t.Fatalf("concurrent decisions: won=%d conflicts=%d", won, conflicted)
	}
	if err := s.ApplyCallFindings(ctx, "default", "one", first, nil); err != nil {
		t.Fatal(err)
	}
	var active int
	if err := pool.QueryRow(ctx, "SELECT count(*) FROM issue_occurrences WHERE active").Scan(&active); err != nil || active != 2 {
		t.Fatalf("unknown source erased findings: %d %v", active, err)
	}
	if err := s.ApplyCallFindings(ctx, "default", "one", first, []store.Finding{}); err != nil {
		t.Fatal(err)
	}
	if err := pool.QueryRow(ctx, "SELECT count(*) FROM issue_occurrences WHERE active").Scan(&active); err != nil || active != 1 {
		t.Fatalf("evaluated empty result did not clear only its own occurrence: %d %v", active, err)
	}
}

func TestIssueFindingBatchRollsBackOnInvalidEvidence(t *testing.T) {
	s, pool := migrated(t)
	ctx := context.Background()
	if _, err := s.CreateProject(ctx, project("one", "key-one")); err != nil {
		t.Fatal(err)
	}
	id, err := s.UpsertCall(ctx, store.CallUpsert{OrganisationID: "default", ProjectID: "one", RoomSID: "first", RoomName: "room"})
	if err != nil {
		t.Fatal(err)
	}
	err = s.ApplyCallFindings(ctx, "default", "one", id, []store.Finding{
		{ID: "first", Title: "Valid", Severity: "medium"}, {ID: "second", Title: "Invalid", Severity: "invented"},
	})
	if err == nil {
		t.Fatal("invalid severity accepted")
	}
	var count int
	if err := pool.QueryRow(ctx, "SELECT count(*) FROM issues").Scan(&count); err != nil || count != 0 {
		t.Fatalf("partial finding batch persisted: %d %v", count, err)
	}
}

func TestIssuePartialCoveragePreservesUnreportedEvidence(t *testing.T) {
	s, pool := migrated(t)
	ctx := context.Background()
	if _, err := s.CreateProject(ctx, project("coverage", "key-coverage")); err != nil {
		t.Fatal(err)
	}
	now := time.Now().UTC()
	id, err := s.UpsertCall(ctx, store.CallUpsert{OrganisationID: "default", ProjectID: "coverage", RoomSID: "coverage", StartedAt: &now})
	if err != nil {
		t.Fatal(err)
	}
	findings := []store.Finding{{ID: "slow", Title: "Slow answers", Severity: "medium"}, {ID: "tts", Title: "Slow speech", Severity: "medium"}}
	if err := s.ApplyCallFindings(ctx, "default", "coverage", id, findings); err != nil {
		t.Fatal(err)
	}
	for _, tc := range []struct {
		covered []string
		want    int
	}{{[]string{}, 2}, {[]string{"slow"}, 1}, {[]string{"tts"}, 0}} {
		tx, err := pool.Begin(ctx)
		if err != nil {
			t.Fatal(err)
		}
		err = applyCallFindings(ctx, s.q.WithTx(tx), "default", "coverage", id, []store.Finding{}, tc.covered)
		if err != nil {
			_ = tx.Rollback(ctx)
			t.Fatal(err)
		}
		if err := tx.Commit(ctx); err != nil {
			t.Fatal(err)
		}
		var count int
		if err := pool.QueryRow(ctx, "SELECT count(*) FROM issue_occurrences WHERE call_id=$1::uuid AND active", id).Scan(&count); err != nil {
			t.Fatal(err)
		}
		if count != tc.want {
			t.Fatalf("coverage %v: %d active, want %d", tc.covered, count, tc.want)
		}
	}
}
