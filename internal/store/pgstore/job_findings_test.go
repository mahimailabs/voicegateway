package pgstore

import (
	"context"
	"testing"
	"time"

	"github.com/mahimailabs/voicegateway/internal/scrape"
	"github.com/mahimailabs/voicegateway/internal/store"
)

func TestJobFindingsLateExactJoinAndSettings(t *testing.T) {
	s, pool := migrated(t)
	ctx := context.Background()
	const scope = "job-findings"
	for _, id := range []string{scope, "job-other"} {
		if _, err := s.CreateProject(ctx, project(id, "key-"+id)); err != nil {
			t.Fatal(err)
		}
	}
	at := time.Now().UTC().Truncate(time.Microsecond)
	dispatchAt := at.Add(-10 * time.Second)
	ds := []store.Dispatch{{ID: "AD_exact", CreatedAt: &dispatchAt, Jobs: []store.Job{{ID: "AJ_exact", ParticipantIdentity: "actual-agent", Status: "JS_FAILED"}}}}
	read := store.LayerRead{OrganisationID: "default", ProjectID: scope, Layer: store.LayerMediaServer, ReadAt: at, Every: time.Minute, OK: true}
	if err := s.StoreJobObservations(ctx, read, []store.RoomSample{{OrganisationID: "default", ProjectID: scope, ReadAt: at, Room: store.Room{SID: "RM_exact", Dispatches: &ds}}}); err != nil {
		t.Fatal(err)
	}
	evaluator := scrape.JobEvaluator{Store: s}
	tick := func() {
		t.Helper()
		if err := evaluator.Tick(ctx, at); err != nil {
			t.Fatal(err)
		}
	}
	tick()
	var linked *string
	var revision int64
	if err := pool.QueryRow(ctx, "SELECT call_id::text FROM issue_occurrences WHERE project_id=$1 AND issue_id='job-failed'", scope).Scan(&linked); err != nil || linked != nil {
		t.Fatalf("failure without call: %v %v", linked, err)
	}
	call, err := s.UpsertCall(ctx, store.CallUpsert{OrganisationID: "default", ProjectID: scope, RoomSID: "RM_exact", StartedAt: &dispatchAt})
	if err != nil {
		t.Fatal(err)
	}
	other, err := s.UpsertCall(ctx, store.CallUpsert{OrganisationID: "default", ProjectID: "job-other", RoomSID: "RM_exact", StartedAt: &dispatchAt})
	if err != nil {
		t.Fatal(err)
	}
	joined := dispatchAt.Add(5 * time.Second)
	// Neither another project's identity nor a different participant in this room is evidence.
	for _, p := range []store.Participant{{CallID: other, SID: "PA_other", Identity: "actual-agent", Kind: "agent", JoinedAt: &joined}, {CallID: call, SID: "PA_wrong", Identity: "another-agent", Kind: "agent", JoinedAt: &joined}} {
		if err := s.UpsertParticipant(ctx, p); err != nil {
			t.Fatal(err)
		}
	}
	tick()
	var count int
	if err := pool.QueryRow(ctx, "SELECT count(*) FROM issue_occurrences WHERE project_id=$1 AND issue_id='slow-join'", scope).Scan(&count); err != nil || count != 0 {
		t.Fatalf("guessed join: %d %v", count, err)
	}
	if err := pool.QueryRow(ctx, "SELECT call_id::text FROM issue_occurrences WHERE project_id=$1 AND issue_id='job-failed'", scope).Scan(&linked); err != nil || linked == nil || *linked != call {
		t.Fatalf("late call link: %v %v", linked, err)
	}
	if err := s.UpsertParticipant(ctx, store.Participant{CallID: call, SID: "PA_exact", Identity: "actual-agent", Kind: "agent", JoinedAt: &joined}); err != nil {
		t.Fatal(err)
	}
	stale, err := s.PendingJobFindings(ctx, 100)
	if err != nil {
		t.Fatal(err)
	}
	tick()
	if err := pool.QueryRow(ctx, "SELECT evidence_revision FROM issues WHERE project_id=$1 AND id='slow-join'", scope).Scan(&revision); err != nil || revision != 1 {
		t.Fatalf("exact slow join missing: %d %v", revision, err)
	}
	tick()
	// An old worker evaluation cannot erase the completed newer evaluation.
	for _, job := range stale {
		if job.ProjectID == scope {
			if err := s.PutJobFindings(ctx, job, nil, []string{"slow-join"}, 0); err != nil {
				t.Fatal(err)
			}
		}
	}
	if err := pool.QueryRow(ctx, "SELECT count(*) FROM issue_occurrences WHERE project_id=$1 AND issue_id='slow-join' AND active", scope).Scan(&count); err != nil || count != 1 {
		t.Fatalf("stale write erased finding: %d %v", count, err)
	}
	// Call findings own a different source and must not clear linked job findings.
	if err := s.ApplyCallFindings(ctx, "default", scope, call, []store.Finding{}); err != nil {
		t.Fatal(err)
	}
	settings, err := s.ProjectSettings(ctx, "default", scope)
	if err != nil {
		t.Fatal(err)
	}
	settings.Thresholds.JoinMs = 6000
	if _, err = s.UpdateProjectSettings(ctx, "default", scope, settings.Revision, settings.Thresholds); err != nil {
		t.Fatal(err)
	}
	tick()
	if err := pool.QueryRow(ctx, "SELECT count(*) FROM issue_occurrences WHERE project_id=$1 AND active", scope).Scan(&count); err != nil || count != 1 {
		t.Fatalf("threshold change should clear only slow join: %d %v", count, err)
	}
}
