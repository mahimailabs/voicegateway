package pgstore

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/mahimailabs/voicegateway/internal/store"
)

func TestMediaFindingReplayScopeAndTerminalEvidence(t *testing.T) {
	s, pool := migrated(t)
	ctx := context.Background()
	for _, id := range []string{"recording-one", "recording-two"} {
		if _, err := s.CreateProject(ctx, project(id, "key-"+id)); err != nil {
			t.Fatal(err)
		}
	}
	at := time.Now().UTC().Truncate(time.Microsecond)
	f := store.MediaFinding{OrganisationID: "default", ProjectID: "recording-one", EventID: "EV_failure", ComponentID: "EG_one", Layer: "egress", Status: "EGRESS_FAILED", At: &at}
	if err := s.PutMediaFinding(ctx, f); err != nil {
		t.Fatal(err)
	}
	resolved := "resolved"
	if _, err := s.UpdateIssueMetadata(ctx, "default", f.ProjectID, "recording-lost", 0, 1, &resolved, nil, false); err != nil {
		t.Fatal(err)
	}
	if err := s.PutMediaFinding(ctx, f); err != nil {
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
	if err := s.PutMediaFinding(ctx, f); err != nil {
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
	if err := s.PutMediaFinding(ctx, wrong); !errors.Is(err, store.ErrNotFound) {
		t.Fatalf("cross-project call accepted: %v", err)
	}
	wrong.CallID = ""
	if err := s.PutMediaFinding(ctx, wrong); err != nil {
		t.Fatal(err)
	}
	f.EventID = "EV_limit"
	f.Status = "EGRESS_LIMIT_REACHED"
	if err := s.PutMediaFinding(ctx, f); err != nil {
		t.Fatal(err)
	}
	var detail string
	if err := pool.QueryRow(ctx, "SELECT detail->>'detail' FROM issue_occurrences WHERE project_id=$1 AND source_id=$2", f.ProjectID, "webhook:"+f.EventID).Scan(&detail); err != nil || !strings.Contains(detail, "cut short") || strings.Contains(detail, "lost") {
		t.Fatalf("limit is not loss: %q %v", detail, err)
	}
	f.EventID = "EV_complete"
	f.Status = "EGRESS_COMPLETE"
	if err := s.PutMediaFinding(ctx, f); err == nil {
		t.Fatal("healthy completion accepted as failure")
	}
	var count int
	if err := pool.QueryRow(ctx, "SELECT count(*) FROM issue_occurrences WHERE project_id=$1", f.ProjectID).Scan(&count); err != nil || count != 2 {
		t.Fatalf("occurrences %d: %v", count, err)
	}
	// Deleting the call keeps the recorded failure and drops only its link to the call.
	if _, err := pool.Exec(ctx, "DELETE FROM calls WHERE id=$1", call); err != nil {
		t.Fatal(err)
	}
	var unlinked *string
	if err := pool.QueryRow(ctx, "SELECT call_id::text FROM issue_occurrences WHERE project_id=$1 AND source_id=$2", f.ProjectID, "webhook:EV_failure").Scan(&unlinked); err != nil || unlinked != nil {
		t.Fatalf("call deletion removed media evidence: %v %v", unlinked, err)
	}
	list, err := s.ListIssues(ctx, "default", f.ProjectID, store.IssueFilter{From: at.Add(-time.Hour), To: at.Add(time.Hour)})
	if err != nil || len(list.Items) != 1 || list.Items[0].ID != "recording-lost" || list.Items[0].Source != "delivery" || len(list.Items[0].Hits) != 2 {
		t.Fatalf("listed after call deletion: %+v %v", list.Items, err)
	}
}

func TestMediaSourceReadsPreserveTerminalEvents(t *testing.T) {
	s, pool := migrated(t)
	ctx := context.Background()
	const scope = "media-source-history"
	if _, err := s.CreateProject(ctx, project(scope, "key-history")); err != nil {
		t.Fatal(err)
	}
	at := time.Now().UTC()
	for _, layer := range []string{"egress", "ingress"} {
		status := "EGRESS_FAILED"
		if layer == "ingress" {
			status = "ENDPOINT_ERROR"
		}
		if err := s.PutMediaFinding(ctx, store.MediaFinding{OrganisationID: "default", ProjectID: scope, EventID: "EV_" + layer, ComponentID: "component-" + layer, Layer: layer, Status: status, At: &at}); err != nil {
			t.Fatal(err)
		}
		read := store.LayerRead{OrganisationID: "default", ProjectID: scope, Layer: layer, ReadAt: at, Every: time.Minute, ErrorClass: store.ErrorUnreachable}
		if err := s.ApplyStandingFindings(ctx, read, nil); err != nil {
			t.Fatal(err)
		}
		read.ReadAt = at.Add(time.Second)
		read.OK = true
		read.ErrorClass = ""
		if err := s.ApplyStandingFindings(ctx, read, &store.StandingEvaluation{}); err != nil {
			t.Fatal(err)
		}
	}
	var count int
	if err := pool.QueryRow(ctx, "SELECT count(*) FROM issue_occurrences WHERE project_id=$1 AND active", scope).Scan(&count); err != nil || count != 2 {
		t.Fatalf("poll erased terminal evidence: %d %v", count, err)
	}
}
