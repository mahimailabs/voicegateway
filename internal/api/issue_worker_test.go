package api

import (
	"context"
	"fmt"
	"testing"
	"time"

	"github.com/mahimailabs/voicegateway/internal/event"
	"github.com/mahimailabs/voicegateway/internal/join"
	"github.com/mahimailabs/voicegateway/internal/store"
)

// Native proof: ingest a reported latency through Redis and ClickHouse, then let the real worker
// store the issue alongside its call summary. Retry and source failure must not invent new hits.
func TestWorkerFindingsDurableRetryAndSourceFailure(t *testing.T) {
	f := newFixture(t)
	ctx := context.Background()
	f.roomCall("RM_ISSUE", listBase, nil)
	f.append(event.KindAgentTraces, "", f.export([]store.Span{{
		TraceID: fmt.Sprintf("%032x", 99), SpanID: fmt.Sprintf("%016x", 99),
		Name: "agent_turn", Start: listBase, End: listBase.Add(3 * time.Second),
		Attributes: map[string]string{"room_id": "RM_ISSUE", "lk.e2e_latency": "2.5"},
	}}))
	f.store()
	f.summarize()
	id := f.callID("RM_ISSUE")
	m, err := f.pg.IssueMetadata(ctx, "default", f.project, "slow")
	if err != nil || m.State != "open" || m.EvidenceRevision != 1 {
		t.Fatalf("worker issue: %+v %v", m, err)
	}
	var active int
	if err := f.pool.QueryRow(ctx, "SELECT count(*) FROM issue_occurrences WHERE call_id = $1::uuid AND active", id).Scan(&active); err != nil || active != 1 {
		t.Fatalf("worker evidence: %d %v", active, err)
	}
	resolved := "resolved"
	if _, err := f.pg.UpdateIssueMetadata(ctx, "default", f.project, "slow", 0, 1, &resolved, nil, false); err != nil {
		t.Fatal(err)
	}
	if err := f.pg.MarkStale(ctx, []string{id}); err != nil {
		t.Fatal(err)
	}
	f.summarize()
	m, err = f.pg.IssueMetadata(ctx, "default", f.project, "slow")
	if err != nil || m.State != "resolved" || m.EvidenceRevision != 1 {
		t.Fatalf("replayed summary regressed issue: %+v %v", m, err)
	}
	if err := f.pg.MarkStale(ctx, []string{id}); err != nil {
		t.Fatal(err)
	}
	j := &join.Joiner{Calls: f.pg, Traces: failingTrace{Traces: f.events, call: id}, Log: f.log}
	if err := j.Summarize(ctx, time.Now()); err != nil {
		t.Fatal(err)
	}
	if err := f.pool.QueryRow(ctx, "SELECT count(*) FROM issue_occurrences WHERE call_id = $1::uuid AND active", id).Scan(&active); err != nil || active != 1 {
		t.Fatalf("source failure erased known evidence: %d %v", active, err)
	}
}
