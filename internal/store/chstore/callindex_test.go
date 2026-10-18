package chstore

import (
	"context"
	"fmt"
	"regexp"
	"strconv"
	"testing"
	"time"

	"github.com/ClickHouse/clickhouse-go/v2"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// primaryKey matches EXPLAIN indexes = 1's primary key section: the granules it kept of those given.
var primaryKey = regexp.MustCompile(`(?s)PrimaryKey\n.*?Granules: (\d+)/(\d+)`)

// A call's spans are read through the call_id index and the sorting key, not by scanning the
// project: on a project of many calls the read keeps a few granules, and still returns the spans
// joined only through their trace's link.
func TestCallSpansSkipsOtherCalls(t *testing.T) {
	s := openMigrated(t)
	ctx := context.Background()
	const calls, perCall = 300, 100
	var spans []store.Span
	var links []store.TraceCall
	start := time.Date(2026, 10, 18, 0, 0, 0, 0, time.UTC)
	for c := range calls {
		call := fmt.Sprintf("%08d-0000-0000-0000-000000000000", c)
		trace := fmt.Sprintf("%032x", c+1)
		links = append(links, store.TraceCall{TraceID: trace, CallID: call})
		for i := range perCall {
			sp := store.Span{
				TraceID: trace, SpanID: fmt.Sprintf("%016x", c*perCall+i+1), Name: "agent_turn",
				Start: start.Add(time.Duration(c*perCall+i) * time.Second), End: start.Add(time.Duration(c*perCall+i+1) * time.Second),
			}
			// Every tenth span carries its call id; the rest join through the trace link.
			if i%10 == 0 {
				sp.CallID = call
			}
			spans = append(spans, sp)
		}
	}
	if err := s.Insert(ctx, []store.Event{export("1-0", spans, links...)}); err != nil {
		t.Fatal(err)
	}
	if err := s.conn.Exec(ctx, "OPTIMIZE TABLE spans FINAL"); err != nil {
		t.Fatal(err)
	}

	const call = "00000150-0000-0000-0000-000000000000"
	got, err := s.CallSpans(ctx, "default", "production", call)
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != perCall {
		t.Fatalf("CallSpans = %d spans, want %d", len(got), perCall)
	}

	rows, err := s.conn.Query(ctx, "EXPLAIN indexes = 1 "+callSpans,
		clickhouse.Named("org", "default"), clickhouse.Named("project", "production"), clickhouse.Named("call", call))
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = rows.Close() }()
	var plan string
	for rows.Next() {
		var line string
		if err := rows.Scan(&line); err != nil {
			t.Fatal(err)
		}
		plan += line + "\n"
	}
	// The plan's first read is the outer one of spans; its sorting key must exclude other calls.
	m := primaryKey.FindStringSubmatch(plan)
	if m == nil {
		t.Fatalf("plan has no primary key condition:\n%s", plan)
	}
	if kept, _ := strconv.Atoi(m[1]); kept >= mustAtoi(t, m[2]) {
		t.Errorf("the read keeps %s of %s granules, want fewer:\n%s", m[1], m[2], plan)
	}
}

func mustAtoi(t *testing.T, s string) int {
	t.Helper()
	n, err := strconv.Atoi(s)
	if err != nil {
		t.Fatal(err)
	}
	return n
}
