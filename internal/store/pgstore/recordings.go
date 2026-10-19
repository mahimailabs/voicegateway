package pgstore

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/jackc/pgx/v5"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// SetProjectRecordings turns a project's recordings on or off (phase 07a), or returns
// store.ErrNotFound.
func (s *Store) SetProjectRecordings(ctx context.Context, organisationID, projectID string, on bool) error {
	n, err := s.q.SetProjectRecordings(ctx, SetProjectRecordingsParams{Recordings: on, OrganisationID: organisationID, ID: projectID})
	if err != nil {
		return fmt.Errorf("project %s/%s recordings: %w", organisationID, projectID, err)
	}
	if n == 0 {
		return store.ErrNotFound
	}
	return nil
}

// LinkRecording links a stored recording to its call. Linking the same object twice adds nothing.
func (s *Store) LinkRecording(ctx context.Context, l store.RecordingLink) error {
	id, err := uuid(l.CallID)
	if err != nil {
		return err
	}
	if err := s.q.InsertCallRecording(ctx, InsertCallRecordingParams{
		CallID: id, ObjectKey: l.ObjectKey, SizeBytes: l.SizeBytes,
		StartedAt: timestamp(l.StartedAt), StoredAt: timestamp(&l.StoredAt),
	}); err != nil {
		return fmt.Errorf("link recording to call %s: %w", l.CallID, err)
	}
	return nil
}

// Recording returns one recording of a project, or store.ErrNotFound, including when the id is not
// a UUID or belongs to another project's call.
func (s *Store) Recording(ctx context.Context, organisationID, projectID, recordingID string) (store.Recording, error) {
	id, err := uuid(recordingID)
	if err != nil {
		return store.Recording{}, store.ErrNotFound
	}
	row, err := s.q.RecordingByID(ctx, RecordingByIDParams{ID: id, OrganisationID: organisationID, ProjectID: projectID})
	if errors.Is(err, pgx.ErrNoRows) {
		return store.Recording{}, store.ErrNotFound
	}
	if err != nil {
		return store.Recording{}, fmt.Errorf("recording %s: %w", recordingID, err)
	}
	return store.Recording{
		ID: row.ID.String(), ObjectKey: row.ObjectKey, SizeBytes: row.SizeBytes,
		StartedAt: timeOf(row.StartedAt), StoredAt: row.StoredAt.Time.UTC(),
	}, nil
}

// ForgetRecordings deletes the links of recordings stored before cutoff and returns how many.
func (s *Store) ForgetRecordings(ctx context.Context, cutoff time.Time) (int64, error) {
	n, err := s.q.DeleteExpiredRecordings(ctx, timestamp(&cutoff))
	if err != nil {
		return 0, fmt.Errorf("forget recordings: %w", err)
	}
	return n, nil
}
