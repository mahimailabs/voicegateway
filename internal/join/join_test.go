package join_test

import (
	"context"
	"errors"
	"fmt"
	"math"
	"os"
	"sync/atomic"
	"testing"
	"time"

	"github.com/livekit/protocol/livekit"

	"github.com/mahimailabs/voicegateway/internal/event"
	"github.com/mahimailabs/voicegateway/internal/join"
	"github.com/mahimailabs/voicegateway/internal/store"
	"github.com/mahimailabs/voicegateway/internal/store/chstore"
	"github.com/mahimailabs/voicegateway/internal/stream"
)

// base is a whole second, since webhooks report seconds.
var base = time.Now().UTC().Truncate(time.Second)

func room(sid, name string, created time.Time) *livekit.Room {
	r := &livekit.Room{Sid: sid, Name: name}
	if !created.IsZero() {
		r.CreationTime = created.Unix()
	}
	return r
}

func at(t time.Time) int64 { return t.Unix() }

func equalTime(a *time.Time, b time.Time) bool { return a != nil && a.Equal(b) }

// Acceptance check 1.
func TestJoinWebhooksIntoCall(t *testing.T) {
	f := newFixture(t)
	r := room("RM_A", "call-a", base)
	f.appendWebhook(&livekit.WebhookEvent{Event: "room_started", Room: r, Id: "EV_1", CreatedAt: at(base)})
	f.appendWebhook(&livekit.WebhookEvent{
		Event: "participant_joined", Room: r, Id: "EV_2", CreatedAt: at(base.Add(time.Second)),
		Participant: &livekit.ParticipantInfo{Sid: "PA_agent", Identity: "agent", Kind: livekit.ParticipantInfo_AGENT, JoinedAt: at(base.Add(time.Second))},
	})
	f.appendWebhook(&livekit.WebhookEvent{
		Event: "participant_joined", Room: r, Id: "EV_3", CreatedAt: at(base.Add(2 * time.Second)),
		Participant: &livekit.ParticipantInfo{Sid: "PA_caller", Identity: "caller", JoinedAt: at(base.Add(2 * time.Second))},
	})
	f.appendWebhook(&livekit.WebhookEvent{
		Event: "participant_left", Room: r, Id: "EV_4", CreatedAt: at(base.Add(time.Minute)),
		Participant: &livekit.ParticipantInfo{Sid: "PA_caller", Identity: "caller"},
	})
	f.appendWebhook(&livekit.WebhookEvent{Event: "room_finished", Room: r, Id: "EV_5", CreatedAt: at(base.Add(2 * time.Minute))})
	f.store("w1")

	calls := f.calls()
	c, ok := calls["RM_A"]
	if len(calls) != 1 || !ok {
		t.Fatalf("calls = %+v, want one for RM_A", calls)
	}
	if !equalTime(c.StartedAt, base) || !equalTime(c.EndedAt, base.Add(2*time.Minute)) || c.EndState != "observed" {
		t.Errorf("call = %+v, want start %v, end %v, observed", c, base, base.Add(2*time.Minute))
	}
	ps := f.participants(c.ID)
	if len(ps) != 2 || ps["PA_agent"].Kind != "agent" || ps["PA_caller"].Kind != "standard" {
		t.Fatalf("participants = %+v", ps)
	}
	if !equalTime(ps["PA_caller"].JoinedAt, base.Add(2*time.Second)) || !equalTime(ps["PA_caller"].LeftAt, base.Add(time.Minute)) {
		t.Errorf("caller = %+v", ps["PA_caller"])
	}
	events, n := f.eventCalls()
	if n != 5 {
		t.Fatalf("event rows = %d, want 5", n)
	}
	for key, id := range events {
		if id != c.ID {
			t.Errorf("event %s call id = %q, want %q", key, id, c.ID)
		}
	}
}

// Acceptance check 2.
func TestReusedRoomNameIsTwoCalls(t *testing.T) {
	f := newFixture(t)
	one, two := room("RM_1", "support", base), room("RM_2", "support", base.Add(time.Second))
	f.appendWebhook(&livekit.WebhookEvent{Event: "room_started", Room: one, Id: "EV_1a", CreatedAt: at(base)})
	f.appendWebhook(&livekit.WebhookEvent{Event: "room_started", Room: two, Id: "EV_2a", CreatedAt: at(base.Add(time.Second))})
	f.appendWebhook(&livekit.WebhookEvent{Event: "room_finished", Room: one, Id: "EV_1b", CreatedAt: at(base.Add(time.Minute))})
	f.appendWebhook(&livekit.WebhookEvent{Event: "room_finished", Room: two, Id: "EV_2b", CreatedAt: at(base.Add(2 * time.Minute))})
	f.store("w1")

	calls := f.calls()
	if len(calls) != 2 {
		t.Fatalf("calls = %+v, want 2", calls)
	}
	events, _ := f.eventCalls()
	want := map[string]string{"EV_1a": calls["RM_1"].ID, "EV_1b": calls["RM_1"].ID, "EV_2a": calls["RM_2"].ID, "EV_2b": calls["RM_2"].ID}
	for key, id := range want {
		if events[key] != id {
			t.Errorf("event %s call id = %q, want %q", key, events[key], id)
		}
	}
}

// Acceptance check 3.
func TestSessionReportJoinsCall(t *testing.T) {
	f := newFixture(t)
	f.appendWebhook(&livekit.WebhookEvent{Event: "room_started", Room: room("RM_W", "w", base), Id: "EV_1", CreatedAt: at(base)})
	f.append(event.KindAgentSessionReport, "AJ_W", f.sessionReport(&livekit.MetricsRecordingHeader{
		RoomId: "RM_W", JobId: "AJ_W", StartTime: ts(base.Add(time.Second)), Duration: 30_000,
	}))
	roomStart := base.Add(-time.Minute).Truncate(time.Millisecond)
	f.append(event.KindAgentSessionReport, "AJ_N", f.sessionReport(&livekit.MetricsRecordingHeader{
		RoomId: "RM_N", JobId: "AJ_N", RoomStartTime: ts(roomStart), StartTime: ts(base),
	}))
	f.store("w1")

	calls := f.calls()
	if len(calls) != 2 {
		t.Fatalf("calls = %+v, want 2", calls)
	}
	if c := calls["RM_W"]; c.JobID != "AJ_W" || !equalTime(c.StartedAt, base) {
		t.Errorf("webhook call = %+v, want job AJ_W and the webhook's start", c)
	}
	if c := calls["RM_N"]; c.JobID != "AJ_N" || !equalTime(c.StartedAt, roomStart) || c.EndState != "open" {
		t.Errorf("new call = %+v, want job AJ_N, start %v, open", c, roomStart)
	}
	events, _ := f.eventCalls()
	if events["AJ_W"] != calls["RM_W"].ID || events["AJ_N"] != calls["RM_N"].ID {
		t.Errorf("report call ids = %+v", events)
	}
}

// Acceptance check 4.
func TestSpansJoinByOwnIDs(t *testing.T) {
	f := newFixture(t)
	ids := map[string]string{"room_id": "RM_S", "job_id": "AJ_S"}
	// The resource names the process's first job, as LiveKit Agents leaves it.
	stale := map[string]string{"room_id": "RM_OTHER", "job_id": "AJ_OTHER"}
	children := f.export(
		span{trace: "T1", id: "c1", parent: "root", start: base.Add(time.Second), end: base.Add(2 * time.Second), attrs: ids},
		span{trace: "T1", id: "c2", parent: "root", start: base.Add(2 * time.Second), end: base.Add(3 * time.Second), attrs: ids, resource: stale},
	)
	root := f.export(span{trace: "T1", id: "root", start: base, end: base.Add(time.Minute), attrs: ids})
	f.append(event.KindAgentTraces, "", children)
	f.append(event.KindAgentTraces, "", children) // delivered twice
	f.append(event.KindAgentTraces, "", root)
	f.store("w1")

	calls := f.calls()
	c, ok := calls["RM_S"]
	if len(calls) != 1 || !ok || c.JobID != "AJ_S" {
		t.Fatalf("calls = %+v, want only RM_S with job AJ_S", calls)
	}
	spans, n := f.spanCalls()
	if n != 3 {
		t.Fatalf("span rows = %d, want 3", n)
	}
	for _, id := range []string{"c1", "c2", "root"} {
		if spans[hexSpan(id)] != c.ID {
			t.Errorf("span %s own call id = %q, want %q", id, spans[hexSpan(id)], c.ID)
		}
	}
	// Every export row resolved to the one call its spans joined.
	events, rows := f.eventCalls()
	if rows != 3 {
		t.Fatalf("export rows = %d, want 3", rows)
	}
	for key, id := range events {
		if id != c.ID {
			t.Errorf("export %s call id = %q, want %q", key, id, c.ID)
		}
	}
}

// Acceptance check 5, first half.
func TestSpansJoinThroughTraceID(t *testing.T) {
	f := newFixture(t)
	f.append(event.KindAgentTraces, "", f.export(
		span{trace: "T2", id: "bare", parent: "root", start: base, end: base.Add(time.Second)},
		span{trace: "T2", id: "root", start: base, end: base.Add(time.Minute), attrs: map[string]string{"room_id": "RM_T"}},
	))
	f.store("w1")
	if err := f.joiner.Sweep(context.Background(), base.Add(time.Hour)); err != nil {
		t.Fatal(err)
	}

	c := f.calls()["RM_T"]
	resolved, err := f.events.SpanCalls(context.Background(), "default", f.project, hexTrace("T2"))
	if err != nil {
		t.Fatal(err)
	}
	if resolved[hexSpan("bare")] != c.ID || resolved[hexSpan("root")] != c.ID {
		t.Errorf("resolved = %+v, want both spans on %s", resolved, c.ID)
	}
	if n := f.unmatched(chstore.UnlinkedTraceKind); n != 0 {
		t.Errorf("unmatched traces = %d, want 0", n)
	}
}

// Acceptance check 5, second half.
func TestUnlinkedTraceIsCountedNotDropped(t *testing.T) {
	f := newFixture(t)
	last := base.Add(time.Second)
	f.append(event.KindAgentTraces, "", f.export(
		span{trace: "T3", id: "a", start: base, end: base.Add(500 * time.Millisecond)},
		span{trace: "T3", id: "b", start: base, end: last},
	))
	f.store("w1")

	if err := f.joiner.Sweep(context.Background(), last.Add(join.LinkWindow-time.Second)); err != nil {
		t.Fatal(err)
	}
	if n := f.unmatched(chstore.UnlinkedTraceKind); n != 0 {
		t.Fatalf("unmatched inside the window = %d, want 0", n)
	}
	for range 2 {
		if err := f.joiner.Sweep(context.Background(), last.Add(join.LinkWindow+time.Second)); err != nil {
			t.Fatal(err)
		}
	}
	if n := f.unmatched(chstore.UnlinkedTraceKind); n != 1 {
		t.Errorf("unmatched traces = %d after two sweeps, want 1", n)
	}
	if _, n := f.spanCalls(); n != 2 {
		t.Errorf("spans stored = %d, want 2", n)
	}
}

// Acceptance check 6.
func TestEgressAndIngressJoinOrStayUnmatched(t *testing.T) {
	f := newFixture(t)
	f.appendWebhook(&livekit.WebhookEvent{Event: "room_started", Room: room("RM_E", "studio", base), Id: "EV_1", CreatedAt: at(base)})
	f.appendWebhook(&livekit.WebhookEvent{
		Event: "egress_started", Id: "EV_2", CreatedAt: at(base.Add(time.Second)),
		EgressInfo: &livekit.EgressInfo{EgressId: "EG_1", RoomId: "RM_E", RoomName: "studio"},
	})
	f.appendWebhook(&livekit.WebhookEvent{
		Event: "ingress_started", Id: "EV_3", CreatedAt: at(base.Add(2 * time.Second)),
		IngressInfo: &livekit.IngressInfo{IngressId: "IN_1", RoomName: "studio"},
	})
	f.appendWebhook(&livekit.WebhookEvent{
		Event: "ingress_started", Id: "EV_4", CreatedAt: at(base.Add(2 * time.Second)),
		IngressInfo: &livekit.IngressInfo{IngressId: "IN_2", RoomName: "nobody-here"},
	})
	f.store("w1")

	calls := f.calls()
	if len(calls) != 1 {
		t.Fatalf("calls = %+v, want only RM_E", calls)
	}
	id := calls["RM_E"].ID
	events, _ := f.eventCalls()
	if events["EV_2"] != id || events["EV_3"] != id {
		t.Errorf("egress and ingress call ids = %q, %q; want %q", events["EV_2"], events["EV_3"], id)
	}
	if events["EV_4"] != "" {
		t.Errorf("ingress for an empty room joined %q", events["EV_4"])
	}
	if n := f.unmatched(string(event.KindLiveKitWebhook)); n != 1 {
		t.Errorf("unmatched webhooks = %d, want 1", n)
	}
}

// Acceptance check 7.
func TestOpenCallClosesAsInferred(t *testing.T) {
	f := newFixture(t)
	f.appendWebhook(&livekit.WebhookEvent{Event: "room_started", Room: room("RM_IDLE", "a", base), Id: "EV_1", CreatedAt: at(base)})
	f.appendWebhook(&livekit.WebhookEvent{Event: "room_started", Room: room("RM_SILENT", "b", time.Time{}), Id: "EV_2"})
	f.appendWebhook(&livekit.WebhookEvent{Event: "room_started", Room: room("RM_BUSY", "c", base), Id: "EV_3", CreatedAt: at(base)})
	f.append(event.KindAgentTraces, "", f.export(span{
		trace: "T4", id: "s", start: base.Add(100 * time.Minute), end: base.Add(110 * time.Minute),
		attrs: map[string]string{"room_id": "RM_BUSY"},
	}))
	f.appendWebhook(&livekit.WebhookEvent{Event: "room_started", Room: room("RM_DONE", "d", base), Id: "EV_4", CreatedAt: at(base)})
	f.appendWebhook(&livekit.WebhookEvent{Event: "room_finished", Room: room("RM_DONE", "d", base), Id: "EV_5", CreatedAt: at(base.Add(time.Hour))})
	f.store("w1")

	if err := f.joiner.Sweep(context.Background(), base.Add(join.IdleTimeout+time.Minute)); err != nil {
		t.Fatal(err)
	}
	calls := f.calls()
	if c := calls["RM_IDLE"]; c.EndState != "inferred" || !equalTime(c.EndedAt, base) {
		t.Errorf("idle call = %+v, want inferred, ending %v", c, base)
	}
	if c := calls["RM_SILENT"]; c.EndState != "inferred" || c.EndedAt != nil {
		t.Errorf("call with no reported time = %+v, want inferred with no end", c)
	}
	if c := calls["RM_BUSY"]; c.EndState != "open" {
		t.Errorf("call with recent spans = %+v, want open", c)
	}
	if c := calls["RM_DONE"]; c.EndState != "observed" || !equalTime(c.EndedAt, base.Add(time.Hour)) {
		t.Errorf("finished call = %+v, want observed", c)
	}
}

// Acceptance check 8.
func TestUnreportedTimesStayNull(t *testing.T) {
	f := newFixture(t)
	r := room("RM_U", "u", time.Time{})
	f.appendWebhook(&livekit.WebhookEvent{Event: "room_started", Room: r, Id: "EV_1"})
	f.appendWebhook(&livekit.WebhookEvent{
		Event: "participant_joined", Room: r, Id: "EV_2",
		Participant: &livekit.ParticipantInfo{Sid: "PA_1", Kind: livekit.ParticipantInfo_SIP},
	})
	f.store("w1")

	c := f.calls()["RM_U"]
	if c.ID == "" || c.StartedAt != nil || c.EndedAt != nil {
		t.Fatalf("call = %+v, want no start and no end", c)
	}
	p := f.participants(c.ID)["PA_1"]
	if p.Kind != "sip" || p.JoinedAt != nil || p.LeftAt != nil {
		t.Errorf("participant = %+v, want sip with no times", p)
	}
}

// Acceptance check 9.
func TestJoinIsIdempotentAcrossRestart(t *testing.T) {
	f := newFixture(t)
	r := room("RM_R", "r", base)
	hooks := []*livekit.WebhookEvent{
		{Event: "room_started", Room: r, Id: "EV_1", CreatedAt: at(base)},
		{Event: "participant_joined", Room: r, Id: "EV_2", CreatedAt: at(base), Participant: &livekit.ParticipantInfo{Sid: "PA_1", JoinedAt: at(base)}},
		{Event: "participant_joined", Room: r, Id: "EV_3", CreatedAt: at(base), Participant: &livekit.ParticipantInfo{Sid: "PA_2", Kind: livekit.ParticipantInfo_AGENT, JoinedAt: at(base)}},
	}
	for _, h := range hooks {
		f.appendWebhook(h)
	}
	f.store("w1")
	for _, h := range hooks {
		f.appendWebhook(h)
	}
	f.store("w2") // a restarted worker

	calls := f.calls()
	if len(calls) != 1 {
		t.Fatalf("calls = %d, want 1", len(calls))
	}
	if ps := f.participants(calls["RM_R"].ID); len(ps) != 2 {
		t.Errorf("participants = %d, want 2", len(ps))
	}
	if _, n := f.eventCalls(); n != 3 {
		t.Errorf("event rows = %d, want 3", n)
	}
}

// pgDown is the call index unreachable while down is set.
type pgDown struct {
	join.Calls
	down atomic.Bool
}

var errRefused = errors.New("dial tcp 127.0.0.1:5432: connect: connection refused")

func (p *pgDown) UpsertCall(ctx context.Context, c store.CallUpsert) (string, error) {
	if p.down.Load() {
		return "", errRefused
	}
	return p.Calls.UpsertCall(ctx, c)
}

func (p *pgDown) UpsertParticipant(ctx context.Context, pt store.Participant) error {
	if p.down.Load() {
		return errRefused
	}
	return p.Calls.UpsertParticipant(ctx, pt)
}

// Acceptance check 10.
func TestJoinAcksOnlyAfterEveryWrite(t *testing.T) {
	f := newFixture(t)
	r := room("RM_D", "d", base)
	f.appendWebhook(&livekit.WebhookEvent{Event: "room_started", Room: r, Id: "EV_1", CreatedAt: at(base)})
	f.appendWebhook(&livekit.WebhookEvent{
		Event: "participant_joined", Room: r, Id: "EV_2", CreatedAt: at(base),
		Participant: &livekit.ParticipantInfo{Sid: "PA_1"},
	})
	calls := &pgDown{Calls: f.joiner.Calls}
	calls.down.Store(true)
	j := &join.Joiner{Calls: calls, Traces: f.events, Log: f.joiner.Log}

	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() { done <- f.sink("w1", j).Run(ctx) }()
	time.Sleep(time.Second)
	if f.drained() {
		t.Fatal("entries acknowledged while Postgres was down")
	}
	if _, n := f.eventCalls(); n != 0 {
		t.Fatalf("event rows during the outage = %d, want 0", n)
	}
	calls.down.Store(false)
	deadline := time.Now().Add(20 * time.Second)
	for !f.drained() && time.Now().Before(deadline) {
		time.Sleep(20 * time.Millisecond)
	}
	cancel()
	if err := <-done; err != nil {
		t.Errorf("Run = %v", err)
	}
	c := f.calls()["RM_D"]
	events, n := f.eventCalls()
	if n != 2 || events["EV_1"] != c.ID || events["EV_2"] != c.ID {
		t.Errorf("events after recovery = %+v (%d rows), want both on %s", events, n, c.ID)
	}
}

// Pair-review note: LiveKit Agents falls back to the last job's ids outside a job, so a span made
// between jobs names a call that has already ended. It is stored, not joined to that call.
func TestSpanAfterItsCallEndedIsNotJoined(t *testing.T) {
	f := newFixture(t)
	r := room("RM_OLD", "o", base)
	f.appendWebhook(&livekit.WebhookEvent{Event: "room_started", Room: r, Id: "EV_1", CreatedAt: at(base)})
	f.appendWebhook(&livekit.WebhookEvent{Event: "room_finished", Room: r, Id: "EV_2", CreatedAt: at(base.Add(time.Minute))})
	f.store("w1")
	ids := map[string]string{"room_id": "RM_OLD", "job_id": "AJ_OLD"}
	f.append(event.KindAgentTraces, "", f.export(
		span{trace: "T5", id: "in", start: base.Add(30 * time.Second), end: base.Add(40 * time.Second), attrs: ids},
		// Shutdown spans and clock skew: shortly after the end still joins.
		span{trace: "T5", id: "shutdown", start: base.Add(90 * time.Second), end: base.Add(95 * time.Second), attrs: ids},
		span{trace: "T6", id: "late", start: base.Add(3 * time.Minute), end: base.Add(4 * time.Minute), attrs: ids},
	))
	f.store("w1")

	c := f.calls()["RM_OLD"]
	spans, n := f.spanCalls()
	if n != 3 || spans[hexSpan("in")] != c.ID || spans[hexSpan("shutdown")] != c.ID || spans[hexSpan("late")] != "" {
		t.Errorf("spans = %+v, want 'in' and 'shutdown' on %s and 'late' unjoined", spans, c.ID)
	}
	if c.EndState != "observed" || !equalTime(c.EndedAt, base.Add(time.Minute)) {
		t.Errorf("call = %+v, want its end unchanged", c)
	}
}

// A job-only span ahead of the first span naming the room, in one export, joins the room's call
// rather than opening a second.
func TestJobOnlySpanFirstJoinsRoomCall(t *testing.T) {
	f := newFixture(t)
	f.append(event.KindAgentTraces, "", f.export(
		span{trace: "T7", id: "job", start: base, end: base.Add(time.Second), attrs: map[string]string{"lk.job_id": "AJ_7"}},
		span{trace: "T7", id: "room", start: base, end: base.Add(time.Second), attrs: map[string]string{"room_id": "RM_7", "job_id": "AJ_7"}},
	))
	f.store("w1")
	calls := f.calls()
	if len(calls) != 1 || calls["RM_7"].JobID != "AJ_7" {
		t.Fatalf("calls = %+v, want one call for RM_7 with job AJ_7", calls)
	}
	spans, _ := f.spanCalls()
	if spans[hexSpan("job")] != calls["RM_7"].ID {
		t.Errorf("job-only span call id = %q, want %q", spans[hexSpan("job")], calls["RM_7"].ID)
	}
}

// A webhook Postgres refuses for its values (a NUL byte in the room name) is dead-lettered on its
// own; the rest of the batch is joined and stored, and the worker keeps going.
func TestRefusedRowIsDeadLettered(t *testing.T) {
	f := newFixture(t)
	f.appendWebhook(&livekit.WebhookEvent{Event: "room_started", Room: room("RM_OK", "fine", base), Id: "EV_1", CreatedAt: at(base)})
	f.appendWebhook(&livekit.WebhookEvent{Event: "room_started", Room: room("RM_BAD", "nul\x00name", base), Id: "EV_2", CreatedAt: at(base)})
	before := f.rdb.XLen(context.Background(), stream.DeadLetters(stream.Ingest)).Val()
	f.store("w1")

	if calls := f.calls(); len(calls) != 1 || calls["RM_OK"].ID == "" {
		t.Fatalf("calls = %+v, want only RM_OK", calls)
	}
	if events, n := f.eventCalls(); n != 1 || events["EV_1"] == "" {
		t.Errorf("events = %+v, want EV_1 joined", events)
	}
	if n := f.rdb.XLen(context.Background(), stream.DeadLetters(stream.Ingest)).Val() - before; n != 1 {
		t.Errorf("dead letters added = %d, want 1", n)
	}
}

// A span the exporter sent without times is stored with null times, never the epoch.
func TestSpanWithoutTimesStoresNull(t *testing.T) {
	f := newFixture(t)
	f.append(event.KindAgentTraces, "", f.export(span{trace: "T8", id: "notime"}))
	f.store("w1")
	var nulls uint64
	if err := f.ch.QueryRow(context.Background(),
		`SELECT count() FROM spans FINAL WHERE project_id = ? AND start_time IS NULL AND end_time IS NULL`, f.project).Scan(&nulls); err != nil || nulls != 1 {
		t.Errorf("spans with null times = %d, %v; want 1", nulls, err)
	}
}

// An export whose spans join two calls names neither on its own row; each span keeps its call.
func TestExportSpanningTwoCallsHasNoCallID(t *testing.T) {
	f := newFixture(t)
	f.append(event.KindAgentTraces, "", f.export(
		span{trace: "T9", id: "x", start: base, end: base.Add(time.Second), attrs: map[string]string{"room_id": "RM_X"}},
		span{trace: "TA", id: "y", start: base, end: base.Add(time.Second), attrs: map[string]string{"room_id": "RM_Y"}},
	))
	f.store("w1")
	calls := f.calls()
	if len(calls) != 2 {
		t.Fatalf("calls = %+v, want 2", calls)
	}
	events, n := f.eventCalls()
	for key, id := range events {
		if n != 1 || id != "" {
			t.Errorf("export %s call id = %q (%d rows), want one row with an empty call id", key, id, n)
		}
	}
	spans, _ := f.spanCalls()
	if spans[hexSpan("x")] != calls["RM_X"].ID || spans[hexSpan("y")] != calls["RM_Y"].ID {
		t.Errorf("span calls = %+v", spans)
	}
}

// Acceptance check 10 of phase 04b: STT audio seconds come from the agent's usage counter, joined
// point by point, counted once when an export is delivered twice, and stay null without a point.
func TestSTTAudioFromUsageMetric(t *testing.T) {
	f := newFixture(t)
	exports := make([][]byte, 4)
	for i := range exports {
		b, err := os.ReadFile(fmt.Sprintf("testdata/agents-1.8.7-metrics-%d.binpb", i+1))
		if err != nil {
			t.Fatal(err)
		}
		exports[i] = b
	}
	for _, e := range exports {
		f.append(event.KindAgentMetrics, "", e)
	}
	f.appendWebhook(&livekit.WebhookEvent{Event: "room_started", Room: room("RM_NOSTT", "quiet", base), Id: "EV_1", CreatedAt: at(base)})
	f.store("w1")
	// The second export delivered again later, so it lands in a part of its own.
	f.append(event.KindAgentMetrics, "", exports[1])
	f.store("w2")

	calls := f.calls()
	ctx := context.Background()
	for sid, want := range map[string]float64{"RM_Sg9DUevZhpJp": 23.900 + 9.950, "RM_35ayuuwAkwVj": 24.750 + 8.500} {
		got, err := f.events.CallTurns(ctx, "default", f.project, calls[sid].ID)
		if err != nil {
			t.Fatal(err)
		}
		if s := got.Usage.STTAudioSeconds.Sum; s == nil || math.Abs(*s-want) > 0.001 {
			t.Errorf("call %s STT audio = %s, want %.3f", sid, orNull(s), want)
		}
	}
	quiet, err := f.events.CallTurns(ctx, "default", f.project, calls["RM_NOSTT"].ID)
	if err != nil {
		t.Fatal(err)
	}
	if s := quiet.Usage.STTAudioSeconds.Sum; s != nil {
		t.Errorf("call with no metric point STT audio = %v, want null", *s)
	}
}

func orNull(v *float64) string {
	if v == nil {
		return "null"
	}
	return fmt.Sprintf("%.3f", *v)
}
