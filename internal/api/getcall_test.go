package api

import (
	"bytes"
	"log/slog"
	"math"
	"net/http"
	"strings"
	"testing"
	"time"

	"github.com/livekit/protocol/livekit"
	"google.golang.org/protobuf/encoding/protojson"

	"github.com/mahimailabs/voicegateway/internal/event"
	"github.com/mahimailabs/voicegateway/internal/store"
	"github.com/mahimailabs/voicegateway/internal/store/chstore"
	"github.com/mahimailabs/voicegateway/internal/turn"
)

func path(project, callID string) string {
	return "/api/v1/org/default/project/" + project + "/calls/" + callID
}

func near(got *float64, want float64, tol float64) bool {
	return got != nil && math.Abs(*got-want) <= tol
}

// Acceptance check 1.
func TestGetCallServesStoredCall(t *testing.T) {
	f := newFixture(t)
	f.storeRealCall("api/testdata/full-call.webhooks.jsonl", "api/testdata/full-call.spans.jsonl", "api/testdata/full-call.session_report.json",
		"join/testdata/agents-1.8.7-metrics-3.binpb", "join/testdata/agents-1.8.7-metrics-4.binpb")

	var c call
	if code := f.get(f.api(nil), path(f.project, f.callID("RM_35ayuuwAkwVj")), &c); code != http.StatusOK {
		t.Fatalf("status = %d, want 200", code)
	}
	if c.Room != "vg-full-134152" || c.RoomSID == nil || *c.RoomSID != "RM_35ayuuwAkwVj" || c.EndState != "observed" {
		t.Errorf("room %q, sid %v, end state %q", c.Room, c.RoomSID, c.EndState)
	}
	if want := time.Date(2026, 10, 17, 17, 41, 53, 336_000_000, time.UTC); !c.StartedAt.Equal(want) {
		t.Errorf("startedAt = %v, want %v", c.StartedAt, want)
	}
	if want := time.Date(2026, 10, 17, 17, 42, 35, 0, time.UTC); c.EndedAt == nil || !c.EndedAt.Equal(want) {
		t.Errorf("endedAt = %v, want %v", c.EndedAt, want)
	}
	if !near(c.DurationSeconds, 41.664, 0.0005) || c.ParticipantCount == nil || *c.ParticipantCount != 2 {
		t.Errorf("duration %v, participants %v; want 41.664 and 2", c.DurationSeconds, c.ParticipantCount)
	}
	if c.Clocks[0].ID != clockCall || c.Clocks[0].Alignment != alignObserved {
		t.Errorf("call clock = %+v, want observed", c.Clocks[0])
	}
	if c.Turns == nil || len(*c.Turns) != 3 {
		t.Fatalf("turns = %v, want 3", c.Turns)
	}
	third := (*c.Turns)[2]
	if !near(third.TTFTMs, 323.6, 0.1) || !near(third.E2EMs, 844.6, 0.1) {
		t.Errorf("turn 3 ttft %v, e2e %v; want 323.6 and 844.6", third.TTFTMs, third.E2EMs)
	}
	if c.Usage == nil || !near(c.Usage.STTSeconds, 33.25, 0.001) {
		t.Errorf("usage = %+v, want sttSeconds 33.25", c.Usage)
	}
}

// Acceptance check 2.
func TestGetCallFromAgentUploadsOnly(t *testing.T) {
	f := newFixture(t)
	f.storeRealCall("", "turn/testdata/agents-1.8.7-call.spans.jsonl", "turn/testdata/agents-1.8.7-call.session_report.json",
		"join/testdata/agents-1.8.7-metrics-1.binpb", "join/testdata/agents-1.8.7-metrics-2.binpb")

	var c call
	if code := f.get(f.api(nil), path(f.project, f.callID("RM_Sg9DUevZhpJp")), &c); code != http.StatusOK {
		t.Fatalf("status = %d, want 200", code)
	}
	if want := time.Date(2026, 10, 17, 17, 3, 15, 679_406_000, time.UTC); c.Room != "vg-check-130314" || !c.StartedAt.Equal(want) {
		t.Errorf("room %q startedAt %v; want vg-check-130314 and %v", c.Room, c.StartedAt, want)
	}
	if c.EndedAt != nil || c.DurationSeconds != nil || c.ParticipantCount != nil {
		t.Errorf("endedAt %v, duration %v, participants %v; want all null", c.EndedAt, c.DurationSeconds, c.ParticipantCount)
	}
	if c.Clocks[0].Alignment != alignInferred {
		t.Errorf("call clock = %+v, want inferred", c.Clocks[0])
	}
	if c.Usage == nil || !near(c.Usage.STTSeconds, 33.85, 0.001) {
		t.Errorf("usage = %+v, want sttSeconds 33.85", c.Usage)
	}
}

func webhook(t *testing.T, ev *livekit.WebhookEvent) []byte {
	t.Helper()
	b, err := protojson.Marshal(ev)
	if err != nil {
		t.Fatal(err)
	}
	return b
}

// Acceptance check 3.
func TestGetCallWithoutAgentData(t *testing.T) {
	f := newFixture(t)
	base := time.Date(2026, 10, 17, 18, 0, 0, 0, time.UTC)
	f.append(event.KindLiveKitWebhook, "EV_1", webhook(t, &livekit.WebhookEvent{
		Event: "room_started", Id: "EV_1", CreatedAt: base.Unix(),
		Room: &livekit.Room{Sid: "RM_HOOKS", Name: "hooks-only", CreationTime: base.Unix()},
	}))
	f.store()
	var raw map[string]any
	if code := f.get(f.api(nil), path(f.project, f.callID("RM_HOOKS")), &raw); code != http.StatusOK {
		t.Fatalf("status = %d, want 200", code)
	}
	if raw["spans"] != nil || raw["turns"] != nil {
		t.Errorf("spans %v, turns %v; want null", raw["spans"], raw["turns"])
	}
	if _, ok := raw["usage"]; ok {
		t.Errorf("usage = %v, want omitted", raw["usage"])
	}
}

// Acceptance check 4.
func TestGetCallPartialUsageIsNull(t *testing.T) {
	f := newFixture(t)
	base := time.Date(2026, 10, 17, 18, 0, 0, 0, time.UTC)
	f.append(event.KindLiveKitWebhook, "EV_1", webhook(t, &livekit.WebhookEvent{
		Event: "room_started", Id: "EV_1", CreatedAt: base.Unix(),
		Room: &livekit.Room{Sid: "RM_PART", Name: "partial", CreationTime: base.Unix()},
	}))
	ids := map[string]string{"room_id": "RM_PART", "job_id": "AJ_PART"}
	with := func(extra map[string]string) map[string]string {
		m := map[string]string{"room_id": "RM_PART", "job_id": "AJ_PART"}
		for k, v := range extra {
			m[k] = v
		}
		return m
	}
	trace := "0102030405060708090a0b0c0d0e0f10"
	at := func(ms int) time.Time { return base.Add(time.Duration(ms) * time.Millisecond) }
	f.append(event.KindAgentTraces, "", f.export([]store.Span{
		{TraceID: trace, SpanID: "a000000000000001", Name: "agent_turn", Start: at(1000), End: at(5000), Attributes: ids},
		{TraceID: trace, SpanID: "b000000000000001", ParentSpanID: "a000000000000001", Name: "llm_request", Start: at(1001), End: at(1100), Attributes: with(map[string]string{"gen_ai.usage.input_tokens": "40"})},
		{TraceID: trace, SpanID: "b000000000000002", ParentSpanID: "a000000000000001", Name: "llm_request", Start: at(2001), End: at(2100), Attributes: with(map[string]string{"gen_ai.usage.input_tokens": "50"})},
		{TraceID: trace, SpanID: "b000000000000003", ParentSpanID: "a000000000000001", Name: "llm_request", Start: at(3001), End: at(3100), Attributes: ids},
	}))
	f.store()

	var c call
	if code := f.get(f.api(nil), path(f.project, f.callID("RM_PART")), &c); code != http.StatusOK {
		t.Fatalf("status = %d, want 200", code)
	}
	if c.Usage == nil || c.Usage.LLMInputTokens != nil || !strings.Contains(c.Usage.Note, "1 request did not report them") {
		t.Errorf("usage = %+v, want null input tokens and a note naming 1 request", c.Usage)
	}
	if c.Turns == nil || (*c.Turns)[0].InputTokens != nil {
		t.Errorf("turn input tokens = %v, want null", c.Turns)
	}
}

// Acceptance check 5.
func TestGetCallNotFound(t *testing.T) {
	f := newFixture(t)
	base := time.Date(2026, 10, 17, 18, 0, 0, 0, time.UTC)
	f.append(event.KindLiveKitWebhook, "EV_1", webhook(t, &livekit.WebhookEvent{
		Event: "room_started", Id: "EV_1", CreatedAt: base.Unix(),
		Room: &livekit.Room{Sid: "RM_MINE", Name: "mine", CreationTime: base.Unix()},
	}))
	f.store()
	mine := f.callID("RM_MINE")
	other := f.addProject(t.Name() + "-other")

	// An organisation the caller is not in, existing or not, is a denied membership: 403.
	var denied apiError
	if code := f.get(f.api(nil), "/api/v1/org/nobody/project/"+f.project+"/calls/"+mine, &denied); code != http.StatusForbidden || denied.Code != codeForbidden {
		t.Errorf("unknown organisation: status %d, body %+v; want 403 forbidden", code, denied)
	}
	for name, p := range map[string]string{
		"unknown project":        "/api/v1/org/default/project/nothing/calls/" + mine,
		"unknown call":           path(f.project, "00000000-0000-0000-0000-000000000000"),
		"not a UUID":             path(f.project, "vg-full-134152"),
		"another project's call": path(other, mine),
	} {
		t.Run(name, func(t *testing.T) {
			var e apiError
			if code := f.get(f.api(nil), p, &e); code != http.StatusNotFound || e.Code != codeNotFound || e.RequestID == "" {
				t.Errorf("%s: status %d, body %+v; want 404 not_found with a request id", p, code, e)
			}
		})
	}
}

// Acceptance check 6.
func TestGetCallStorageDown(t *testing.T) {
	f := newFixture(t)
	base := time.Date(2026, 10, 17, 18, 0, 0, 0, time.UTC)
	f.append(event.KindLiveKitWebhook, "EV_1", webhook(t, &livekit.WebhookEvent{
		Event: "room_started", Id: "EV_1", CreatedAt: base.Unix(),
		Room: &livekit.Room{Sid: "RM_DOWN", Name: "down", CreationTime: base.Unix()},
	}))
	f.store()
	// ClickHouse at a port nothing listens on: a real connection that fails, not a fake.
	down, err := chstore.OpenReadOnly("clickhouse://voicegateway@127.0.0.1:1/voicegateway?dial_timeout=1s")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = down.Close() })
	var logged bytes.Buffer
	a := f.api(down)
	a.Log = slog.New(slog.NewJSONHandler(&logged, nil))
	var e apiError
	if code := f.get(a, path(f.project, f.callID("RM_DOWN")), &e); code != http.StatusInternalServerError || e.Code != codeInternal {
		t.Errorf("status %d, body %+v; want 500 internal_error", code, e)
	}
	// The log line an operator searches for carries the request id the body gave.
	if e.RequestID == "" || !strings.Contains(logged.String(), `"request_id":"`+e.RequestID+`"`) {
		t.Errorf("log %q does not carry request id %q", logged.String(), e.RequestID)
	}
	if strings.Contains(e.Message, "127.0.0.1") || strings.Contains(e.Message, "refused") {
		t.Errorf("error message leaks the storage error: %q", e.Message)
	}
}

// Pair review of #376: getCall's span fallback is for calls not yet summarized. Once the sweep has
// summarized a call, its stored columns are final, and a call with no room is not served.
func TestGetCallFallbackOnlyBeforeSummary(t *testing.T) {
	spans := []store.Span{{Name: "job_entrypoint", Start: listBase, End: listBase.Add(time.Second), Attributes: map[string]string{"lk.pii.room_name": "from-spans"}}}
	if _, ok := buildCall(store.Call{ID: "c"}, spans, turn.Result{}); !ok {
		t.Error("unsummarized call with a room in its spans is not served")
	}
	if _, ok := buildCall(store.Call{ID: "c", Summarized: true}, spans, turn.Result{}); ok {
		t.Error("summarized call with no stored room is served from its spans")
	}
}
