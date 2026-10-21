package pgstore

import (
	"context"
	"errors"
	"fmt"
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

func TestIssueListFiltersPersistedEvidenceAndMetadata(t *testing.T) {
	s, _ := migrated(t)
	ctx := context.Background()
	for _, p := range []string{"one", "two"} {
		if _, err := s.CreateProject(ctx, project(p, "key-"+p)); err != nil {
			t.Fatal(err)
		}
	}
	now := time.Now().UTC().Truncate(time.Second)
	for n, p := range []string{"one", "one", "two"} {
		at := now.Add(time.Duration(n) * time.Minute)
		id, err := s.UpsertCall(ctx, store.CallUpsert{OrganisationID: "default", ProjectID: p, RoomSID: fmt.Sprintf("room-%d", n), RoomName: fmt.Sprintf("room-%d", n), StartedAt: &at})
		if err != nil {
			t.Fatal(err)
		}
		if err := s.ApplyCallFindings(ctx, "default", p, id, []store.Finding{{ID: "slow", Title: "Slow answers", Severity: "medium", Detail: "response exceeded budget"}}); err != nil {
			t.Fatal(err)
		}
	}
	base := store.IssueFilter{From: now, To: now.Add(2 * time.Minute)}
	got, err := s.ListIssues(ctx, "default", "one", base)
	if err != nil || len(got.Items) != 1 || len(got.Items[0].Hits) != 2 || got.Complete {
		t.Fatalf("scoped list: %+v %v", got, err)
	}
	if got.Items[0].Metadata.EvidenceRevision != 2 || got.Items[0].Hits[0].Where != "room-1" {
		t.Fatalf("metadata or ordering: %+v", got)
	}
	state := "resolved"
	if _, err := s.UpdateIssueMetadata(ctx, "default", "one", "slow", 0, 2, &state, []string{"support"}, true); err != nil {
		t.Fatal(err)
	}
	archived := true
	filter := base
	filter.Archived = &archived
	filter.Tags = []string{"support"}
	filter.Search = "ROOM-0"
	got, err = s.ListIssues(ctx, "default", "one", filter)
	if err != nil || len(got.Items) != 1 || len(got.Items[0].Hits) != 1 || got.Items[0].Metadata.State != "resolved" {
		t.Fatalf("filtered list: %+v %v", got, err)
	}
	filter.Search = "%"
	got, err = s.ListIssues(ctx, "default", "one", filter)
	if err != nil || len(got.Items) != 0 {
		t.Fatalf("search interpreted wildcard: %+v %v", got, err)
	}
	filter = base
	filter.To = now.Add(time.Minute)
	got, err = s.ListIssues(ctx, "default", "one", filter)
	if err != nil || len(got.Items) != 1 || len(got.Items[0].Hits) != 1 {
		t.Fatalf("exclusive upper range: %+v %v", got, err)
	}
	if _, err := s.ListIssues(ctx, "default", "missing", base); !errors.Is(err, store.ErrNotFound) {
		t.Fatalf("missing project: %v", err)
	}
}

func TestIssueBackfillPreservesSummaryAndRejectsStaleEvaluation(t *testing.T) {
	s, pool := migrated(t)
	ctx := context.Background()
	at := time.Now().UTC()
	if _, err := s.CreateProject(ctx, project("history", "key-history")); err != nil {
		t.Fatal(err)
	}
	id, err := s.UpsertCall(ctx, store.CallUpsert{OrganisationID: "default", ProjectID: "history", RoomSID: "old", RoomName: "historical-room", StartedAt: &at})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := pool.Exec(ctx, "INSERT INTO call_summaries (call_id,turn_count,answer_ms,models,computed_at) VALUES ($1::uuid,9,ARRAY[123.0]::double precision[],'{}',now())", id); err != nil {
		t.Fatal(err)
	}
	if _, err := pool.Exec(ctx, "UPDATE calls SET summary_stale_at=NULL WHERE id=$1::uuid", id); err != nil {
		t.Fatal(err)
	}
	pending, err := s.PendingCallFindings(ctx, 100)
	if err != nil || len(pending) != 1 {
		t.Fatalf("pending history: %+v %v", pending, err)
	}
	first := pending[0]
	slow := []store.Finding{{ID: "slow", Title: "Slow answers", Severity: "medium", Detail: "captured response"}}
	if err := s.PutCallFindings(ctx, first, slow, []string{"slow"}, 0, true, at); err != nil {
		t.Fatal(err)
	}
	// A competing worker read the same version. Even identical timestamps cannot allow its write.
	if err := s.PutCallFindings(ctx, first, []store.Finding{{ID: "tts", Title: "Slow speech", Severity: "medium"}}, nil, 0, true, at); err != nil {
		t.Fatal(err)
	}
	if _, err := s.IssueMetadata(ctx, "default", "history", "tts"); !errors.Is(err, store.ErrNotFound) {
		t.Fatalf("stale backfill replaced evidence: %v", err)
	}
	var count int
	var answers []float64
	if err := pool.QueryRow(ctx, "SELECT turn_count,answer_ms FROM call_summaries WHERE call_id=$1::uuid", id).Scan(&count, &answers); err != nil {
		t.Fatal(err)
	}
	if count != 9 || len(answers) != 1 || answers[0] != 123 {
		t.Fatalf("historical summary changed: %d %v", count, answers)
	}
	if _, err := s.UpdateProjectSettings(ctx, "default", "history", 0, store.Thresholds{AnswerMs: 2000, JoinMs: 2000, TTSStartMs: 1000}); err != nil {
		t.Fatal(err)
	}
	pending, err = s.PendingCallFindings(ctx, 100)
	if err != nil || len(pending) != 1 || pending[0].Revision != 1 {
		t.Fatalf("settings did not requeue: %+v %v", pending, err)
	}
	if err := s.DeferCallFindings(ctx, pending[0]); err != nil {
		t.Fatal(err)
	}
	deferred, err := s.PendingCallFindings(ctx, 100)
	if err != nil || len(deferred) != 0 {
		t.Fatalf("retry backoff missing: %+v %v", deferred, err)
	}
	// An unavailable retained source records coverage but must not clear a known finding.
	if err := s.PutCallFindings(ctx, pending[0], nil, []string{}, 1, false, at); err != nil {
		t.Fatal(err)
	}
	if _, err := s.IssueMetadata(ctx, "default", "history", "slow"); err != nil {
		t.Fatalf("missing source erased evidence: %v", err)
	}
	var available bool
	var active int
	if err := pool.QueryRow(ctx, "SELECT issue_source_available FROM calls WHERE id=$1::uuid", id).Scan(&available); err != nil {
		t.Fatal(err)
	}
	if err := pool.QueryRow(ctx, "SELECT count(*) FROM issue_occurrences WHERE call_id=$1::uuid AND active", id).Scan(&active); err != nil {
		t.Fatal(err)
	}
	if available || active != 1 {
		t.Fatalf("coverage %v active %d", available, active)
	}
	pending, err = s.PendingCallFindings(ctx, 100)
	if err != nil || len(pending) != 0 {
		t.Fatalf("backfill never finishes: %+v %v", pending, err)
	}
}

func TestIssueEvidenceAdvancesOnlyOnNewOccurrences(t *testing.T) {
	s, _ := migrated(t)
	ctx := context.Background()
	if _, err := s.CreateProject(ctx, project("one", "key-one")); err != nil {
		t.Fatal(err)
	}
	at := time.Now().UTC()
	call, err := s.UpsertCall(ctx, store.CallUpsert{OrganisationID: "default", ProjectID: "one", RoomSID: "rs", RoomName: "room", StartedAt: &at})
	if err != nil {
		t.Fatal(err)
	}
	finding := func(detail string) []store.Finding {
		return []store.Finding{{ID: "slow", Title: "Slow answers", Severity: "medium", Detail: detail, Turns: []int{0}}}
	}
	if err := s.ApplyCallFindings(ctx, "default", "one", call, finding("over 1500 ms")); err != nil {
		t.Fatal(err)
	}
	resolved := "resolved"
	if _, err := s.UpdateIssueMetadata(ctx, "default", "one", "slow", 0, 1, &resolved, nil, false); err != nil {
		t.Fatal(err)
	}
	// A threshold change rewrites the detail of the same occurrence: no new failure, no regression.
	if err := s.ApplyCallFindings(ctx, "default", "one", call, finding("over 1200 ms")); err != nil {
		t.Fatal(err)
	}
	m, err := s.IssueMetadata(ctx, "default", "one", "slow")
	if err != nil || m.EvidenceRevision != 1 || m.Regressed || m.State != "resolved" {
		t.Fatalf("rewritten detail reopened the issue: %+v %v", m, err)
	}
	// The occurrence clears, then fails again: that is new evidence.
	if err := s.ApplyCallFindings(ctx, "default", "one", call, []store.Finding{}); err != nil {
		t.Fatal(err)
	}
	if err := s.ApplyCallFindings(ctx, "default", "one", call, finding("over 1200 ms")); err != nil {
		t.Fatal(err)
	}
	m, err = s.IssueMetadata(ctx, "default", "one", "slow")
	if err != nil || m.EvidenceRevision != 2 || !m.Regressed {
		t.Fatalf("reactivated occurrence did not regress: %+v %v", m, err)
	}
	// Tags and archive never compare evidence, so a stale evidence revision does not conflict.
	m, err = s.UpdateIssueMetadata(ctx, "default", "one", "slow", m.Revision, 1, nil, []string{"support"}, true)
	if err != nil || !m.Archived || m.Tags[0] != "support" || !m.Regressed {
		t.Fatalf("tags-only edit with stale evidence: %+v %v", m, err)
	}
	if _, err := s.UpdateIssueMetadata(ctx, "default", "one", "slow", m.Revision, 1, &resolved, nil, false); !errors.Is(err, store.ErrIssueConflict) {
		t.Fatalf("decision on stale evidence accepted: %v", err)
	}
}
