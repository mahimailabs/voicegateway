package pgstore

import (
	"crypto/sha256"
	"errors"
	"testing"
	"time"

	"github.com/mahimailabs/voicegateway/internal/store"
)

func limit(n int32) *int32 { return &n }

// A plan's project and member limits refuse one more, members and open invitations both counting;
// without a plan, or with a null limit, nothing is refused (decision 0042).
func TestPlanLimitsRefuseProjectsAndInvitations(t *testing.T) {
	s, _ := migrated(t)
	ctx := t.Context()
	invite := func(email string) error {
		sum := sha256.Sum256([]byte(email))
		role := "member"
		_, err := s.CreateInvitation(ctx, store.NewInvitation{Kind: "invite", OrganisationID: "default", Email: email, Role: &role, TokenSHA256: sum[:], ExpiresAt: time.Now().Add(time.Hour)})
		return err
	}
	// No plan: unlimited.
	if _, err := s.CreateProject(ctx, project("one", "key-one")); err != nil {
		t.Fatal(err)
	}
	if err := invite("a@example.com"); err != nil {
		t.Fatal(err)
	}
	from := time.Now().Add(-time.Hour).UTC().Truncate(time.Microsecond)
	e := store.Entitlement{From: from, To: from.Add(30 * 24 * time.Hour), LimitCredits: 100, MaxProjects: limit(1), MaxMembers: limit(2), Active: true, Revision: 1}
	if err := s.PutEntitlement(ctx, "default", e); err != nil {
		t.Fatal(err)
	}
	if _, err := s.CreateProject(ctx, project("two", "key-two")); !errors.Is(err, store.ErrPlanLimit) {
		t.Fatalf("second project past a limit of 1: %v", err)
	}
	// One open invitation of two seats; replacing it for the same email does not count it twice.
	if err := invite("a@example.com"); err != nil {
		t.Fatalf("replacing an open invitation: %v", err)
	}
	if err := invite("b@example.com"); err != nil {
		t.Fatal(err)
	}
	if err := invite("c@example.com"); !errors.Is(err, store.ErrPlanLimit) {
		t.Fatalf("third seat past a limit of 2: %v", err)
	}
	// A null limit lifts it.
	e.Revision, e.MaxProjects, e.MaxMembers = 2, nil, nil
	if err := s.PutEntitlement(ctx, "default", e); err != nil {
		t.Fatal(err)
	}
	if _, err := s.CreateProject(ctx, project("two", "key-two")); err != nil {
		t.Fatal(err)
	}
	if err := invite("c@example.com"); err != nil {
		t.Fatal(err)
	}
	u, err := s.EntitlementUsage(ctx, "default")
	if err != nil || u.LimitCredits != 100 || u.MaxProjects != nil || u.MaxMembers != nil || u.RetentionDays != nil {
		t.Fatalf("usage %+v %v", u, err)
	}
}

// Trimming to a plan's retention deletes closed calls that ended before the cutoff with their
// summaries and participants, keeps open and recent calls, other organisations' calls and the
// billed usage.
func TestTrimCallsKeepsUsage(t *testing.T) {
	s, pool := migrated(t)
	ctx := t.Context()
	if _, err := s.CreateProject(ctx, project("trim", "trim-key")); err != nil {
		t.Fatal(err)
	}
	now := time.Now().UTC()
	e := store.Entitlement{From: now.Add(-40 * 24 * time.Hour), To: now.Add(20 * 24 * time.Hour), LimitCredits: 1000, RetentionDays: limit(7), Active: true, Revision: 1}
	if err := s.PutEntitlement(ctx, "default", e); err != nil {
		t.Fatal(err)
	}
	insert := func(start time.Time, state string) string {
		var id string
		var end *time.Time
		if state != "open" {
			t := start.Add(time.Minute)
			end = &t
		}
		if err := pool.QueryRow(ctx, `INSERT INTO calls(organisation_id,project_id,started_at,ended_at,end_state,first_seen_at,last_activity_at)
            VALUES('default','trim',$1,$2,$3,$1,$1) RETURNING id::text`, start, end, state).Scan(&id); err != nil {
			t.Fatal(err)
		}
		return id
	}
	old := insert(now.Add(-10*24*time.Hour), "observed")
	openOld := insert(now.Add(-10*24*time.Hour), "open")
	recent := insert(now.Add(-time.Hour), "observed")
	if _, err := pool.Exec(ctx, "INSERT INTO call_participants(call_id,participant_sid,identity,kind) VALUES($1,'PA_1','caller','standard')", old); err != nil {
		t.Fatal(err)
	}
	plans, err := s.RetentionPlans(ctx)
	if err != nil || len(plans) != 1 || plans[0].OrganisationID != "default" || plans[0].Days != 7 {
		t.Fatalf("plans %+v %v", plans, err)
	}
	n, err := s.TrimCalls(ctx, "default", now.Add(-7*24*time.Hour))
	if err != nil || n != 1 {
		t.Fatalf("trimmed %d %v", n, err)
	}
	for id, want := range map[string]bool{old: false, openOld: true, recent: true} {
		var exists bool
		if err := pool.QueryRow(ctx, "SELECT EXISTS(SELECT 1 FROM calls WHERE id=$1)", id).Scan(&exists); err != nil || exists != want {
			t.Fatalf("call %s exists %v, want %v (%v)", id, exists, want, err)
		}
	}
	var billed bool
	if err := pool.QueryRow(ctx, "SELECT EXISTS(SELECT 1 FROM profiled_call_usage WHERE call_id=$1)", old).Scan(&billed); err != nil || !billed {
		t.Fatalf("billed usage of a trimmed call kept %v %v", billed, err)
	}
}
