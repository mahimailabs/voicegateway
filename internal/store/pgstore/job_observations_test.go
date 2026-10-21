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
	read := store.LayerRead{OrganisationID: "default", ProjectID: projectID, Layer: store.LayerMediaServer, ReadAt: at, Every: time.Minute, OK: true}
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
	read.ErrorClass = store.ErrorUnreachable
	read.ReadAt = at.Add(3 * time.Minute)
	if err := s.StoreJobObservations(ctx, read, nil); err != nil {
		t.Fatal(err)
	}
	read.OK = true
	read.ErrorClass = ""
	read.ReadAt = at.Add(4 * time.Minute)
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

func TestJobObservationSourceCoverageAndLateRead(t *testing.T) {
	s, _ := migrated(t)
	ctx := context.Background()
	const scope = "job-source-health"
	if _, err := s.CreateProject(ctx, project(scope, "key-"+scope)); err != nil {
		t.Fatal(err)
	}
	at := time.Now().UTC().Add(-time.Minute)
	read := store.LayerRead{OrganisationID: "default", ProjectID: scope, Layer: store.LayerMediaServer, ReadAt: at, Every: time.Minute, OK: true, JobFailuresSupported: true}
	state := func(want string) {
		t.Helper()
		rows, err := s.q.IssueSourceCoverage(ctx, IssueSourceCoverageParams{OrganisationID: "default", ProjectID: scope})
		if err != nil || len(rows) != 1 || rows[0].State != want {
			t.Fatalf("source state: %+v %v want %s", rows, err, want)
		}
	}
	if err := s.StoreJobObservations(ctx, read, []store.RoomSample{{OrganisationID: "default", ProjectID: scope, ReadAt: at, Room: store.Room{SID: "RM_partial"}}}); err != nil {
		t.Fatal(err)
	}
	state("partial")
	failed := read
	failed.ReadAt = at.Add(time.Second)
	failed.OK = false
	failed.ErrorClass = store.ErrorUnreachable
	if err := s.StoreJobObservations(ctx, failed, nil); err != nil {
		t.Fatal(err)
	}
	state("unavailable")
	// An older successful snapshot cannot erase a newer connection failure.
	if err := s.StoreJobObservations(ctx, read, nil); err != nil {
		t.Fatal(err)
	}
	state("unavailable")
	read.ReadAt = at.Add(2 * time.Second)
	if err := s.StoreJobObservations(ctx, read, []store.RoomSample{}); err != nil {
		t.Fatal(err)
	}
	state("current")
	read.ReadAt = read.ReadAt.Add(time.Second)
	read.JobFailuresSupported = false
	if err := s.StoreJobObservations(ctx, read, []store.RoomSample{}); err != nil {
		t.Fatal(err)
	}
	state("partial") // Successful self-hosted polling cannot prove job-failure coverage.
}
