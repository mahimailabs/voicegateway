package pgstore

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/mahimailabs/voicegateway/internal/store"
)

func TestRecordingFindingReplayScopeAndTerminalEvidence(t *testing.T) {
	s, pool := migrated(t)
	ctx := context.Background()
	for _, id := range []string{"recording-one", "recording-two"} {
		if _, err := s.CreateProject(ctx, project(id, "key-"+id)); err != nil {
			t.Fatal(err)
		}
	}
	at := time.Now().UTC().Truncate(time.Microsecond)
	f := store.RecordingFinding{OrganisationID: "default", ProjectID: "recording-one", EventID: "EV_failure", EgressID: "EG_one", Status: "EGRESS_FAILED", At: &at}
	if err := s.PutRecordingFinding(ctx, f); err != nil {
		t.Fatal(err)
	}
	resolved := "resolved"
	if _, err := s.UpdateIssueMetadata(ctx, "default", f.ProjectID, "recording-lost", 0, 1, &resolved, nil, false); err != nil {
		t.Fatal(err)
	}
	if err := s.PutRecordingFinding(ctx, f); err != nil {
		t.Fatal(err)
	}
	m, err := NewStore(pool, s.sealer).IssueMetadata(ctx, "default", f.ProjectID, "recording-lost")
	if err != nil || m.State != "resolved" || m.EvidenceRevision != 1 {
		t.Fatalf("replay: %+v %v", m, err)
	}
	call, err := s.UpsertCall(ctx, store.CallUpsert{OrganisationID: "default", ProjectID: f.ProjectID, RoomSID: "RM_recording", StartedAt: &at})
	if err != nil {
		t.Fatal(err)
	}
	f.CallID = call
	if err := s.PutRecordingFinding(ctx, f); err != nil {
		t.Fatal(err)
	}
	if err := s.ApplyCallFindings(ctx, "default", f.ProjectID, call, []store.Finding{}); err != nil {
		t.Fatal(err)
	}
	var active bool
	var linked string
	if err := pool.QueryRow(ctx, "SELECT active,call_id::text FROM issue_occurrences WHERE project_id=$1 AND source_id=$2", f.ProjectID, "webhook:"+f.EventID).Scan(&active, &linked); err != nil || !active || linked != call {
		t.Fatalf("terminal evidence lost: %v %s %v", active, linked, err)
	}
	wrong := f
	wrong.ProjectID = "recording-two"
	if err := s.PutRecordingFinding(ctx, wrong); !errors.Is(err, store.ErrNotFound) {
		t.Fatalf("cross-project call accepted: %v", err)
	}
	wrong.CallID = ""
	if err := s.PutRecordingFinding(ctx, wrong); err != nil {
		t.Fatal(err)
	}
	f.EventID = "EV_limit"
	f.Status = "EGRESS_LIMIT_REACHED"
	if err := s.PutRecordingFinding(ctx, f); err != nil {
		t.Fatal(err)
	}
	var detail string
	if err := pool.QueryRow(ctx, "SELECT detail->>'detail' FROM issue_occurrences WHERE project_id=$1 AND source_id=$2", f.ProjectID, "webhook:"+f.EventID).Scan(&detail); err != nil || !strings.Contains(detail, "cut short") || strings.Contains(detail, "lost") {
		t.Fatalf("limit is not loss: %q %v", detail, err)
	}
	f.EventID = "EV_complete"
	f.Status = "EGRESS_COMPLETE"
	if err := s.PutRecordingFinding(ctx, f); err == nil {
		t.Fatal("healthy completion accepted as failure")
	}
	var count int
	if err := pool.QueryRow(ctx, "SELECT count(*) FROM issue_occurrences WHERE project_id=$1", f.ProjectID).Scan(&count); err != nil || count != 2 {
		t.Fatalf("occurrences %d: %v", count, err)
	}
}
