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

// An existing summary must not need to be rebuilt to populate historical issues.
func TestWorkerFindingsHistoricalBackfill(t *testing.T) {
	f := newFixture(t)
	ctx := context.Background()
	f.roomCall("RM_BACKFILL", listBase, nil)
	f.append(event.KindAgentTraces, "", f.export([]store.Span{{
		TraceID: fmt.Sprintf("%032x", 101), SpanID: fmt.Sprintf("%016x", 101),
		Name: "agent_turn", Start: listBase, End: listBase.Add(3 * time.Second),
		Attributes: map[string]string{"room_id": "RM_BACKFILL", "lk.e2e_latency": "2.5"},
	}}))
	f.store()
	f.summarize()
	id := f.callID("RM_BACKFILL")
	// Model an already summarized installation upgraded before findings existed.
	if _, err := f.pool.Exec(ctx, "DELETE FROM issues WHERE organisation_id = 'default' AND project_id = $1", f.project); err != nil {
		t.Fatal(err)
	}
	if _, err := f.pool.Exec(ctx, "UPDATE calls SET issue_evaluated_at = NULL, issue_settings_revision = -1 WHERE id = $1::uuid", id); err != nil {
		t.Fatal(err)
	}
	var before, after string
	if err := f.pool.QueryRow(ctx, "SELECT row_to_json(s)::text FROM call_summaries s WHERE call_id = $1::uuid", id).Scan(&before); err != nil {
		t.Fatal(err)
	}
	// Queue more than one sweep of older calls so this test also works in isolation.
	if _, err := f.pool.Exec(ctx, `INSERT INTO calls (id, organisation_id, project_id, room_sid, room_name, started_at, summary_stale_at)
	 SELECT gen_random_uuid(), 'default', $1,
	 'backlog-' || n, 'backlog-' || n, $2, NULL FROM generate_series(1, 101) n`, f.project, listBase); err != nil {
		t.Fatal(err)
	}
	j := &join.Joiner{Calls: f.pg, Traces: failingTrace{Traces: f.events, call: id}, Log: f.log}
	// The package shares a native database. Earlier fixtures may have more than one batch of
	// historical calls queued. Exercise bounded sweeps until this call is reached.
	var failures int
	for range 20 {
		if err := j.BackfillFindings(ctx, time.Now()); err != nil {
			t.Fatal(err)
		}
		if err := f.pool.QueryRow(ctx, "SELECT issue_failures FROM calls WHERE id = $1::uuid", id).Scan(&failures); err != nil {
			t.Fatal(err)
		}
		if failures > 0 {
			break
		}
	}
	if failures != 1 {
		t.Fatalf("retry state after bounded sweeps: %d", failures)
	}
	if _, err := f.pool.Exec(ctx, "UPDATE calls SET issue_retry_at = NULL WHERE id = $1::uuid", id); err != nil {
		t.Fatal(err)
	}
	j.Traces = f.events
	if err := j.BackfillFindings(ctx, time.Now()); err != nil {
		t.Fatal(err)
	}
	m, err := f.pg.IssueMetadata(ctx, "default", f.project, "slow")
	if err != nil || m.EvidenceRevision != 1 {
		t.Fatalf("historical issue: %+v %v", m, err)
	}
	if err := f.pool.QueryRow(ctx, "SELECT row_to_json(s)::text FROM call_summaries s WHERE call_id = $1::uuid", id).Scan(&after); err != nil {
		t.Fatal(err)
	}
	if before != after {
		t.Fatal("backfill rewrote an existing summary")
	}
	if err := j.BackfillFindings(ctx, time.Now()); err != nil {
		t.Fatal(err)
	}
	m, err = f.pg.IssueMetadata(ctx, "default", f.project, "slow")
	if err != nil || m.EvidenceRevision != 1 {
		t.Fatalf("backfill replay: %+v %v", m, err)
	}
}
