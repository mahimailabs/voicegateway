package pgstore

import (
	"context"
	"testing"
	"time"

	"github.com/mahimailabs/voicegateway/internal/store"
)

func TestStandingFindingsSourceFailureUnknownRecoveryAndReplay(t *testing.T) {
	s, pool := migrated(t)
	ctx := context.Background()
	for _, id := range []string{"one", "two"} {
		if _, err := s.CreateProject(ctx, project(id, "key-"+id)); err != nil {
			t.Fatal(err)
		}
	}
	at := time.Now().UTC().Truncate(time.Microsecond)
	read := store.LayerRead{OrganisationID: "default", ProjectID: "one", Layer: store.LayerSIP, ReadAt: at, Every: time.Minute, OK: true}
	key := store.FindingKey{IssueID: "sip:plain", SourceID: "inbound:trunk-1"}
	found := store.StandingEvaluation{Subjects: []string{key.SourceID}, Covered: []store.FindingKey{key}, Findings: []store.StandingFinding{{Finding: store.Finding{ID: key.IssueID, Title: "Unencrypted SIP media", Severity: "medium", Detail: "Encryption disabled"}, SourceID: key.SourceID, Where: "trunk-1"}}}
	apply := func(r store.LayerRead, e *store.StandingEvaluation) {
		t.Helper()
		if err := s.ApplyStandingFindings(ctx, r, e); err != nil {
			t.Fatal(err)
		}
	}
	active := func(want int) {
		t.Helper()
		var got int
		if err := pool.QueryRow(ctx, "SELECT count(*) FROM issue_occurrences WHERE project_id='one' AND active").Scan(&got); err != nil || got != want {
			t.Fatalf("active: %d want %d (%v)", got, want, err)
		}
	}
	apply(read, &found)
	resolved := "resolved"
	if _, err := s.UpdateIssueMetadata(ctx, "default", "one", key.IssueID, 0, 1, &resolved, nil, false); err != nil {
		t.Fatal(err)
	}
	apply(read, &found)
	read.ReadAt = at.Add(time.Minute)
	apply(read, &found)
	m, err := s.IssueMetadata(ctx, "default", "one", key.IssueID)
	if err != nil || m.EvidenceRevision != 1 || m.State != "resolved" {
		t.Fatalf("unchanged read regressed decision: %+v %v", m, err)
	}
	// A successful snapshot with an unknown encryption field cannot declare recovery.
	unknown := store.StandingEvaluation{Subjects: []string{key.SourceID}}
	read.ReadAt = at.Add(2 * time.Minute)
	apply(read, &unknown)
	active(1)
	var observed time.Time
	if err := pool.QueryRow(ctx, "SELECT observed_at FROM issue_occurrences WHERE project_id='one'").Scan(&observed); err != nil || !observed.Equal(at.Add(time.Minute)) {
		t.Fatalf("unknown field refreshed evidence: %v %v", observed, err)
	}
	failed := read
	failed.ReadAt = at.Add(3 * time.Minute)
	failed.OK = false
	failed.ErrorClass = store.ErrorUnreachable
	apply(failed, nil)
	active(1)
	var ok bool
	var good time.Time
	if err := pool.QueryRow(ctx, "SELECT ok,good_at FROM issue_sources WHERE project_id='one'").Scan(&ok, &good); err != nil || ok || !good.Equal(read.ReadAt) {
		t.Fatalf("source failure: ok=%t good=%v %v", ok, good, err)
	}
	healthy := store.StandingEvaluation{Subjects: []string{key.SourceID}, Covered: []store.FindingKey{key}}
	apply(read, &healthy) // Older successful read cannot overwrite the newer failure.
	active(1)
	read.ReadAt = at.Add(4 * time.Minute)
	apply(read, &healthy)
	active(0)
	read.ReadAt = at.Add(5 * time.Minute)
	apply(read, &found)
	m, err = s.IssueMetadata(ctx, "default", "one", key.IssueID)
	if err != nil || m.EvidenceRevision != 2 || !m.Regressed {
		t.Fatalf("returned finding did not regress: %+v %v", m, err)
	}
	// Deletion is known only after a complete successful read; another scope cannot erase it.
	other := read
	other.ProjectID = "two"
	other.ReadAt = at.Add(6 * time.Minute)
	apply(other, &store.StandingEvaluation{})
	active(1)
	read.ReadAt = at.Add(6 * time.Minute)
	apply(read, &store.StandingEvaluation{})
	active(0)
}

func TestStandingFindingsInvalidBatchRollsBackSourceObservation(t *testing.T) {
	s, pool := migrated(t)
	ctx := context.Background()
	if _, err := s.CreateProject(ctx, project("one", "key")); err != nil {
		t.Fatal(err)
	}
	read := store.LayerRead{OrganisationID: "default", ProjectID: "one", Layer: store.LayerSIP, ReadAt: time.Now().UTC(), Every: time.Minute, OK: true}
	invalid := store.StandingEvaluation{Subjects: []string{"trunk"}, Findings: []store.StandingFinding{{Finding: store.Finding{ID: "slow", Title: "wrong namespace", Severity: "medium"}, SourceID: "trunk"}}}
	if err := s.ApplyStandingFindings(ctx, read, &invalid); err == nil {
		t.Fatal("invalid identity accepted")
	}
	var n int
	if err := pool.QueryRow(ctx, "SELECT count(*) FROM issue_sources").Scan(&n); err != nil || n != 0 {
		t.Fatalf("invalid batch consumed source: %d %v", n, err)
	}
	if err := s.ApplyStandingFindings(ctx, read, nil); err == nil {
		t.Fatal("missing evaluation accepted as healthy")
	}
}

func TestStandingFindingsListReportsObservationFreshness(t *testing.T) {
	s, _ := migrated(t)
	ctx := context.Background()
	if _, err := s.CreateProject(ctx, project("one", "key")); err != nil {
		t.Fatal(err)
	}
	now := time.Now().UTC().Truncate(time.Microsecond)
	read := store.LayerRead{OrganisationID: "default", ProjectID: "one", Layer: store.LayerSIP, ReadAt: now.Add(-10 * time.Minute), Every: time.Minute, OK: true}
	finding := store.StandingEvaluation{Subjects: []string{"trunk"}, Findings: []store.StandingFinding{{Finding: store.Finding{ID: "sip:plain", Title: "Media encryption disabled", Severity: "medium"}, SourceID: "trunk", Where: "trunk"}}}
	check := func(want string, observed time.Time) {
		t.Helper()
		got, err := s.ListIssues(ctx, "default", "one", store.IssueFilter{From: now.Add(-time.Hour), To: now.Add(time.Hour)})
		if err != nil {
			t.Fatal(err)
		}
		if len(got.Items) != 1 || len(got.Items[0].Hits) != 1 {
			t.Fatalf("missing preserved finding: %+v", got)
		}
		hit := got.Items[0].Hits[0]
		if hit.EvidenceState != want || hit.ObservedAt == nil || !hit.ObservedAt.Equal(observed) || hit.At != nil {
			t.Fatalf("want %s observed %v, got %+v", want, observed, hit)
		}
	}
	apply := func(e *store.StandingEvaluation) {
		t.Helper()
		if err := s.ApplyStandingFindings(ctx, read, e); err != nil {
			t.Fatal(err)
		}
	}
	apply(&finding)
	check("stale", read.ReadAt)
	read.ReadAt = now
	apply(&finding)
	check("current", now)
	read.ReadAt = now.Add(time.Second)
	apply(&store.StandingEvaluation{Subjects: []string{"trunk"}})
	check("unknown", now)
	read.ReadAt = now.Add(2 * time.Second)
	read.OK = false
	read.ErrorClass = store.ErrorPermissionDenied
	apply(nil)
	check("unavailable", now)
}
