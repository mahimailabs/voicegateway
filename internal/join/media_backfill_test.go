package join_test

import (
	"context"
	"errors"
	"fmt"
	"testing"
	"time"

	"github.com/livekit/protocol/livekit"

	"github.com/mahimailabs/voicegateway/internal/event"
	"github.com/mahimailabs/voicegateway/internal/join"
	"github.com/mahimailabs/voicegateway/internal/store"
)

type failedRecordingRead struct{ join.Traces }

func (f failedRecordingRead) MediaEvents(context.Context, store.MediaBackfill) ([]store.Event, error) {
	return nil, errors.New("source unavailable")
}

func TestMediaBackfillBatchBoundaryFailureAndRestart(t *testing.T) {
	f := newFixture(t)
	ctx := context.Background()
	received := time.Now().UTC().Truncate(time.Second).Add(123456789 * time.Nanosecond)
	rows := make([]store.Event, 101)
	for i := range rows {
		id := fmt.Sprintf("EV_%03d", i)
		ev := &livekit.WebhookEvent{Event: "egress_ended", Id: id, CreatedAt: at(base), EgressInfo: &livekit.EgressInfo{EgressId: fmt.Sprintf("EG_%03d", i), Status: livekit.EgressStatus_EGRESS_FAILED}}
		if i%2 == 1 {
			ev = &livekit.WebhookEvent{Event: "ingress_ended", Id: id, CreatedAt: at(base), IngressInfo: &livekit.IngressInfo{IngressId: fmt.Sprintf("IN_%03d", i), State: &livekit.IngressState{Status: livekit.IngressState_ENDPOINT_ERROR}}}
		}
		body := f.webhook(ev)
		rows[i] = store.Event{OrganisationID: "default", ProjectID: f.project, Kind: string(event.KindLiveKitWebhook), EventID: id, DedupeKey: id, StreamID: id, ReceivedAt: received, Payload: body}
	}
	if err := f.events.Insert(ctx, rows); err != nil {
		t.Fatal(err)
	}
	j := *f.joiner
	j.Traces = failedRecordingRead{Traces: f.events}
	if err := j.BackfillMediaFindings(ctx, time.Now()); err != nil {
		t.Fatal(err)
	}
	var ns, revision int64
	var failures, count int
	var done bool
	if err := f.pool.QueryRow(ctx, "SELECT cursor_ns,failures,done FROM issue_media_backfills WHERE project_id=$1", f.project).Scan(&ns, &failures, &done); err != nil || ns != 0 || failures != 1 || done {
		t.Fatalf("failed read advanced: %d %d %v %v", ns, failures, done, err)
	}
	if _, err := f.pool.Exec(ctx, "UPDATE issue_media_backfills SET retry_at=now() WHERE project_id=$1", f.project); err != nil {
		t.Fatal(err)
	}
	if err := f.joiner.BackfillMediaFindings(ctx, time.Now()); err != nil {
		t.Fatal(err)
	}
	if err := f.pool.QueryRow(ctx, "SELECT cursor_ns,revision,done FROM issue_media_backfills WHERE project_id=$1", f.project).Scan(&ns, &revision, &done); err != nil || ns != received.UnixNano() || done {
		t.Fatalf("first page cursor: %d %d %v %v", ns, revision, done, err)
	}
	if err := f.pool.QueryRow(ctx, "SELECT count(*) FROM issue_occurrences WHERE project_id=$1", f.project).Scan(&count); err != nil || count != 100 {
		t.Fatalf("first page: %d %v", count, err)
	}
	// Simulate a crash after findings committed but before the cursor checkpoint.
	if _, err := f.pool.Exec(ctx, "UPDATE issue_media_backfills SET cursor_ns=0,cursor_key='',revision=revision+1 WHERE project_id=$1", f.project); err != nil {
		t.Fatal(err)
	}
	if err := f.joiner.BackfillMediaFindings(ctx, time.Now()); err != nil {
		t.Fatal(err)
	}
	if err := f.pool.QueryRow(ctx, "SELECT count(*) FROM issue_occurrences WHERE project_id=$1", f.project).Scan(&count); err != nil || count != 100 {
		t.Fatalf("partial-commit replay duplicated evidence: %d %v", count, err)
	}
	// A new worker resumes the persisted cursor, including the tie-breaking event key.
	restarted := *f.joiner
	if err := restarted.BackfillMediaFindings(ctx, time.Now()); err != nil {
		t.Fatal(err)
	}
	if err := f.pool.QueryRow(ctx, "SELECT count(*) FROM issue_occurrences WHERE project_id=$1", f.project).Scan(&count); err != nil || count != 101 {
		t.Fatalf("restart: %d %v", count, err)
	}
	stale := store.MediaBackfill{OrganisationID: "default", ProjectID: f.project, Revision: revision}
	if err := f.stack.calls.AdvanceMediaBackfill(ctx, stale, 0, "", false); err != nil {
		t.Fatal(err)
	}
	if err := f.pool.QueryRow(ctx, "SELECT done,cursor_ns FROM issue_media_backfills WHERE project_id=$1", f.project).Scan(&done, &ns); err != nil || !done || ns != received.UnixNano() {
		t.Fatalf("stale checkpoint regressed: %v %d %v", done, ns, err)
	}
}

func TestMediaBackfillRetainedEventAfterCallDeletion(t *testing.T) {
	f := newFixture(t)
	ctx := context.Background()
	call, err := f.stack.calls.UpsertCall(ctx, store.CallUpsert{OrganisationID: "default", ProjectID: f.project, RoomSID: "RM_deleted", StartedAt: &base})
	if err != nil {
		t.Fatal(err)
	}
	ev := &livekit.WebhookEvent{Event: "egress_ended", Id: "EV_deleted", CreatedAt: at(base), EgressInfo: &livekit.EgressInfo{EgressId: "EG_deleted", RoomId: "RM_deleted", Status: livekit.EgressStatus_EGRESS_ABORTED}}
	row := store.Event{OrganisationID: "default", ProjectID: f.project, Kind: string(event.KindLiveKitWebhook), EventID: ev.Id, DedupeKey: ev.Id, StreamID: ev.Id, ReceivedAt: time.Now(), Payload: f.webhook(ev), CallID: call}
	if err := f.events.Insert(ctx, []store.Event{row}); err != nil {
		t.Fatal(err)
	}
	if _, err := f.pool.Exec(ctx, "DELETE FROM calls WHERE id=$1", call); err != nil {
		t.Fatal(err)
	}
	if err := f.joiner.BackfillMediaFindings(ctx, time.Now()); err != nil {
		t.Fatal(err)
	}
	var linked *string
	if err := f.pool.QueryRow(ctx, "SELECT call_id::text FROM issue_occurrences WHERE project_id=$1 AND source_id='webhook:EV_deleted'", f.project).Scan(&linked); err != nil || linked != nil {
		t.Fatalf("retained evidence: %v %v", linked, err)
	}
	if calls := f.calls(); len(calls) != 0 {
		t.Fatalf("backfill recreated calls: %+v", calls)
	}
}
