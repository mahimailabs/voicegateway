package join

import (
	"context"
	"fmt"
	"time"

	"github.com/mahimailabs/voicegateway/internal/store"
	"github.com/mahimailabs/voicegateway/internal/turn"
)

// BackfillFindings evaluates historical calls without rebuilding their summaries. Settings changes
// requeue calls through the persisted settings revision. A missing trace is coverage information,
// not an instruction to erase a previously observed finding.
func (j *Joiner) BackfillFindings(ctx context.Context, now time.Time) error {
	calls, err := j.Calls.PendingCallFindings(ctx, 100)
	if err != nil {
		return err
	}
	for _, call := range calls {
		if ctx.Err() != nil {
			return ctx.Err()
		}
		run := ctx
		cancel := func() {}
		if j.Timeout > 0 {
			run, cancel = context.WithTimeout(ctx, j.Timeout)
		}
		err := j.backfillOne(run, call, now)
		cancel()
		if err != nil {
			if ctx.Err() != nil {
				return ctx.Err()
			}
			j.Log.Warn("findings backfill failed", "call", call.ID, "err", err)
			if deferErr := j.Calls.DeferCallFindings(ctx, call); deferErr != nil {
				return deferErr
			}
			continue
		}
		if j.Changed != nil {
			j.Changed(ctx, call.OrganisationID, call.ProjectID)
		}
	}
	return nil
}

func (j *Joiner) backfillOne(ctx context.Context, call store.PendingFindings, now time.Time) error {
	spans, result, err := j.Traces.CallTrace(ctx, call.OrganisationID, call.ProjectID, call.ID)
	if err != nil {
		return err
	}
	settings, err := j.Calls.ProjectSettings(ctx, call.OrganisationID, call.ProjectID)
	if err != nil {
		return fmt.Errorf("read finding settings: %w", err)
	}
	var findings []store.Finding
	available := len(spans) > 0 || result.NoReply
	if available {
		findings = turn.Findings(spans, result, settings.Thresholds)
	}
	return j.Calls.PutCallFindings(ctx, call, findings, turn.FindingCoverage(result), settings.Revision, available, now)
}
