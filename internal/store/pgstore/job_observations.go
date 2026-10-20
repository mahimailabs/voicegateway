package pgstore

import (
	"context"
	"errors"
	"fmt"
	"sort"
	"time"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// StoreJobObservations retains native dispatch evidence independently of room lifetime.
// Failed reads and absent jobs never erase a previously reported failure or identity.
func (s *Store) StoreJobObservations(ctx context.Context, read store.LayerRead, rooms []store.RoomSample) error {
	if read.Layer != store.LayerMediaServer || read.ReadAt.IsZero() {
		return errors.New("job observations require a timestamped media server read")
	}
	if !read.OK {
		return nil
	}
	rows := []UpsertJobObservationParams{}
	seen := map[string]bool{}
	for _, sample := range rooms {
		if sample.OrganisationID != read.OrganisationID || sample.ProjectID != read.ProjectID || sample.Room.SID == "" || !sample.ReadAt.Equal(read.ReadAt) {
			return errors.New("job observation room does not match source scope and timestamp")
		}
		if sample.Room.Dispatches == nil {
			continue
		}
		for _, dispatch := range *sample.Room.Dispatches {
			for _, job := range dispatch.Jobs {
				if job.ID == "" || dispatch.ID == "" || seen[job.ID] {
					return errors.New("job observations require unique native job and dispatch IDs")
				}
				seen[job.ID] = true
				var reported, observed *time.Time
				if job.Status == "JS_FAILED" {
					observed = &read.ReadAt
					for _, status := range job.Statuses {
						if status.Status == "JS_FAILED" && status.At != nil && (reported == nil || status.At.Before(*reported)) {
							reported = status.At
						}
					}
				}
				rows = append(rows, UpsertJobObservationParams{
					OrganisationID: read.OrganisationID, ProjectID: read.ProjectID, JobID: job.ID,
					RoomSid: sample.Room.SID, DispatchID: dispatch.ID, AgentName: textOf(dispatch.AgentName),
					ParticipantIdentity: text(job.ParticipantIdentity), DispatchedAt: timestamp(dispatch.CreatedAt),
					Status: job.Status, ReportedFailedAt: timestamp(reported), FailureObservedAt: timestamp(observed), ObservedAt: timestamp(&read.ReadAt),
				})
			}
		}
	}
	sort.Slice(rows, func(i, j int) bool { return rows[i].JobID < rows[j].JobID })
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer func() { _ = tx.Rollback(ctx) }()
	q := s.q.WithTx(tx)
	for _, row := range rows {
		if _, err := q.UpsertJobObservation(ctx, row); err != nil {
			return fmt.Errorf("store job observation: %w", err)
		}
	}
	return tx.Commit(ctx)
}
