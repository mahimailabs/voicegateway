package chstore

import (
	"context"
	"strings"
	"testing"
	"time"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// Raw telemetry expires after 90 days: every raw table carries the TTL, and a merge removes an
// event received 91 days ago while keeping one received now.
func TestRawTelemetryExpiresAfter90Days(t *testing.T) {
	s := openMigrated(t)
	ctx := context.Background()
	for _, table := range []string{"events", "spans", "trace_calls", "metric_points", "unmatched"} {
		var ddl string
		if err := s.conn.QueryRow(ctx, "SELECT create_table_query FROM system.tables WHERE database = currentDatabase() AND name = ?", table).Scan(&ddl); err != nil {
			t.Fatalf("%s: %v", table, err)
		}
		if !strings.Contains(ddl, "toIntervalDay(90)") {
			t.Errorf("%s has no 90-day TTL:\n%s", table, ddl)
		}
	}

	old, current := export("old", nil), export("now", nil)
	old.ReceivedAt = time.Now().Add(-91 * 24 * time.Hour)
	if err := s.Insert(ctx, []store.Event{old, current}); err != nil {
		t.Fatal(err)
	}
	if got := storedEvents(ctx, t, s); len(got) != 2 {
		t.Fatalf("before the merge events holds %v, want both", got)
	}
	if err := s.conn.Exec(ctx, "OPTIMIZE TABLE events FINAL"); err != nil {
		t.Fatal(err)
	}
	if got := storedEvents(ctx, t, s); len(got) != 1 || got[0] != current.DedupeKey {
		t.Errorf("after the merge events holds %v, want [%s]", got, current.DedupeKey)
	}
}

func storedEvents(ctx context.Context, t *testing.T, s *Store) []string {
	t.Helper()
	rows, err := s.conn.Query(ctx, "SELECT dedupe_key FROM events ORDER BY dedupe_key")
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = rows.Close() }()
	var keys []string
	for rows.Next() {
		var k string
		if err := rows.Scan(&k); err != nil {
			t.Fatal(err)
		}
		keys = append(keys, k)
	}
	if err := rows.Err(); err != nil {
		t.Fatal(err)
	}
	return keys
}
