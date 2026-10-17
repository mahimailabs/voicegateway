package pgstore

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgtype"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// UpsertCall opens the call of a room instance, or adds what the source reported to it, and
// returns its id.
func (s *Store) UpsertCall(ctx context.Context, c store.CallUpsert) (string, error) {
	id, err := s.q.UpsertCallByRoomSID(ctx, UpsertCallByRoomSIDParams{
		OrganisationID: c.OrganisationID,
		ProjectID:      c.ProjectID,
		RoomSid:        text(c.RoomSID),
		RoomName:       text(c.RoomName),
		JobID:          text(c.JobID),
		StartedAt:      timestamp(c.StartedAt),
		LastActivityAt: timestamp(c.LastActivityAt),
	})
	if err != nil {
		return "", fmt.Errorf("upsert call: %w", err)
	}
	return id.String(), nil
}

// UpsertCallByJob opens a call for a job no source named a room SID for, or adds an activity time
// to it, and returns its id.
func (s *Store) UpsertCallByJob(ctx context.Context, organisationID, projectID, jobID string, activityAt *time.Time) (string, error) {
	id, err := s.q.UpsertCallByJobID(ctx, UpsertCallByJobIDParams{
		OrganisationID: organisationID, ProjectID: projectID, JobID: text(jobID), LastActivityAt: timestamp(activityAt),
	})
	if err != nil {
		return "", fmt.Errorf("upsert call by job id: %w", err)
	}
	return id.String(), nil
}

// CallByRoom returns the call of a room SID, or store.ErrNotFound.
func (s *Store) CallByRoom(ctx context.Context, organisationID, projectID, roomSID string) (store.CallRef, error) {
	row, err := s.q.CallByRoomSID(ctx, CallByRoomSIDParams{OrganisationID: organisationID, ProjectID: projectID, RoomSid: text(roomSID)})
	return ref(row.ID, row.EndedAt, err, "call by room SID")
}

// CallByJob returns the latest call of a job id, or store.ErrNotFound.
func (s *Store) CallByJob(ctx context.Context, organisationID, projectID, jobID string) (store.CallRef, error) {
	row, err := s.q.CallByJobID(ctx, CallByJobIDParams{OrganisationID: organisationID, ProjectID: projectID, JobID: text(jobID)})
	return ref(row.ID, row.EndedAt, err, "call by job id")
}

// CallInRoom returns the call open in a named room at a reported time, or store.ErrNotFound.
func (s *Store) CallInRoom(ctx context.Context, organisationID, projectID, roomName string, at time.Time) (string, error) {
	id, err := s.q.OpenCallInRoom(ctx, OpenCallInRoomParams{
		OrganisationID: organisationID, ProjectID: projectID, RoomName: text(roomName), At: timestamp(&at),
	})
	return found(id, err, "call in room")
}

// TouchCall adds a job id, a start and an activity time to a call; empty values change nothing.
func (s *Store) TouchCall(ctx context.Context, callID, jobID string, startedAt, activityAt *time.Time) error {
	id, err := uuid(callID)
	if err != nil {
		return err
	}
	if err := s.q.TouchCall(ctx, TouchCallParams{ID: id, JobID: text(jobID), StartedAt: timestamp(startedAt), LastActivityAt: timestamp(activityAt)}); err != nil {
		return fmt.Errorf("touch call: %w", err)
	}
	return nil
}

// FinishCall marks a call observed, ending at the reported time when there is one.
func (s *Store) FinishCall(ctx context.Context, callID string, endedAt *time.Time) error {
	id, err := uuid(callID)
	if err != nil {
		return err
	}
	if err := s.q.FinishCall(ctx, FinishCallParams{ID: id, EndedAt: timestamp(endedAt)}); err != nil {
		return fmt.Errorf("finish call: %w", err)
	}
	return nil
}

// UpsertParticipant stores a participant of a call; a time already set is kept.
func (s *Store) UpsertParticipant(ctx context.Context, p store.Participant) error {
	id, err := uuid(p.CallID)
	if err != nil {
		return err
	}
	err = s.q.UpsertParticipant(ctx, UpsertParticipantParams{
		CallID: id, ParticipantSid: p.SID, Identity: p.Identity, Kind: p.Kind,
		JoinedAt: timestamp(p.JoinedAt), LeftAt: timestamp(p.LeftAt),
	})
	if err != nil {
		return fmt.Errorf("upsert participant: %w", err)
	}
	return nil
}

// CloseIdleCalls closes, as inferred, every open call nothing has joined since idleSince, and
// returns how many.
func (s *Store) CloseIdleCalls(ctx context.Context, idleSince time.Time) (int64, error) {
	n, err := s.q.CloseIdleCalls(ctx, timestamp(&idleSince))
	if err != nil {
		return 0, fmt.Errorf("close idle calls: %w", err)
	}
	return n, nil
}

func ref(id pgtype.UUID, endedAt pgtype.Timestamptz, err error, what string) (store.CallRef, error) {
	s, err := found(id, err, what)
	if err != nil {
		return store.CallRef{}, err
	}
	r := store.CallRef{ID: s}
	if endedAt.Valid {
		t := endedAt.Time
		r.EndedAt = &t
	}
	return r, nil
}

func found(id pgtype.UUID, err error, what string) (string, error) {
	if errors.Is(err, pgx.ErrNoRows) {
		return "", store.ErrNotFound
	}
	if err != nil {
		return "", fmt.Errorf("%s: %w", what, err)
	}
	return id.String(), nil
}

func text(s string) pgtype.Text { return pgtype.Text{String: s, Valid: s != ""} }

func timestamp(t *time.Time) pgtype.Timestamptz {
	if t == nil {
		return pgtype.Timestamptz{}
	}
	return pgtype.Timestamptz{Time: *t, Valid: true}
}

func uuid(s string) (pgtype.UUID, error) {
	var id pgtype.UUID
	if err := id.Scan(s); err != nil {
		return id, fmt.Errorf("call id %q: %w", s, err)
	}
	return id, nil
}
