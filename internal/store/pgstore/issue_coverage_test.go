package pgstore

import (
	"context"
	"testing"
	"time"

	"github.com/mahimailabs/voicegateway/internal/store"
)

func TestIssueCoverageSeparatesPendingAndUnavailableWithinScope(t *testing.T) {
	s, pool := migrated(t)
	ctx := context.Background()
	const scope = "issue-coverage"
	for _, id := range []string{scope, "coverage-other"} {
		if _, err := s.CreateProject(ctx, project(id, "key-"+id)); err != nil {
			t.Fatal(err)
		}
	}
	at := time.Now().UTC().Truncate(time.Microsecond)
	from, to := at.Add(-time.Hour), at.Add(time.Hour)
	ids := map[string]string{}
	for _, name := range []string{"pending", "unavailable", "evaluated", "outside", "other"} {
		projectID := scope
		start := at
		if name == "other" {
			projectID = "coverage-other"
		}
		if name == "outside" {
			start = at.Add(-24 * time.Hour)
		}
		id, err := s.UpsertCall(ctx, store.CallUpsert{OrganisationID: "default", ProjectID: projectID, RoomSID: name, RoomName: name, StartedAt: &start})
		if err != nil {
			t.Fatal(err)
		}
		ids[name] = id
	}
	for _, name := range []string{"unavailable", "evaluated"} {
		if _, err := pool.Exec(ctx, "UPDATE calls SET issue_evaluated_at=now(),issue_settings_revision=0,issue_source_available=$2 WHERE id=$1::uuid", ids[name], name == "evaluated"); err != nil {
			t.Fatal(err)
		}
	}
	// Backoff is still pending coverage, not a successful evaluation.
	if _, err := pool.Exec(ctx, "UPDATE calls SET issue_retry_at=now()+interval '1 hour' WHERE id=$1::uuid", ids["pending"]); err != nil {
		t.Fatal(err)
	}
	args := IssueEvaluationCoverageParams{OrganisationID: "default", ProjectID: scope, FromAt: timestamp(&from), ToAt: timestamp(&to)}
	got, err := s.q.IssueEvaluationCoverage(ctx, args)
	if err != nil || got.PendingCalls != 1 || got.UnavailableCalls != 1 || got.PendingJobs != 0 {
		t.Fatalf("coverage: %+v %v", got, err)
	}
	if _, err := s.UpdateProjectSettings(ctx, "default", scope, 0, store.Thresholds{AnswerMs: 2000, JoinMs: 2000, TTSStartMs: 1000}); err != nil {
		t.Fatal(err)
	}
	got, err = s.q.IssueEvaluationCoverage(ctx, args)
	if err != nil || got.PendingCalls != 3 || got.UnavailableCalls != 0 {
		t.Fatalf("changed settings must requeue prior evaluation: %+v %v", got, err)
	}
	args.ProjectID = "coverage-other"
	got, err = s.q.IssueEvaluationCoverage(ctx, args)
	if err != nil || got.PendingCalls != 1 || got.UnavailableCalls != 0 {
		t.Fatalf("scope leakage: %+v %v", got, err)
	}
}

func TestIssueCoverageRequiresFreshCompleteSources(t *testing.T) {
	s, pool := migrated(t)
	ctx := context.Background()
	const scope = "source-coverage"
	if _, err := s.CreateProject(ctx, project(scope, "key-"+scope)); err != nil {
		t.Fatal(err)
	}
	at := time.Now().UTC().Add(-time.Minute)
	filter := store.IssueFilter{From: at.Add(-time.Hour), To: at.Add(time.Hour)}
	readList := func(want bool) store.IssueList {
		t.Helper()
		got, err := s.ListIssues(ctx, "default", scope, filter)
		if err != nil || got.Complete != want {
			t.Fatalf("complete=%t want=%t: %v", got.Complete, want, err)
		}
		return got
	}
	empty := readList(false)
	if len(empty.Coverage.Sources) != 5 || empty.Coverage.Sources[0].State != "unknown" {
		t.Fatal("missing sources must be explicit")
	}
	for _, layer := range []string{store.LayerMediaServer, store.LayerSIP, store.LayerCloudAgents, store.LayerEgress, store.LayerIngress} {
		if err := s.ApplyStandingFindings(ctx, store.LayerRead{OrganisationID: "default", ProjectID: scope, Layer: layer, ReadAt: at, Every: time.Minute, OK: true}, &store.StandingEvaluation{}); err != nil {
			t.Fatal(err)
		}
	}
	pending := readList(false)
	if pending.Coverage.Sources[3].State != "partial" || pending.Coverage.Sources[4].State != "partial" {
		t.Fatal("uninitialized media backfill reported complete")
	}
	batches, err := s.PendingMediaBackfills(ctx)
	if err != nil {
		t.Fatal(err)
	}
	readList(false)
	for _, b := range batches {
		if b.ProjectID == scope {
			if err := s.AdvanceMediaBackfill(ctx, b, 0, "", true); err != nil {
				t.Fatal(err)
			}
		}
	}
	readList(true)
	read := store.LayerRead{OrganisationID: "default", ProjectID: scope, Layer: store.LayerSIP, ReadAt: at.Add(time.Second), Every: time.Minute, OK: true}
	if err := s.ApplyStandingFindings(ctx, read, &store.StandingEvaluation{Incomplete: true}); err != nil {
		t.Fatal(err)
	}
	partial := readList(false)
	if partial.Coverage.Sources[1].State != "partial" {
		t.Fatalf("partial evidence reported as complete: %+v", partial.Coverage)
	}
	read.ReadAt = read.ReadAt.Add(time.Second)
	read.OK = false
	read.ErrorClass = store.ErrorUnreachable
	if err := s.ApplyStandingFindings(ctx, read, nil); err != nil {
		t.Fatal(err)
	}
	if got := readList(false); got.Coverage.Sources[1].State != "unavailable" {
		t.Fatal("failed source not distinguished")
	}
	if _, err := pool.Exec(ctx, "UPDATE issue_sources SET fresh_until=now()-interval '1 second' WHERE project_id=$1 AND layer='egress'", scope); err != nil {
		t.Fatal(err)
	}
	if got := readList(false); got.Coverage.Sources[3].State != "stale" {
		t.Fatal("stale source not distinguished")
	}
}
