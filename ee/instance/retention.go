package instance

import (
	"context"
	"errors"
	"log/slog"
	"time"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// Plans lists the organisations whose plan limits how long call data is kept.
type Plans interface {
	RetentionPlans(ctx context.Context) ([]store.RetentionPlan, error)
	TrimCalls(ctx context.Context, organisationID string, cutoff time.Time) (int64, error)
}

// Telemetry deletes an organisation's stored call data older than a cutoff.
type Telemetry interface {
	TrimTelemetry(ctx context.Context, organisationID string, cutoff time.Time) error
}

// Retention trims each organisation's call data to its plan's retentionDays (decision 0042): the
// call index first, so the console never lists a call whose data is gone, then the telemetry.
// Billed usage stays. An organisation without the limit keeps the install's own retention.
type Retention struct {
	Plans     Plans
	Telemetry Telemetry
	Log       *slog.Logger
}

// Run trims what is past each plan's retention by now. Safe to run on every worker at once; one
// organisation's failure does not stop the others.
func (r *Retention) Run(ctx context.Context, now time.Time) error {
	plans, err := r.Plans.RetentionPlans(ctx)
	if err != nil {
		return err
	}
	var errs []error
	for _, p := range plans {
		cutoff := now.Add(-time.Duration(p.Days) * 24 * time.Hour)
		n, err := r.Plans.TrimCalls(ctx, p.OrganisationID, cutoff)
		if err != nil {
			errs = append(errs, err)
			continue
		}
		if err := r.Telemetry.TrimTelemetry(ctx, p.OrganisationID, cutoff); err != nil {
			errs = append(errs, err)
			continue
		}
		if n > 0 {
			r.Log.Info("call data past plan retention trimmed", "organisation", p.OrganisationID, "days", p.Days, "calls", n)
		}
	}
	return errors.Join(errs...)
}
