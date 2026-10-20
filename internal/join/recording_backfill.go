package join

import (
	"context"
	"errors"
	"time"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// BackfillRecordings advances only after durable findings. Partial failures replay safely.
func (j *Joiner) BackfillRecordings(ctx context.Context, _ time.Time) error {
	batches, err := j.Calls.PendingRecordingBackfills(ctx)
	if err != nil {
		return err
	}
	for _, b := range batches {
		run, cancel := context.WithTimeout(ctx, 30*time.Second)
		err := j.backfillRecordingPage(run, b)
		cancel()
		if err != nil {
			if ctx.Err() != nil {
				return ctx.Err()
			}
			j.Log.Warn("recording backfill failed", "project", b.ProjectID, "err", err)
			if err := j.Calls.DeferRecordingBackfill(ctx, b); err != nil {
				return err
			}
			continue
		}
		if j.Changed != nil {
			j.Changed(ctx, b.OrganisationID, b.ProjectID)
		}
	}
	return nil
}

func (j *Joiner) backfillRecordingPage(ctx context.Context, b store.RecordingBackfill) error {
	rows, err := j.Traces.RecordingEvents(ctx, b)
	if err != nil {
		return err
	}
	for _, row := range rows {
		err := j.recordingFindings(ctx, []store.Event{row})
		// Retention can remove a linked call while retaining the component event.
		// Preserve the event without recreating the deleted call or linking another scope.
		if errors.Is(err, store.ErrNotFound) && row.CallID != "" {
			row.CallID = ""
			err = j.recordingFindings(ctx, []store.Event{row})
		}
		if err != nil {
			return err
		}
	}
	ns, key := b.CursorNS, b.CursorKey
	if len(rows) > 0 {
		last := rows[len(rows)-1]
		ns, key = last.ReceivedAt.UnixNano(), last.DedupeKey
	}
	return j.Calls.AdvanceRecordingBackfill(ctx, b, ns, key, len(rows) < 100)
}
