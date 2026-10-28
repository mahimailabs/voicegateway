package pgstore

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgtype"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// ListCalls reads one page of a project's calls, after the cursor when one is given, and, when
// withTotals is set, the totals over every matching call. Both reads run in one REPEATABLE READ
// READ ONLY transaction, so the page and its totals describe the same state. A zero
// f.SnapshotAt takes the snapshot time from the Postgres clock, the clock that writes created_at,
// and returns it.
func (s *Store) ListCalls(ctx context.Context, f store.CallFilter, after *store.CallCursor, limit int, withTotals bool, maxValues int) ([]store.CallListRow, *store.CallTotals, time.Time, error) {
	tx, err := s.pool.BeginTx(ctx, pgx.TxOptions{IsoLevel: pgx.RepeatableRead, AccessMode: pgx.ReadOnly})
	if err != nil {
		return nil, nil, time.Time{}, fmt.Errorf("list calls: %w", err)
	}
	defer func() { _ = tx.Rollback(ctx) }()
	q := s.q.WithTx(tx)
	if f.MetadataRevision != nil {
		revision, err := q.CallMetadataRevision(ctx, CallMetadataRevisionParams{OrganisationID: f.OrganisationID, ProjectID: f.ProjectID})
		if err != nil {
			return nil, nil, time.Time{}, err
		}
		if revision != *f.MetadataRevision {
			return nil, nil, time.Time{}, store.ErrMetadataSnapshot
		}
	}
	if f.SnapshotAt.IsZero() {
		now, err := q.TransactionTime(ctx)
		if err != nil {
			return nil, nil, time.Time{}, fmt.Errorf("list calls: %w", err)
		}
		f.SnapshotAt = now.Time.UTC()
	}

	fields, err := json.Marshal(noNULMap(f.Fields))
	if err != nil {
		return nil, nil, time.Time{}, fmt.Errorf("list calls: %w", err)
	}
	p := ListCallsParams{
		Tags: nonNilStrings(f.Tags), Fields: fields, Archived: optionalBool(f.Archived),
		MaxRows: int32(min(limit, math.MaxInt32)), SortBy: f.SortBy, Descending: f.Descending, //nolint:gosec // bounded above
		OrganisationID: f.OrganisationID, ProjectID: f.ProjectID, SnapshotAt: timestamp(&f.SnapshotAt),
		FromAt: timestamp(f.From), ToAt: timestamp(f.To), Room: text(f.Room), Channels: nonNilStrings(f.Channels),
		AnswerMin: float(f.AnswerMin), AnswerMax: float(f.AnswerMax), DurationMin: float(f.DurationMin), DurationMax: float(f.DurationMax),
		TurnsMin: integer(f.TurnsMin), TurnsMax: integer(f.TurnsMax),
		UnknownAnswer: f.UnknownAnswer, UnknownDuration: f.UnknownDuration, UnknownTurns: f.UnknownTurns, MatchNone: f.MatchNone,
		Recorded: boolean(f.Recorded),
	}
	if after != nil {
		id, err := uuid(after.ID)
		if err != nil {
			return nil, nil, time.Time{}, err
		}
		p.AfterCursor, p.CursorUnknown, p.CursorValue, p.CursorID = true, after.Unknown, after.Value, id
	}
	rows, err := q.ListCalls(ctx, p)
	if err != nil {
		return nil, nil, time.Time{}, fmt.Errorf("list calls: %w", err)
	}
	out := make([]store.CallListRow, 0, len(rows))
	for _, r := range rows {
		row, err := listRow(r)
		if err != nil {
			return nil, nil, time.Time{}, err
		}
		out = append(out, row)
	}

	var totals *store.CallTotals
	if withTotals {
		t, err := q.CallTotals(ctx, CallTotalsParams{
			OrganisationID: p.OrganisationID, ProjectID: p.ProjectID, SnapshotAt: p.SnapshotAt, Tags: p.Tags, Fields: p.Fields, Archived: p.Archived,
			FromAt: p.FromAt, ToAt: p.ToAt, Room: p.Room, Channels: p.Channels,
			AnswerMin: p.AnswerMin, AnswerMax: p.AnswerMax, DurationMin: p.DurationMin, DurationMax: p.DurationMax,
			TurnsMin: p.TurnsMin, TurnsMax: p.TurnsMax,
			UnknownAnswer: p.UnknownAnswer, UnknownDuration: p.UnknownDuration, UnknownTurns: p.UnknownTurns, MatchNone: p.MatchNone,
			Recorded: p.Recorded,
			BudgetMs: f.BudgetMs, MaxValues: int32(min(maxValues, math.MaxInt32)), //nolint:gosec // bounded above
		})
		if err != nil {
			return nil, nil, time.Time{}, fmt.Errorf("call totals: %w", err)
		}
		p50, err := optionalFloat(t.P50Ms)
		if err != nil {
			return nil, nil, time.Time{}, fmt.Errorf("call totals p50: %w", err)
		}
		totals = &store.CallTotals{
			CallCount: t.CallCount, Phone: t.Phone, Web: t.Web, Unknown: t.UnknownChannel, Recorded: t.Recorded,
			AnswerTurns: t.AnswerTurns, OverBudget: t.OverBudget, P50Ms: p50,
			ValuesMs: t.ValuesMs, ValuesTruncated: t.AnswerTurns > int64(len(t.ValuesMs)),
		}
	}
	if err := tx.Commit(ctx); err != nil {
		return nil, nil, time.Time{}, fmt.Errorf("list calls: %w", err)
	}
	return out, totals, f.SnapshotAt, nil
}

func listRow(r ListCallsRow) (store.CallListRow, error) {
	duration, err := optionalFloat(r.DurationSeconds)
	if err != nil {
		return store.CallListRow{}, fmt.Errorf("call %s duration: %w", r.ID.String(), err)
	}
	unknown, ok := r.KeyUnknown.(bool)
	if !ok {
		return store.CallListRow{}, fmt.Errorf("call %s sort key: unexpected %T", r.ID.String(), r.KeyUnknown)
	}
	row := store.CallListRow{
		Metadata: store.CallMetadata{Tags: r.Tags, Archived: r.Archived, Revision: r.MetadataRevision},
		ID:       r.ID.String(), RoomName: r.RoomName.String, RoomSID: r.RoomSid.String, StartAt: r.StartAt.Time.UTC(),
		StartedAt: timeOf(r.StartedAt), EndedAt: timeOf(r.EndedAt), EndState: r.EndState, DurationSeconds: duration,
		Participants: r.Participants, Channel: r.Channel, AnswerMs: r.AnswerMs, Models: r.Models, Pipeline: r.Pipeline,
		Recorded: r.Recorded,
		Cursor:   store.CallCursor{Unknown: unknown, Value: r.SortValue, ID: r.ID.String()},
	}
	if r.TurnCount.Valid {
		n := int(r.TurnCount.Int32)
		row.TurnCount = &n
	}
	if r.AnswerMsMedian.Valid {
		m := r.AnswerMsMedian.Float64
		row.AnswerMsMedian = &m
	}
	if r.AgentKey.Valid {
		a := r.AgentKey.String
		row.AgentKey = &a
	}
	return row, nil
}

// optionalFloat reads a value sqlc could not type: nil, or a float.
func optionalFloat(v any) (*float64, error) {
	switch x := v.(type) {
	case nil:
		return nil, nil
	case float64:
		return &x, nil
	case pgtype.Numeric:
		f, err := x.Float64Value()
		if err != nil || !f.Valid {
			return nil, err
		}
		return &f.Float64, nil
	}
	return nil, fmt.Errorf("unexpected %T", v)
}

func float(v *float64) pgtype.Float8 {
	if v == nil {
		return pgtype.Float8{}
	}
	return pgtype.Float8{Float64: *v, Valid: true}
}

func boolean(v *bool) pgtype.Bool {
	if v == nil {
		return pgtype.Bool{}
	}
	return pgtype.Bool{Bool: *v, Valid: true}
}

func integer(v *int) pgtype.Int4 {
	if v == nil {
		return pgtype.Int4{}
	}
	return pgtype.Int4{Int32: int32(min(max(*v, math.MinInt32), math.MaxInt32)), Valid: true} //nolint:gosec // bounded above
}

func nonNilStrings(v []string) []string {
	if v == nil {
		return []string{}
	}
	return v
}

// PutListSnapshot stores a listCalls snapshot, deleting those older than a day.
func (s *Store) PutListSnapshot(ctx context.Context, snap store.ListSnapshot) error {
	if err := s.q.ForgetListSnapshots(ctx); err != nil {
		return fmt.Errorf("forget list snapshots: %w", err)
	}
	err := s.q.PutListSnapshot(ctx, PutListSnapshotParams{
		ID: snap.ID, OrganisationID: snap.OrganisationID, ProjectID: snap.ProjectID, Filters: snap.Filters,
		At: timestamp(&snap.At), Totals: snap.Totals,
	})
	if err != nil {
		return fmt.Errorf("put list snapshot: %w", err)
	}
	return nil
}

// ListSnapshot returns a stored listCalls snapshot, or store.ErrNotFound.
func (s *Store) ListSnapshot(ctx context.Context, id string) (store.ListSnapshot, error) {
	row, err := s.q.ListSnapshot(ctx, id)
	if errors.Is(err, pgx.ErrNoRows) {
		return store.ListSnapshot{}, store.ErrNotFound
	}
	if err != nil {
		return store.ListSnapshot{}, fmt.Errorf("list snapshot: %w", err)
	}
	return store.ListSnapshot{ID: id, OrganisationID: row.OrganisationID, ProjectID: row.ProjectID, Filters: row.Filters, At: row.At.Time.UTC(), Totals: row.Totals}, nil
}

func optionalBool(v *bool) pgtype.Bool {
	if v == nil {
		return pgtype.Bool{}
	}
	return pgtype.Bool{Bool: *v, Valid: true}
}
