package pgstore

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"strings"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
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
		FirstSeenAt:    timestamp(c.FirstSeenAt),
	})
	if err != nil {
		return "", fmt.Errorf("upsert call: %w", badRow(err))
	}
	return id.String(), nil
}

// UpsertCallByJob opens a call for a job no source named a room SID for, or adds an activity time
// to it, and returns its id.
func (s *Store) UpsertCallByJob(ctx context.Context, organisationID, projectID, jobID string, activityAt, firstSeenAt *time.Time) (string, error) {
	id, err := s.q.UpsertCallByJobID(ctx, UpsertCallByJobIDParams{
		OrganisationID: organisationID, ProjectID: projectID, JobID: text(jobID), LastActivityAt: timestamp(activityAt),
		FirstSeenAt: timestamp(firstSeenAt),
	})
	if err != nil {
		return "", fmt.Errorf("upsert call by job id: %w", badRow(err))
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

// TouchCall adds a job id, a start, an activity time and a first-seen time to a call; empty values
// change nothing.
func (s *Store) TouchCall(ctx context.Context, callID, jobID string, startedAt, activityAt, firstSeenAt *time.Time) error {
	id, err := uuid(callID)
	if err != nil {
		return err
	}
	if err := s.q.TouchCall(ctx, TouchCallParams{ID: id, JobID: text(jobID), StartedAt: timestamp(startedAt), LastActivityAt: timestamp(activityAt), FirstSeenAt: timestamp(firstSeenAt)}); err != nil {
		return fmt.Errorf("touch call: %w", badRow(err))
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
		return fmt.Errorf("finish call: %w", badRow(err))
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
		return fmt.Errorf("upsert participant: %w", badRow(err))
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

// badRow wraps store.ErrBadRow around errors that blame the values, not the server: a data
// exception (class 22, such as a NUL byte or invalid UTF-8 in text) or a missing project (23503).
// Retrying those never succeeds.
func badRow(err error) error {
	var pgErr *pgconn.PgError
	if errors.As(err, &pgErr) && (strings.HasPrefix(pgErr.Code, "22") || pgErr.Code == foreignKeyViolation) {
		return fmt.Errorf("%w: %w", store.ErrBadRow, err)
	}
	return err
}

// foreignKeyViolation is the Postgres error code for a row naming a missing parent.
const foreignKeyViolation = "23503"

func found(id pgtype.UUID, err error, what string) (string, error) {
	if errors.Is(err, pgx.ErrNoRows) {
		return "", store.ErrNotFound
	}
	if err != nil {
		return "", fmt.Errorf("%s: %w", what, badRow(err))
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

// Call returns one call of a project, or store.ErrNotFound, including when the id is not a UUID.
func (s *Store) Call(ctx context.Context, organisationID, projectID, callID string) (store.Call, error) {
	id, err := uuid(callID)
	if err != nil {
		return store.Call{}, store.ErrNotFound
	}
	row, err := s.q.CallByID(ctx, CallByIDParams{OrganisationID: organisationID, ProjectID: projectID, ID: id})
	if errors.Is(err, pgx.ErrNoRows) {
		return store.Call{}, store.ErrNotFound
	}
	if err != nil {
		return store.Call{}, fmt.Errorf("call %s: %w", callID, err)
	}
	c := store.Call{
		Metadata: store.CallMetadata{Tags: row.Tags, Archived: row.Archived, Revision: row.MetadataRevision},
		Fields:   callFields(row.Fields),
		ID:       row.ID.String(), RoomName: row.RoomName.String, RoomSID: row.RoomSid.String, JobID: row.JobID.String,
		StartedAt: timeOf(row.StartedAt), EndedAt: timeOf(row.EndedAt), FirstSeenAt: timeOf(row.FirstSeenAt),
		EndState: row.EndState, Participants: row.Participants, Servable: row.Servable.Bool, Summarized: row.Summarized,
	}
	if row.RecordingID.Valid {
		c.Recording = &store.Recording{ID: row.RecordingID.String(), StartedAt: timeOf(row.RecordingStartedAt)}
	}
	return c, nil
}

func timeOf(t pgtype.Timestamptz) *time.Time {
	if !t.Valid {
		return nil
	}
	v := t.Time.UTC()
	return &v
}

// StaleCalls returns up to limit calls whose summary needs rebuilding, oldest mark first.
func (s *Store) StaleCalls(ctx context.Context, limit int) ([]store.StaleCall, error) {
	rows, err := s.q.StaleCalls(ctx, int32(min(limit, math.MaxInt32))) //nolint:gosec // bounded above
	if err != nil {
		return nil, fmt.Errorf("stale calls: %w", err)
	}
	out := make([]store.StaleCall, 0, len(rows))
	for _, r := range rows {
		out = append(out, store.StaleCall{OrganisationID: r.OrganisationID, ProjectID: r.ProjectID, ID: r.ID.String(), StaleAt: r.SummaryStaleAt.Time})
	}
	return out, nil
}

// PutSummary stores a call's summary, then clears its stale mark only if it is still the mark the
// rebuild read, so a join during the rebuild leaves the call stale for the next sweep.
func (s *Store) PutSummary(ctx context.Context, c store.StaleCall, sum store.CallSummary, computedAt time.Time) error {
	id, err := uuid(c.ID)
	if err != nil {
		return err
	}
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer func() { _ = tx.Rollback(ctx) }()
	q := s.q.WithTx(tx)
	var turns pgtype.Int4
	if sum.TurnCount != nil {
		turns = pgtype.Int4{Int32: int32(min(*sum.TurnCount, math.MaxInt32)), Valid: true} //nolint:gosec // bounded above
	}
	var median pgtype.Float8
	if sum.AnswerMsMedian != nil {
		median = pgtype.Float8{Float64: *sum.AnswerMsMedian, Valid: true}
	}
	err = q.PutSummary(ctx, PutSummaryParams{
		CallID: id, TurnCount: turns, AnswerMs: sum.AnswerMs, AnswerMsMedian: median,
		Models: noNUL(sum.Models), Pipeline: sum.Pipeline, AgentKey: text(stripNUL(deref(sum.AgentKey))), ComputedAt: timestamp(&computedAt),
	})
	if err != nil {
		return fmt.Errorf("put summary: %w", badRow(err))
	}
	if err := q.HealCall(ctx, HealCallParams{ID: id, RoomName: text(stripNUL(sum.RoomName)), FirstSeenAt: timestamp(sum.FirstSeenAt)}); err != nil {
		return fmt.Errorf("heal call: %w", badRow(err))
	}
	if err := q.ClearStale(ctx, ClearStaleParams{ID: id, Seen: timestamp(&c.StaleAt)}); err != nil {
		return fmt.Errorf("clear stale: %w", err)
	}
	if err := applyCallFindings(ctx, q, c.OrganisationID, c.ProjectID, c.ID, sum.Findings, sum.EvaluatedFindingIDs); err != nil {
		return fmt.Errorf("put call findings: %w", err)
	}
	if err := q.RecordSummaryFindings(ctx, RecordSummaryFindingsParams{ID: id, EvaluatedAt: timestamp(&computedAt), SettingsRevision: sum.FindingSettingsRevision, SourceAvailable: sum.FindingSourceAvailable}); err != nil {
		return err
	}
	return tx.Commit(ctx)
}

func deref(s *string) string {
	if s == nil {
		return ""
	}
	return *s
}

// MarkStale marks calls whose summary must be rebuilt.
func (s *Store) MarkStale(ctx context.Context, callIDs []string) error {
	ids := make([]pgtype.UUID, 0, len(callIDs))
	for _, c := range callIDs {
		id, err := uuid(c)
		if err != nil {
			return err
		}
		ids = append(ids, id)
	}
	if len(ids) == 0 {
		return nil
	}
	if err := s.q.MarkStale(ctx, ids); err != nil {
		return fmt.Errorf("mark stale: %w", err)
	}
	return nil
}

// DeferStale moves a call whose rebuild failed to the back of the stale queue.
func (s *Store) DeferStale(ctx context.Context, callID string) error {
	id, err := uuid(callID)
	if err != nil {
		return err
	}
	if err := s.q.DeferStale(ctx, id); err != nil {
		return fmt.Errorf("defer stale: %w", err)
	}
	return nil
}

// stripNUL removes NUL characters, which Postgres text cannot hold (SQLSTATE 22021) and an agent's
// span attributes can carry.
func stripNUL(s string) string { return strings.ReplaceAll(s, "\x00", "") }

func noNUL(ss []string) []string {
	out := make([]string, 0, len(ss))
	for _, s := range ss {
		out = append(out, stripNUL(s))
	}
	return out
}

// MergeCallFields adds an SDK's custom fields to a call; a key it already has takes the new value.
func (s *Store) MergeCallFields(ctx context.Context, callID string, fields map[string]string) error {
	id, err := uuid(callID)
	if err != nil {
		return err
	}
	b, err := json.Marshal(noNULMap(fields))
	if err != nil {
		return err
	}
	if err := s.q.MergeCallFields(ctx, MergeCallFieldsParams{ID: id, Fields: b}); err != nil {
		return fmt.Errorf("merge call fields: %w", badRow(err))
	}
	return nil
}

// noNULMap strips NUL from keys and values, which Postgres text and jsonb refuse.
func noNULMap(m map[string]string) map[string]string {
	out := make(map[string]string, len(m))
	for k, v := range m {
		out[stripNUL(k)] = stripNUL(v)
	}
	return out
}

// callFields decodes a call's SDK fields; a value that is not a string object reads as none.
func callFields(b []byte) map[string]string {
	m := map[string]string{}
	_ = json.Unmarshal(b, &m)
	return m
}
