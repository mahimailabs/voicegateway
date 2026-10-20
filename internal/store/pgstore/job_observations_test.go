package pgstore

import (
	"context"
	"testing"
	"time"

	"github.com/mahimailabs/voicegateway/internal/store"
)

func TestJobObservationsPreserveFailureAndRejectCrossScope(t *testing.T) {
	s, pool := migrated(t)
	ctx := context.Background()
	const projectID = "job-observations"
	if _, err := s.CreateProject(ctx, project(projectID, "job-observation-key")); err != nil {
		t.Fatal(err)
	}
	at := time.Now().UTC().Truncate(time.Microsecond)
	failedAt := at.Add(-time.Second)
	read := store.LayerRead{OrganisationID: "default", ProjectID: projectID, Layer: store.LayerMediaServer, ReadAt: at, OK: true}
	dispatches := []store.Dispatch{{ID: "AD_one", Jobs: []store.Job{{ID: "AJ_one", Status: "JS_FAILED", ParticipantIdentity: "native-identity", Statuses: []store.StatusAt{{Status: "JS_FAILED", At: &failedAt}}}}}}
	sample := store.RoomSample{OrganisationID: "default", ProjectID: projectID, ReadAt: at, Room: store.Room{SID: "RM_one", Dispatches: &dispatches}}
	apply := func() {
		t.Helper()
		if err := s.StoreJobObservations(ctx, read, []store.RoomSample{sample}); err != nil {
			t.Fatal(err)
		}
	}
	check := func(wantRevision int64) {
		t.Helper()
		var identity string
		var reported, observed time.Time
		var revision int64
		err := pool.QueryRow(ctx, "SELECT participant_identity,reported_failed_at,failure_observed_at,observation_revision FROM issue_job_observations WHERE organisation_id='default' AND project_id=$1 AND job_id='AJ_one'", projectID).Scan(&identity, &reported, &observed, &revision)
		if err != nil || identity != "native-identity" || !reported.Equal(failedAt) || !observed.Equal(at) || revision != wantRevision {
			t.Fatalf("lost native evidence: identity=%q reported=%v observed=%v revision=%d err=%v", identity, reported, observed, revision, err)
		}
	}
	apply()
	apply() // Exact replay is idempotent.
	read.ReadAt = at.Add(time.Minute)
	sample.ReadAt = read.ReadAt
	apply() // Polling the same failure does not create a new occurrence.
	check(0)
	dispatches[0].Jobs[0].ParticipantIdentity = ""
	dispatches[0].Jobs[0].Status = "JS_RUNNING"
	read.ReadAt = at.Add(2 * time.Minute)
	sample.ReadAt = read.ReadAt
	apply()
	check(1) // Later incomplete data retains failure and correlation evidence.
	read.OK = false
	if err := s.StoreJobObservations(ctx, read, nil); err != nil {
		t.Fatal(err)
	}
	read.OK = true
	if err := s.StoreJobObservations(ctx, read, nil); err != nil {
		t.Fatal(err)
	} // Closed room.
	check(1)
	sample.ProjectID = "another-project"
	if err := s.StoreJobObservations(ctx, read, []store.RoomSample{sample}); err == nil {
		t.Fatal("cross-project observation accepted")
	}
	check(1)
}
