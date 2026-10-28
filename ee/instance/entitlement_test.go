package instance

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/mahimailabs/voicegateway/internal/store"
)

type quotaStore struct {
	u   store.EntitlementUsage
	err error
}

func (s quotaStore) PutEntitlement(context.Context, string, store.Entitlement) error { return nil }
func (s quotaStore) EntitlementUsage(context.Context, string) (store.EntitlementUsage, error) {
	return s.u, s.err
}

func TestQuotaBoundaries(t *testing.T) {
	now := time.Date(2026, 10, 19, 0, 0, 0, 0, time.UTC)
	for _, tt := range []struct {
		name    string
		seconds int64
		active  bool
		now     time.Time
		allow   bool
		warning int
	}{
		{"below warning", 95999, true, now, true, 0},
		{"80 percent", 96000, true, now, true, 80},
		{"95 percent", 114000, true, now, true, 95},
		{"last second", 119999, true, now, true, 95},
		{"at cap", 120000, true, now, false, 100},
		{"over cap", 180000, true, now, false, 100},
		{"inactive", 0, false, now, false, 0},
		{"before period", 0, true, now.Add(-time.Second), false, 0},
		{"exclusive end", 0, true, now.Add(30 * 24 * time.Hour), false, 0},
	} {
		t.Run(tt.name, func(t *testing.T) {
			u := store.EntitlementUsage{Entitlement: store.Entitlement{From: now, To: now.Add(30 * 24 * time.Hour), LimitMinutes: 2000, Active: tt.active}, CallSeconds: tt.seconds}
			q := Quota{Store: quotaStore{u: u}, Now: func() time.Time { return tt.now }}
			got, w, err := q.Check(t.Context(), "org")
			if got != tt.allow || w != tt.warning || err != nil {
				t.Fatalf("got %v,%d,%v", got, w, err)
			}
			// A refusal names its reason for the console: the cap within an active period, the
			// plan otherwise.
			want := store.ReasonPlanInactive
			if tt.active && tt.now.Equal(now) {
				want = store.ReasonPlanCap
			}
			if !got && q.Refusal(t.Context(), "org") != want {
				t.Fatalf("refusal %q, want %q", q.Refusal(t.Context(), "org"), want)
			}
		})
	}
	for _, err := range []error{store.ErrNotFound, errors.New("database unavailable")} {
		q := Quota{Store: quotaStore{err: err}, Now: func() time.Time { return now }}
		ok, _, got := q.Check(t.Context(), "org")
		if ok || ((got == nil) != (err == store.ErrNotFound)) {
			t.Fatalf("error outcome %v %v", ok, got)
		}
		if r := q.Refusal(t.Context(), "org"); r != store.ReasonPlanInactive {
			t.Fatalf("refusal without a plan %q", r)
		}
	}
}
