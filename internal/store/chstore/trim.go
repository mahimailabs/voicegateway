package chstore

import (
	"context"
	"fmt"
	"time"
)

// trimmed names each table holding an organisation's call data and the time its rows are aged by:
// the raw telemetry's TTL times (ClickHouse 00010) and the room and participant reads.
var trimmed = []struct{ table, at string }{
	{"events", "received_at"},
	{"spans", "ifNull(start_time, inserted_at)"},
	{"metric_points", "end_time"},
	{"trace_calls", "inserted_at"},
	{"unmatched", "inserted_at"},
	{"room_samples", "read_at"},
	{"participant_samples", "read_at"},
}

// TrimTelemetry deletes an organisation's call data older than cutoff, for a plan that keeps it
// for fewer days than the tables' own TTL (decision 0042). The deletes are lightweight: rows
// disappear from reads at once and from disk at the next merge. Safe to repeat.
func (s *Store) TrimTelemetry(ctx context.Context, organisationID string, cutoff time.Time) error {
	for _, t := range trimmed {
		// Table and column names are this package's constants; the values are bound.
		q := fmt.Sprintf("DELETE FROM %s WHERE organisation_id = ? AND %s < ?", t.table, t.at) // #nosec G201
		if err := s.conn.Exec(ctx, q, organisationID, cutoff.UTC()); err != nil {
			return fmt.Errorf("trim %s of %s: %w", t.table, organisationID, err)
		}
	}
	return nil
}
