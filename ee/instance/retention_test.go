package instance

import (
	"context"
	"errors"
	"io"
	"log/slog"
	"testing"
	"time"

	"github.com/mahimailabs/voicegateway/internal/store"
)

type retentionFake struct {
	plans    []store.RetentionPlan
	failCall string
	steps    []string
	cutoffs  map[string]time.Time
}

func (f *retentionFake) RetentionPlans(context.Context) ([]store.RetentionPlan, error) {
	return f.plans, nil
}

func (f *retentionFake) TrimCalls(_ context.Context, org string, cutoff time.Time) (int64, error) {
	f.steps = append(f.steps, "calls "+org)
	f.cutoffs[org] = cutoff
	if org == f.failCall {
		return 0, errors.New("database unavailable")
	}
	return 1, nil
}

func (f *retentionFake) TrimTelemetry(_ context.Context, org string, cutoff time.Time) error {
	f.steps = append(f.steps, "telemetry "+org)
	if !cutoff.Equal(f.cutoffs[org]) {
		return errors.New("telemetry cutoff differs from the call index's")
	}
	return nil
}

// Each plan is trimmed at now minus its days, the call index before the telemetry; one
// organisation failing is reported and does not stop the next.
func TestRetentionTrimsEachPlan(t *testing.T) {
	now := time.Date(2026, 11, 14, 12, 0, 0, 0, time.UTC)
	f := &retentionFake{plans: []store.RetentionPlan{{OrganisationID: "a", Days: 7}, {OrganisationID: "b", Days: 30}, {OrganisationID: "c", Days: 1}}, failCall: "b", cutoffs: map[string]time.Time{}}
	r := &Retention{Plans: f, Telemetry: f, Log: slog.New(slog.NewTextHandler(io.Discard, nil))}
	if err := r.Run(t.Context(), now); err == nil {
		t.Fatal("a failed organisation was not reported")
	}
	want := []string{"calls a", "telemetry a", "calls b", "calls c", "telemetry c"}
	if len(f.steps) != len(want) {
		t.Fatalf("steps %v, want %v", f.steps, want)
	}
	for i := range want {
		if f.steps[i] != want[i] {
			t.Fatalf("steps %v, want %v", f.steps, want)
		}
	}
	if got := f.cutoffs["a"]; !got.Equal(now.Add(-7 * 24 * time.Hour)) {
		t.Fatalf("cutoff for 7 days %v", got)
	}
}
