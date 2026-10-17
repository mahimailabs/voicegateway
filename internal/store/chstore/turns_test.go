package chstore

import (
	"bufio"
	"context"
	"encoding/json"
	"os"
	"reflect"
	"slices"
	"testing"
	"time"

	"github.com/mahimailabs/voicegateway/internal/event"
	"github.com/mahimailabs/voicegateway/internal/store"
	"github.com/mahimailabs/voicegateway/internal/turn"
)

// realCallSpans reads the 2026-10-17 LiveKit Agents 1.8.7 call that internal/turn tests against.
func realCallSpans(t *testing.T) ([]store.Span, []byte) {
	t.Helper()
	f, err := os.Open("../../turn/testdata/agents-1.8.7-call.spans.jsonl")
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = f.Close() }()
	var spans []store.Span
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 1<<20), 1<<24)
	for sc.Scan() {
		var row struct {
			TraceID    string            `json:"trace_id"`
			SpanID     string            `json:"span_id"`
			ParentID   string            `json:"parent_span_id"`
			Name       string            `json:"name"`
			Kind       string            `json:"kind"`
			Start      time.Time         `json:"start"`
			End        time.Time         `json:"end"`
			Status     string            `json:"status_code"`
			RoomID     string            `json:"room_id"`
			JobID      string            `json:"job_id"`
			Scope      string            `json:"scope_name"`
			Attributes map[string]string `json:"attributes"`
		}
		if err := json.Unmarshal(sc.Bytes(), &row); err != nil {
			t.Fatal(err)
		}
		spans = append(spans, store.Span{
			TraceID: row.TraceID, SpanID: row.SpanID, ParentSpanID: row.ParentID, Name: row.Name, Kind: row.Kind,
			Start: row.Start, End: row.End, StatusCode: row.Status, RoomID: row.RoomID, JobID: row.JobID, ScopeName: row.Scope,
			Attributes: row.Attributes,
		})
	}
	if err := sc.Err(); err != nil {
		t.Fatal(err)
	}
	report, err := os.ReadFile("../../turn/testdata/agents-1.8.7-call.session_report.json")
	if err != nil {
		t.Fatal(err)
	}
	return spans, report
}

func openMigrated(t *testing.T) *Store {
	t.Helper()
	url := startClickHouse(t)
	if _, err := Migrate(context.Background(), url); err != nil {
		t.Fatal(err)
	}
	s, err := Open(url)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.Close() })
	return s
}

func export(id string, spans []store.Span, links ...store.TraceCall) store.Event {
	return store.Event{
		OrganisationID: "default", ProjectID: "production", Kind: string(event.KindAgentTraces),
		DedupeKey: "entry:" + id, StreamID: id, ReceivedAt: time.Now(), Payload: []byte("otlp"), Spans: spans, TraceCalls: links,
	}
}

// Acceptance check 7 of phase 04b.
func TestCallTurnsReadsStoredSpans(t *testing.T) {
	s := openMigrated(t)
	ctx := context.Background()
	spans, report := realCallSpans(t)
	const call, other = "11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222"
	for i := range spans {
		spans[i].CallID = call
	}
	// One span joined only through its trace's link, as a span with no ids of its own would.
	unlinked := slices.IndexFunc(spans, func(sp store.Span) bool { return sp.Name == "tts_node" })
	spans[unlinked].CallID = ""

	third := len(spans) / 3
	exports := []store.Event{
		export("3-0", spans[2*third:], store.TraceCall{TraceID: spans[0].TraceID, CallID: call}),
		export("1-0", spans[:third]),
		export("2-0", spans[third:2*third]),
	}
	// Another call in a room of the same name: its turn must not appear.
	otherTurn := store.Span{
		TraceID: "ffffffffffffffffffffffffffffffff", SpanID: "ffffffffffffffff", CallID: other, Name: "agent_turn",
		Start: spans[0].Start, End: spans[0].End, Attributes: map[string]string{"lk.e2e_latency": "9"},
	}
	exports = append(exports, export("4-0", []store.Span{otherTurn}))
	reportRow := store.Event{
		OrganisationID: "default", ProjectID: "production", Kind: string(event.KindAgentSessionReport), CallID: call,
		DedupeKey: "AJ_1", StreamID: "5-0", ReceivedAt: time.Now(), Payload: report,
	}
	for _, e := range append(exports, reportRow) {
		if err := s.Insert(ctx, []store.Event{e}); err != nil {
			t.Fatal(err)
		}
	}

	got, err := s.CallTurns(ctx, "default", "production", call)
	if err != nil {
		t.Fatal(err)
	}
	var sr event.SessionReport
	if err := json.Unmarshal(report, &sr); err != nil {
		t.Fatal(err)
	}
	want := turn.Build(spans, []byte(sr.ChatHistory))
	if len(got.Turns) != 3 || !reflect.DeepEqual(got, want) {
		t.Fatalf("CallTurns = %d turns, want the %d turns turn.Build makes from every span at once\ngot  %+v\nwant %+v", len(got.Turns), len(want.Turns), got.Usage, want.Usage)
	}
	if got.Turns[0].AgentText == nil {
		t.Error("turns carry no text from the stored session report")
	}
}

// Acceptance check 8 of phase 04b.
func TestCallWithoutSpansHasNoTurns(t *testing.T) {
	s := openMigrated(t)
	ctx := context.Background()
	const call = "33333333-3333-3333-3333-333333333333"
	if err := s.Insert(ctx, []store.Event{{
		OrganisationID: "default", ProjectID: "production", Kind: string(event.KindLiveKitWebhook), CallID: call,
		DedupeKey: "EV_1", EventID: "EV_1", StreamID: "1-0", ReceivedAt: time.Now(), Payload: []byte(`{"event":"room_started"}`),
	}}); err != nil {
		t.Fatal(err)
	}
	got, err := s.CallTurns(ctx, "default", "production", call)
	if err != nil {
		t.Fatal(err)
	}
	// The empty id unjoined rows carry names no call.
	if none, err := s.CallTurns(ctx, "default", "production", ""); err != nil || len(none.Turns) != 0 {
		t.Errorf("CallTurns(\"\") = %d turns, %v; want none", len(none.Turns), err)
	}
	u := got.Usage
	if len(got.Turns) != 0 || u.InputTokens.Sum != nil || u.OutputTokens.Sum != nil || u.CachedTokens.Sum != nil ||
		u.TTSCharacters.Sum != nil || u.TTSAudioSeconds.Sum != nil || u.STTAudioSeconds.Sum != nil {
		t.Fatalf("CallTurns = %+v, want no turns and every total null", got)
	}
}
