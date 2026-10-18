package chstore

import (
	"context"
	"testing"
	"time"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// CountUnlinkedTraces reads spans without FINAL: a span stored twice and not yet merged counts its
// trace once, a trace joined by a span's call id or by a trace link is never counted, and a second
// sweep records nothing new.
func TestCountUnlinkedTracesIgnoresDuplicateSpans(t *testing.T) {
	s := openMigrated(t)
	ctx := context.Background()
	if err := s.conn.Exec(ctx, "SYSTEM STOP MERGES spans"); err != nil {
		t.Fatal(err)
	}
	end := time.Now().UTC().Add(-time.Hour)
	span := func(trace, id, call string) store.Span {
		return store.Span{TraceID: trace, SpanID: id, CallID: call, Name: "agent_turn", Start: end.Add(-time.Minute), End: end}
	}
	const unlinked, byCallID, byLink = "0000000000000000000000000000000a", "0000000000000000000000000000000b", "0000000000000000000000000000000c"
	spans := []store.Span{
		span(unlinked, "000000000000000a", ""),
		span(byCallID, "000000000000000b", "11111111-1111-1111-1111-111111111111"),
		span(byLink, "000000000000000c", ""),
	}
	link := store.TraceCall{TraceID: byLink, CallID: "22222222-2222-2222-2222-222222222222"}
	// Two exports carry the same spans: two parts, each holding a copy of every span.
	for _, id := range []string{"1-0", "2-0"} {
		if err := s.Insert(ctx, []store.Event{export(id, spans, link)}); err != nil {
			t.Fatal(err)
		}
	}
	var copies uint64
	if err := s.conn.QueryRow(ctx, "SELECT count() FROM spans WHERE trace_id = '"+unlinked+"'").Scan(&copies); err != nil {
		t.Fatal(err)
	}
	if copies != 2 {
		t.Fatalf("spans holds %d copies of the unlinked span, want 2 unmerged", copies)
	}

	for i, want := range []uint64{1, 0} {
		got, err := s.CountUnlinkedTraces(ctx, time.Now().UTC())
		if err != nil {
			t.Fatal(err)
		}
		if got != want {
			t.Errorf("sweep %d recorded %d traces, want %d", i+1, got, want)
		}
	}
	rows, err := s.conn.Query(ctx, "SELECT key FROM unmatched FINAL WHERE kind = 'trace'")
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
	if len(keys) != 1 || keys[0] != unlinked {
		t.Errorf("unmatched traces = %v, want [%s]", keys, unlinked)
	}
}
