package pgstore

import (
	"context"
	"fmt"
	"math"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgtype"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// ListCalls reads one page of a project's calls, after the cursor when one is given, and, when
// withTotals is set, the totals over every matching call. Both reads run in one REPEATABLE READ
// READ ONLY transaction, so the page and its totals describe the same state.
func (s *Store) ListCalls(ctx context.Context, f store.CallFilter, after *store.CallCursor, limit int, withTotals bool, maxValues int) ([]store.CallListRow, *store.CallTotals, error) {
	tx, err := s.pool.BeginTx(ctx, pgx.TxOptions{IsoLevel: pgx.RepeatableRead, AccessMode: pgx.ReadOnly})
	if err != nil {
		return nil, nil, fmt.Errorf("list calls: %w", err)
	}
	defer func() { _ = tx.Rollback(ctx) }()
	q := s.q.WithTx(tx)

	p := ListCallsParams{
		MaxRows: int32(min(limit, math.MaxInt32)), SortBy: f.SortBy, Descending: f.Descending, //nolint:gosec // bounded above
		OrganisationID: f.OrganisationID, ProjectID: f.ProjectID, SnapshotAt: timestamp(&f.SnapshotAt),
		FromAt: timestamp(f.From), ToAt: timestamp(f.To), Room: text(f.Room), Channels: nonNilStrings(f.Channels),
		AnswerMin: float(f.AnswerMin), AnswerMax: float(f.AnswerMax), DurationMin: float(f.DurationMin), DurationMax: float(f.DurationMax),
		TurnsMin: integer(f.TurnsMin), TurnsMax: integer(f.TurnsMax),
		UnknownAnswer: f.UnknownAnswer, UnknownDuration: f.UnknownDuration, UnknownTurns: f.UnknownTurns, MatchNone: f.MatchNone,
	}
	if after != nil {
		id, err := uuid(after.ID)
		if err != nil {
			return nil, nil, err
		}
		p.AfterCursor, p.CursorUnknown, p.CursorValue, p.CursorID = true, after.Unknown, after.Value, id
	}
	rows, err := q.ListCalls(ctx, p)
	if err != nil {
		return nil, nil, fmt.Errorf("list calls: %w", err)
	}
	out := make([]store.CallListRow, 0, len(rows))
	for _, r := range rows {
		row, err := listRow(r)
		if err != nil {
			return nil, nil, err
		}
		out = append(out, row)
	}

	var totals *store.CallTotals
	if withTotals {
		t, err := q.CallTotals(ctx, CallTotalsParams{
			OrganisationID: p.OrganisationID, ProjectID: p.ProjectID, SnapshotAt: p.SnapshotAt,
			FromAt: p.FromAt, ToAt: p.ToAt, Room: p.Room, Channels: p.Channels,
			AnswerMin: p.AnswerMin, AnswerMax: p.AnswerMax, DurationMin: p.DurationMin, DurationMax: p.DurationMax,
			TurnsMin: p.TurnsMin, TurnsMax: p.TurnsMax,
			UnknownAnswer: p.UnknownAnswer, UnknownDuration: p.UnknownDuration, UnknownTurns: p.UnknownTurns, MatchNone: p.MatchNone,
			BudgetMs: f.BudgetMs, MaxValues: int32(min(maxValues, math.MaxInt32)), //nolint:gosec // bounded above
		})
		if err != nil {
			return nil, nil, fmt.Errorf("call totals: %w", err)
		}
		p50, err := optionalFloat(t.P50Ms)
		if err != nil {
			return nil, nil, fmt.Errorf("call totals p50: %w", err)
		}
		totals = &store.CallTotals{
			CallCount: t.CallCount, Phone: t.Phone, Web: t.Web, Unknown: t.UnknownChannel,
			AnswerTurns: t.AnswerTurns, OverBudget: t.OverBudget, P50Ms: p50,
			ValuesMs: t.ValuesMs, ValuesTruncated: t.AnswerTurns > int64(len(t.ValuesMs)),
		}
	}
	if err := tx.Commit(ctx); err != nil {
		return nil, nil, fmt.Errorf("list calls: %w", err)
	}
	return out, totals, nil
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
		ID: r.ID.String(), RoomName: r.RoomName.String, RoomSID: r.RoomSid.String, StartAt: r.StartAt.Time.UTC(),
		StartedAt: timeOf(r.StartedAt), EndedAt: timeOf(r.EndedAt), EndState: r.EndState, DurationSeconds: duration,
		Participants: r.Participants, Channel: r.Channel, AnswerMs: r.AnswerMs, Models: r.Models, Pipeline: r.Pipeline,
		Cursor: store.CallCursor{Unknown: unknown, Value: r.SortValue, ID: r.ID.String()},
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
