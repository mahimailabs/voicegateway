package api

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/livekit/protocol/livekit"

	"github.com/mahimailabs/voicegateway/internal/event"
	"github.com/mahimailabs/voicegateway/internal/join"
	"github.com/mahimailabs/voicegateway/internal/sink"
	"github.com/mahimailabs/voicegateway/internal/store"
	"github.com/mahimailabs/voicegateway/internal/stream"
	"github.com/mahimailabs/voicegateway/internal/turn"
)

var listBase = time.Date(2026, 10, 17, 20, 0, 0, 0, time.UTC)

func listPath(project string, q url.Values) string {
	p := "/api/v1/org/default/project/" + project + "/calls"
	if len(q) > 0 {
		p += "?" + q.Encode()
	}
	return p
}

// summarize runs the worker's summary sweep until no call is stale.
func (f *fixture) summarize() {
	f.t.Helper()
	j := &join.Joiner{Calls: f.pg, Traces: f.events, Log: f.log}
	for range 10 {
		if err := j.Summarize(context.Background(), time.Now()); err != nil {
			f.t.Fatal(err)
		}
		stale, err := f.pg.StaleCalls(context.Background(), 1)
		if err != nil {
			f.t.Fatal(err)
		}
		if len(stale) == 0 {
			return
		}
	}
	f.t.Fatal("summaries still stale after 10 sweeps")
}

// roomCall stores a room_started webhook for a room created at the given time, and, when kind is
// not nil, a participant of that kind.
func (f *fixture) roomCall(sid string, created time.Time, kind *livekit.ParticipantInfo_Kind) {
	f.t.Helper()
	r := &livekit.Room{Sid: sid, Name: "room-" + sid, CreationTime: created.Unix()}
	f.append(event.KindLiveKitWebhook, "EV_"+sid, webhook(f.t, &livekit.WebhookEvent{Event: "room_started", Id: "EV_" + sid, CreatedAt: created.Unix(), Room: r}))
	if kind != nil {
		f.append(event.KindLiveKitWebhook, "EV_P_"+sid, webhook(f.t, &livekit.WebhookEvent{
			Event: "participant_joined", Id: "EV_P_" + sid, CreatedAt: created.Unix(), Room: r,
			Participant: &livekit.ParticipantInfo{Sid: "PA_" + sid, Kind: *kind, JoinedAt: created.Unix()},
		}))
	}
}

// setSummary stores a summary for a call directly, as the sweep would.
func (f *fixture) setSummary(callID string, answers []float64) {
	f.t.Helper()
	stale, err := f.pg.StaleCalls(context.Background(), 1000)
	if err != nil {
		f.t.Fatal(err)
	}
	for _, c := range stale {
		if c.ID != callID {
			continue
		}
		n := len(answers)
		var median *float64
		if n > 0 {
			sorted := slices.Sorted(slices.Values(answers))
			median = &sorted[n/2]
		}
		if err := f.pg.PutSummary(context.Background(), c, store.CallSummary{
			TurnCount: &n, AnswerMs: answers, AnswerMsMedian: median, Models: []string{}, Pipeline: "unknown",
		}, time.Now()); err != nil {
			f.t.Fatal(err)
		}
		return
	}
	f.t.Fatalf("call %s is not stale", callID)
}

func (f *fixture) list(q url.Values) (callPage, int) {
	f.t.Helper()
	var p callPage
	code := f.get(f.api(nil), listPath(f.project, q), &p)
	return p, code
}

// Acceptance check 1.
func TestSummaryFromStoredCalls(t *testing.T) {
	f := newFixture(t)
	f.storeRealCall("api/testdata/full-call.webhooks.jsonl", "api/testdata/full-call.spans.jsonl", "api/testdata/full-call.session_report.json")
	f.storeRealCall("", "turn/testdata/agents-1.8.7-call.spans.jsonl", "turn/testdata/agents-1.8.7-call.session_report.json")
	f.summarize()

	p, code := f.list(nil)
	if code != http.StatusOK || len(p.Items) != 2 {
		t.Fatalf("status %d, items %d; want 200 and 2", code, len(p.Items))
	}
	byRoom := map[string]callSummary{}
	for _, it := range p.Items {
		byRoom[it.Room] = it
	}
	full := byRoom["vg-full-134152"]
	if full.TurnCount == nil || *full.TurnCount != 3 || full.Pipeline != "cascaded" || len(full.Models) == 0 || full.Models[0] != "openai/gpt-4.1-mini" {
		t.Errorf("webhook call = turns %v, pipeline %q, models %v", full.TurnCount, full.Pipeline, full.Models)
	}
	if len(full.AnswerMs) != 2 || !near(&full.AnswerMs[0], 956.5, 0.1) || !near(&full.AnswerMs[1], 844.6, 0.1) {
		t.Errorf("webhook call answerMs = %v, want [956.5 844.6]", full.AnswerMs)
	}
	agent := byRoom["vg-check-130314"]
	if want := time.Date(2026, 10, 17, 17, 3, 15, 679_406_000, time.UTC); agent.TurnCount == nil || *agent.TurnCount != 3 || !agent.StartedAt.Equal(want) {
		t.Errorf("agent-only call = turns %v, startedAt %v; want 3 and %v", agent.TurnCount, agent.StartedAt, want)
	}
}

// Acceptance check 2.
func TestSummaryRebuildsAfterLateSpan(t *testing.T) {
	f := newFixture(t)
	f.roomCall("RM_LATE", listBase, nil)
	f.store()
	f.summarize()
	id := f.callID("RM_LATE")

	turnSpan := func(n int) []byte {
		at := listBase.Add(time.Duration(n) * time.Minute)
		return f.export([]store.Span{{
			TraceID: fmt.Sprintf("%032x", n), SpanID: fmt.Sprintf("%016x", n), Name: "agent_turn",
			Start: at, End: at.Add(time.Second), Attributes: map[string]string{"room_id": "RM_LATE", "lk.e2e_latency": "0.9"},
		}})
	}
	f.append(event.KindAgentTraces, "", turnSpan(1))
	f.store()
	f.summarize()
	if p, _ := f.list(nil); p.Items[0].TurnCount == nil || *p.Items[0].TurnCount != 1 {
		t.Fatalf("turns after a late span = %v, want 1", p.Items[0].TurnCount)
	}

	// A span joined while a rebuild is running leaves the call stale for the next sweep.
	f.append(event.KindAgentTraces, "", turnSpan(2))
	f.store()
	stale, err := f.pg.StaleCalls(context.Background(), 10)
	if err != nil || len(stale) != 1 || stale[0].ID != id {
		t.Fatalf("stale = %v, %v; want the call", stale, err)
	}
	f.append(event.KindAgentTraces, "", turnSpan(3))
	f.store() // joins after the rebuild below read its mark
	if err := f.pg.PutSummary(context.Background(), stale[0], store.CallSummary{Models: []string{}, Pipeline: "unknown"}, time.Now()); err != nil {
		t.Fatal(err)
	}
	if again, _ := f.pg.StaleCalls(context.Background(), 10); len(again) != 1 {
		t.Fatalf("stale after a racing join = %d, want 1", len(again))
	}
	f.summarize()
	if p, _ := f.list(nil); p.Items[0].TurnCount == nil || *p.Items[0].TurnCount != 3 {
		t.Errorf("turns after the next sweep = %v, want 3", p.Items[0].TurnCount)
	}
}

// Acceptance check 3.
func TestListCallsPagesOnce(t *testing.T) {
	f := newFixture(t)
	for i := range 60 {
		f.roomCall(fmt.Sprintf("RM_%02d", i), listBase.Add(time.Duration(i)*time.Minute), nil)
	}
	f.store()

	var got []callSummary
	q := url.Values{"limit": {"25"}}
	for pages := 0; ; pages++ {
		if pages == 4 {
			t.Fatal("more than 3 pages")
		}
		p, code := f.list(q)
		if code != http.StatusOK {
			t.Fatalf("page %d status %d", pages+1, code)
		}
		got = append(got, p.Items...)
		if p.Page.NextCursor == nil {
			if pages != 2 {
				t.Errorf("pages = %d, want 3", pages+1)
			}
			break
		}
		q = url.Values{"limit": {"25"}, "cursor": {*p.Page.NextCursor}, "snapshot": {p.Page.Snapshot}}
	}
	if len(got) != 60 {
		t.Fatalf("calls = %d, want 60", len(got))
	}
	seen := map[string]bool{}
	for i, c := range got {
		if seen[c.ID] {
			t.Errorf("call %s listed twice", c.ID)
		}
		seen[c.ID] = true
		if i > 0 && c.StartedAt.After(got[i-1].StartedAt) {
			t.Errorf("row %d starts after row %d: not startedAt desc", i, i-1)
		}
	}
}

// Acceptance check 4.
func TestListCallsSnapshotHolds(t *testing.T) {
	f := newFixture(t)
	for i := range 30 {
		f.roomCall(fmt.Sprintf("RM_%02d", i), listBase.Add(time.Duration(i)*time.Minute), nil)
	}
	f.store()
	first, _ := f.list(url.Values{"limit": {"25"}})
	for i := range 5 {
		// Older than every listed call, so in startedAt desc order they fall after the cursor:
		// only the snapshot keeps them off the second page.
		f.roomCall(fmt.Sprintf("RM_NEW_%d", i), listBase.Add(-time.Hour+time.Duration(i)*time.Minute), nil)
	}
	// A call on the second page gets a new turn between the two reads.
	f.append(event.KindAgentTraces, "", f.export([]store.Span{{
		TraceID: fmt.Sprintf("%032x", 99), SpanID: fmt.Sprintf("%016x", 99),
		Name: "agent_turn", Start: listBase, End: listBase.Add(time.Second), Attributes: map[string]string{"room_id": "RM_00", "lk.e2e_latency": "0.5"},
	}}))
	f.store()
	f.summarize()

	second, code := f.list(url.Values{"limit": {"25"}, "cursor": {*first.Page.NextCursor}, "snapshot": {first.Page.Snapshot}})
	if code != http.StatusOK {
		t.Fatalf("second page status %d", code)
	}
	all := append(slices.Clone(first.Items), second.Items...)
	count := map[string]int{}
	var updated *callSummary
	for i, c := range all {
		count[c.ID]++
		if len(c.Room) >= 11 && c.Room[:11] == "room-RM_NEW" {
			t.Errorf("call %s, stored after the snapshot, is listed", c.Room)
		}
		if c.Room == "room-RM_00" {
			updated = &all[i]
		}
	}
	if len(all) != 30 || len(count) != 30 {
		t.Errorf("rows %d, distinct %d; want 30 and 30", len(all), len(count))
	}
	if updated == nil || updated.TurnCount == nil || *updated.TurnCount != 1 {
		t.Errorf("updated call = %+v, want it once with its new turn", updated)
	}
	if *second.Totals.CallCount != *first.Totals.CallCount || *first.Totals.CallCount != 30 {
		t.Errorf("totals %d then %d, want 30 both times", *first.Totals.CallCount, *second.Totals.CallCount)
	}
}

// Acceptance check 5.
func TestListCallsSortsUnknownLast(t *testing.T) {
	f := newFixture(t)
	answers := map[string][]float64{"RM_A": {900}, "RM_B": {300}, "RM_C": nil, "RM_D": {600}, "RM_E": nil}
	sids := slices.Sorted(func(yield func(string) bool) {
		for k := range answers {
			if !yield(k) {
				return
			}
		}
	})
	for i, sid := range sids {
		f.roomCall(sid, listBase.Add(time.Duration(i)*time.Minute), nil)
	}
	f.store()
	for _, sid := range sids {
		f.setSummary(f.callID(sid), answers[sid])
	}

	for _, order := range []string{"asc", "desc"} {
		p, _ := f.list(url.Values{"sort": {"answerMs"}, "order": {order}})
		var medians []string
		for _, it := range p.Items {
			if it.AnswerMsMedian == nil {
				medians = append(medians, "null")
			} else {
				medians = append(medians, fmt.Sprint(*it.AnswerMsMedian))
			}
		}
		want := map[string]string{"asc": "[300 600 900 null null]", "desc": "[900 600 300 null null]"}[order]
		if fmt.Sprint(medians) != want {
			t.Errorf("%s: medians %v, want %s", order, medians, want)
		}
		if a, b := p.Items[3].ID, p.Items[4].ID; a > b {
			t.Errorf("%s: unknowns not ordered by id: %s then %s", order, a, b)
		}
	}
	p, _ := f.list(url.Values{"answerMsMin": {"0"}})
	if len(p.Items) != 3 {
		t.Errorf("answerMsMin=0 items = %d, want 3 (unknowns never match)", len(p.Items))
	}
}

// Acceptance check 6.
func TestListCallsRejectsMisusedCursor(t *testing.T) {
	f := newFixture(t)
	for i := range 3 {
		f.roomCall(fmt.Sprintf("RM_%d", i), listBase.Add(time.Duration(i)*time.Minute), nil)
	}
	f.store()
	first, _ := f.list(url.Values{"limit": {"1"}, "room": {"room-RM_1"}})
	_ = first
	p1, _ := f.list(url.Values{"limit": {"1"}})
	c, s := *p1.Page.NextCursor, p1.Page.Snapshot
	for name, q := range map[string]url.Values{
		"changed filter": {"limit": {"1"}, "cursor": {c}, "snapshot": {s}, "room": {"room-RM_0"}},
		"changed order":  {"limit": {"1"}, "cursor": {c}, "snapshot": {s}, "order": {"asc"}},
		"no snapshot":    {"limit": {"1"}, "cursor": {c}},
		"limit 0":        {"limit": {"0"}},
		"limit 101":      {"limit": {"101"}},
	} {
		t.Run(name, func(t *testing.T) {
			var e apiError
			if code := f.get(f.api(nil), listPath(f.project, q), &e); code != http.StatusBadRequest || e.Code != codeInvalidRequest {
				t.Errorf("status %d, body %+v; want 400 invalid_request", code, e)
			}
		})
	}
}

// Acceptance check 7.
func TestListCallsChannelsAndUnstoredFilters(t *testing.T) {
	f := newFixture(t)
	sip, standard := livekit.ParticipantInfo_SIP, livekit.ParticipantInfo_STANDARD
	f.roomCall("RM_SIP", listBase, &sip)
	f.roomCall("RM_WEB", listBase.Add(time.Minute), &standard)
	f.roomCall("RM_NONE", listBase.Add(2*time.Minute), nil)
	f.store()

	p, _ := f.list(nil)
	got := map[string]string{}
	for _, it := range p.Items {
		got[it.Room] = it.Channel
	}
	if got["room-RM_SIP"] != "phone" || got["room-RM_WEB"] != "web" || got["room-RM_NONE"] != "unknown" {
		t.Errorf("channels = %v", got)
	}
	if b := p.Totals.ByChannel; *b.Phone != 1 || *b.Web != 1 || *b.Unknown != 1 || *b.Console != 0 || *b.Local != 0 {
		t.Errorf("byChannel = %+v", b)
	}
	for _, q := range []url.Values{{"issues": {"high"}}, {"tag": {"x"}}, {"archived": {"true"}}, {"recorded": {"true"}}} {
		if p, _ := f.list(q); len(p.Items) != 0 || *p.Totals.CallCount != 0 {
			t.Errorf("%v: items %d, callCount %d; want 0 and 0", q, len(p.Items), *p.Totals.CallCount)
		}
	}
	if p, _ := f.list(url.Values{"archived": {"false"}}); len(p.Items) != 3 {
		t.Errorf("archived=false items = %d, want 3", len(p.Items))
	}
}

// Acceptance check 8. The median is the console's, the upper middle value: 1600 of four.
func TestListCallsTotals(t *testing.T) {
	f := newFixture(t)
	answers := [][]float64{{1000}, {1400}, {1600}, {2000}, nil}
	for i := range answers {
		f.roomCall(fmt.Sprintf("RM_%d", i), listBase.Add(time.Duration(i)*time.Minute), nil)
	}
	f.store()
	for i, a := range answers {
		f.setSummary(f.callID(fmt.Sprintf("RM_%d", i)), a)
	}
	p, _ := f.list(nil)
	a := p.Totals.Answers
	if *a.Turns != 4 || *a.OverBudget != 2 || a.BudgetMs != 1500 || a.P50Ms == nil || *a.P50Ms != 1600 || len(a.ValuesMs) != 4 || a.ValuesTruncated {
		t.Errorf("answers = turns %d, over %d, budget %d, p50 %v, values %v, truncated %v", *a.Turns, *a.OverBudget, a.BudgetMs, a.P50Ms, a.ValuesMs, a.ValuesTruncated)
	}
	// Newest call first: RM_3 (2000) was created last.
	if a.ValuesMs[0] != 2000 {
		t.Errorf("valuesMs = %v, want newest call first", a.ValuesMs)
	}
}

// Acceptance check 9.
func TestListCallsLeavesOutUnservableCalls(t *testing.T) {
	f := newFixture(t)
	f.roomCall("RM_OK", listBase, nil)
	// No start: a room no source reported a time for.
	f.append(event.KindLiveKitWebhook, "EV_NOSTART", webhook(t, &livekit.WebhookEvent{
		Event: "room_started", Id: "EV_NOSTART", Room: &livekit.Room{Sid: "RM_NOSTART", Name: "no-start"},
	}))
	// No room: a job only spans name, with times. Neither: a job only spans name, with no times.
	f.append(event.KindAgentTraces, "", f.export([]store.Span{
		{TraceID: fmt.Sprintf("%032x", 1), SpanID: fmt.Sprintf("%016x", 1), Name: "agent_turn", Start: listBase, End: listBase.Add(time.Second), Attributes: map[string]string{"job_id": "AJ_NOROOM"}},
		{TraceID: fmt.Sprintf("%032x", 2), SpanID: fmt.Sprintf("%016x", 2), Name: "agent_turn", Attributes: map[string]string{"job_id": "AJ_NEITHER"}},
	}))
	f.store()

	p, _ := f.list(nil)
	if len(p.Items) != 1 || p.Items[0].Room != "room-RM_OK" || *p.Totals.CallCount != 1 {
		t.Fatalf("items %d, callCount %d; want only room-RM_OK", len(p.Items), *p.Totals.CallCount)
	}
	rows, err := f.pool.Query(context.Background(), `SELECT id::text FROM calls WHERE project_id = $1 AND COALESCE(room_sid, '') <> 'RM_OK'`, f.project)
	if err != nil {
		t.Fatal(err)
	}
	var ids []string
	for rows.Next() {
		var id string
		if err := rows.Scan(&id); err != nil {
			t.Fatal(err)
		}
		ids = append(ids, id)
	}
	rows.Close()
	if len(ids) != 3 {
		t.Fatalf("unservable calls stored = %d, want 3", len(ids))
	}
	for _, id := range ids {
		var e apiError
		if code := f.get(f.api(nil), path(f.project, id), &e); code != http.StatusNotFound {
			t.Errorf("getCall %s = %d, want 404 by the same rule", id, code)
		}
	}
}

// sweepBeforeInsert runs the summary sweep after the join and before the rows reach ClickHouse,
// the window in which a summary could be rebuilt from a store that lacks them.
type sweepBeforeInsert struct {
	sink.Events
	f *fixture
}

func (s sweepBeforeInsert) Insert(ctx context.Context, rows []store.Event) error {
	j := &join.Joiner{Calls: s.f.pg, Traces: s.f.events, Log: s.f.log}
	if err := j.Summarize(ctx, time.Now()); err != nil {
		return err
	}
	return s.Events.Insert(ctx, rows)
}

// Pair review of #376: the stale mark is set only once the rows are in ClickHouse, so a sweep
// between the join and the insert cannot rebuild without them and clear the mark.
func TestSweepBetweenJoinAndInsertLosesNothing(t *testing.T) {
	f := newFixture(t)
	f.roomCall("RM_RACE", listBase, nil)
	f.store()
	f.summarize()
	f.append(event.KindAgentTraces, "", f.export([]store.Span{{
		TraceID: fmt.Sprintf("%032x", 7), SpanID: fmt.Sprintf("%016x", 7),
		Name: "agent_turn", Start: listBase, End: listBase.Add(time.Second), Attributes: map[string]string{"room_id": "RM_RACE", "lk.e2e_latency": "0.7"},
	}}))
	f.storeWith(sweepBeforeInsert{Events: f.events, f: f})
	f.summarize()
	if p, _ := f.list(nil); p.Items[0].TurnCount == nil || *p.Items[0].TurnCount != 1 {
		t.Fatalf("turns after a sweep between join and insert = %v, want 1", p.Items[0].TurnCount)
	}
}

// failingTrace fails ClickHouse reads for one call.
type failingTrace struct {
	join.Traces
	call string
}

func (t failingTrace) CallTrace(ctx context.Context, org, project, id string) ([]store.Span, turn.Result, error) {
	if id == t.call {
		return nil, turn.Result{}, errors.New("dial tcp: connection refused")
	}
	return t.Traces.CallTrace(ctx, org, project, id)
}

// Pair review of #376: one call whose rebuild fails is skipped and moved to the back of the queue;
// the others are rebuilt. A NUL byte in a span attribute is stripped before Postgres.
func TestSummarizeSkipsFailingCall(t *testing.T) {
	f := newFixture(t)
	f.roomCall("RM_BAD", listBase, nil)
	f.roomCall("RM_GOOD", listBase.Add(time.Minute), nil)
	f.append(event.KindAgentTraces, "", f.export([]store.Span{{
		TraceID: fmt.Sprintf("%032x", 8), SpanID: fmt.Sprintf("%016x", 8),
		Name: "llm_node", Start: listBase, End: listBase.Add(time.Second), Attributes: map[string]string{"room_id": "RM_GOOD", "gen_ai.request.model": "gpt\x00-nul"},
	}}))
	f.store()
	bad, good := f.callID("RM_BAD"), f.callID("RM_GOOD")

	before, err := f.pg.StaleCalls(context.Background(), 10)
	if err != nil {
		t.Fatal(err)
	}
	j := &join.Joiner{Calls: f.pg, Traces: failingTrace{Traces: f.events, call: bad}, Log: f.log}
	if err := j.Summarize(context.Background(), time.Now()); err != nil {
		t.Fatalf("Summarize = %v, want the sweep to carry on past one failure", err)
	}
	// The good call is done; the failing one is still marked but backed off, so it is not due yet.
	if due, err := f.pg.StaleCalls(context.Background(), 10); err != nil || len(due) != 0 {
		t.Fatalf("due after the sweep = %v, %v; want none", due, err)
	}
	var failures int
	var staleAt time.Time
	if err := f.pool.QueryRow(context.Background(), `SELECT summary_failures, summary_stale_at FROM calls WHERE id = $1::uuid`, bad).Scan(&failures, &staleAt); err != nil {
		t.Fatal(err)
	}
	for _, c := range before {
		if c.ID == bad && (failures != 1 || !staleAt.After(c.StaleAt.Add(5*time.Second))) {
			t.Errorf("failing call: %d failures, mark %v; want 1 and backed off from %v", failures, staleAt, c.StaleAt)
		}
	}
	var models []string
	if err := f.pool.QueryRow(context.Background(), `SELECT models FROM call_summaries WHERE call_id = $1::uuid`, good).Scan(&models); err != nil {
		t.Fatal(err)
	}
	if len(models) != 1 || models[0] != "gpt-nul" {
		t.Errorf("models = %q, want the NUL stripped", models)
	}
}

// Pair review of #376: a snapshot is bound to its project and filters, and a bound that is not a
// finite number is 400.
func TestListCallsSnapshotBoundToScope(t *testing.T) {
	f := newFixture(t)
	f.roomCall("RM_1", listBase, nil)
	f.store()
	p, _ := f.list(url.Values{"room": {"room-RM_1"}})
	other := f.addProject(t.Name() + "-other")
	for name, path := range map[string]string{
		"other project":  listPath(other, url.Values{"room": {"room-RM_1"}, "snapshot": {p.Page.Snapshot}}),
		"other filters":  listPath(f.project, url.Values{"room": {"room-RM_2"}, "snapshot": {p.Page.Snapshot}}),
		"unknown":        listPath(f.project, url.Values{"snapshot": {"not-a-snapshot"}}),
		"malformed":      listPath(f.project, url.Values{"snapshot": {strings.Repeat("x", 300) + "%;"}}),
		"NaN bound":      listPath(f.project, url.Values{"answerMsMin": {"NaN"}}),
		"infinite bound": listPath(f.project, url.Values{"durationSecondsMax": {"Inf"}}),
	} {
		t.Run(name, func(t *testing.T) {
			var e apiError
			if code := f.get(f.api(nil), path, &e); code != http.StatusBadRequest || e.Code != codeInvalidRequest {
				t.Errorf("status %d, body %+v; want 400 invalid_request", code, e)
			}
		})
	}
	if len(p.Page.Snapshot) > 64 {
		t.Errorf("snapshot token is %d characters, want a short id", len(p.Page.Snapshot))
	}
}

// crashAfterInsert stops the worker after its rows reach ClickHouse and before it marks their
// calls stale, as a worker that dies there would.
type crashAfterInsert struct {
	sink.Joiner
	crash context.CancelFunc
}

func (c crashAfterInsert) Stored(context.Context, []store.Event) error {
	c.crash()
	return context.Canceled
}

// Pair review of #376: a worker that dies between the insert and the stale mark loses no mark.
// The redelivered entry is joined again, though its rows are stored, and its call is marked.
func TestRedeliveryAfterCrashMarksStale(t *testing.T) {
	f := newFixture(t)
	f.roomCall("RM_CRASH", listBase, nil)
	f.store()
	f.summarize()
	f.append(event.KindAgentTraces, "", f.export([]store.Span{{
		TraceID: fmt.Sprintf("%032x", 9), SpanID: fmt.Sprintf("%016x", 9),
		Name: "agent_turn", Start: listBase, End: listBase.Add(time.Second), Attributes: map[string]string{"room_id": "RM_CRASH", "lk.e2e_latency": "0.6"},
	}}))

	ctx, crash := context.WithCancel(context.Background())
	s := f.sinkWith(f.events)
	s.Join = crashAfterInsert{Joiner: s.Join, crash: crash}
	if err := s.Run(ctx); err != nil {
		t.Fatal(err)
	}
	if pending := f.rdb.XPending(context.Background(), stream.Ingest, "worker").Val().Count; pending != 1 {
		t.Fatalf("pending after the crash = %d, want the entry unacknowledged", pending)
	}
	if stale, _ := f.pg.StaleCalls(context.Background(), 10); len(stale) != 0 {
		t.Fatalf("stale before redelivery = %d, want 0: the crash came before the mark", len(stale))
	}

	f.store() // a restarted worker claims the entry; its rows are already in ClickHouse
	f.summarize()
	if p, _ := f.list(nil); p.Items[0].TurnCount == nil || *p.Items[0].TurnCount != 1 {
		t.Fatalf("turns after redelivery = %v, want 1", p.Items[0].TurnCount)
	}
}
