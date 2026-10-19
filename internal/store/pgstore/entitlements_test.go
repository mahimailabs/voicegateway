package pgstore

import (
	"bytes"
	"errors"
	"net/url"
	"os"
	"sync"
	"testing"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/mahimailabs/voicegateway/internal/seal"

	"github.com/mahimailabs/voicegateway/internal/store"
)

func TestEntitlementLedgerAndRevisions(t *testing.T) {
	s, pool := quotaDatabase(t)
	ctx := t.Context()
	if _, err := s.CreateProject(ctx, project("quota", "quota-key")); err != nil {
		t.Fatal(err)
	}
	from := time.Date(2026, 10, 19, 0, 0, 0, 0, time.UTC)
	e := store.Entitlement{From: from, To: from.Add(30 * 24 * time.Hour), LimitMinutes: 2000, Active: true, Revision: 1}
	if err := s.PutEntitlement(ctx, "default", e); err != nil {
		t.Fatal(err)
	}
	var id string
	if err := pool.QueryRow(ctx, `INSERT INTO calls(organisation_id,project_id,started_at,ended_at,end_state) VALUES('default','quota',$1,$2,'observed') RETURNING id::text`, from, from.Add(120000*time.Second)).Scan(&id); err != nil {
		t.Fatal(err)
	}
	// Replayed worker writes and repeated entitlement requests never double count.
	var wg sync.WaitGroup
	for range 10 {
		wg.Go(func() {
			if err := s.PutEntitlement(ctx, "default", e); err != nil {
				t.Error(err)
			}
		})
	}
	wg.Wait()
	if _, err := pool.Exec(ctx, "UPDATE calls SET ended_at=ended_at WHERE id=$1", id); err != nil {
		t.Fatal(err)
	}
	u, err := s.EntitlementUsage(ctx, "default")
	if err != nil || u.CallSeconds != 120000 {
		t.Fatalf("usage %+v %v", u, err)
	}
	if _, err := pool.Exec(ctx, "DELETE FROM calls WHERE id=$1", id); err != nil {
		t.Fatal(err)
	}
	u, err = s.EntitlementUsage(ctx, "default")
	if err != nil || u.CallSeconds != 120000 {
		t.Fatalf("retention reset usage %+v %v", u, err)
	}
	e.LimitMinutes = 3000
	if err := s.PutEntitlement(ctx, "default", e); !errors.Is(err, store.ErrEntitlementRevision) {
		t.Fatalf("conflicting replay: %v", err)
	}
	e.Revision = 2
	e.Active = false
	if err := s.PutEntitlement(ctx, "default", e); err != nil {
		t.Fatal(err)
	}
	e.Revision = 1
	e.Active = true
	if err := s.PutEntitlement(ctx, "default", e); !errors.Is(err, store.ErrEntitlementRevision) {
		t.Fatalf("stale replay: %v", err)
	}
	e.Revision = 3
	e.From = e.To
	e.To = e.To.Add(30 * 24 * time.Hour)
	if err := s.PutEntitlement(ctx, "default", e); err != nil {
		t.Fatal(err)
	}
	u, err = s.EntitlementUsage(ctx, "default")
	if err != nil || u.CallSeconds != 0 {
		t.Fatalf("renewal %+v %v", u, err)
	}

	// Fractional seconds are summed before rounding. The exclusive period end is not billed here.
	for _, duration := range []time.Duration{90600 * time.Millisecond, 29600 * time.Millisecond} {
		if _, err := pool.Exec(ctx, "INSERT INTO calls(organisation_id,project_id,started_at,ended_at,end_state) VALUES('default','quota',$1,$2,'observed')", e.From, e.From.Add(duration)); err != nil {
			t.Fatal(err)
		}
	}
	if _, err := pool.Exec(ctx, "INSERT INTO calls(organisation_id,project_id,started_at,ended_at,end_state) VALUES('default','quota',$1,$2,'observed')", e.To.Add(-time.Hour), e.To); err != nil {
		t.Fatal(err)
	}
	u, err = s.EntitlementUsage(ctx, "default")
	if err != nil || u.CallSeconds != 120 {
		t.Fatalf("fractional duration and period boundary %+v %v", u, err)
	}
}

func quotaDatabase(t *testing.T) (*Store, *pgxpool.Pool) {
	t.Helper()
	databaseURL := os.Getenv("VOICEGATEWAY_QUOTA_TEST_DATABASE_URL")
	if databaseURL == "" {
		return migrated(t)
	}
	parsed, err := url.Parse(databaseURL)
	if err != nil || parsed.Path != "/vg_quota_test" {
		t.Fatal("quota test database must be named vg_quota_test")
	}
	pool, err := pgxpool.New(t.Context(), databaseURL)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(pool.Close)
	if _, err := Migrate(t.Context(), pool); err != nil {
		t.Fatal(err)
	}
	// This opt-in database must be a fresh disposable instance.
	_, err = pool.Exec(t.Context(), "TRUNCATE telemetry_admissions,profiled_call_usage,organisation_entitlements,calls,projects CASCADE")
	if err != nil {
		t.Fatal(err)
	}
	// NewStore needs a sealer for creating the fixture project.
	sealer, err := seal.New(bytes.Repeat([]byte{7}, seal.KeySize))
	if err != nil {
		t.Fatal(err)
	}
	return NewStore(pool, sealer), pool
}
