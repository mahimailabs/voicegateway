package join_test

import (
	"context"
	"strings"
	"testing"

	"github.com/livekit/protocol/livekit"
)

func TestRecordingFindingsFromDurableWebhooks(t *testing.T) {
	f := newFixture(t)
	for _, ev := range []*livekit.WebhookEvent{
		{Event: "egress_ended", Id: "EV_failed", CreatedAt: at(base), EgressInfo: &livekit.EgressInfo{EgressId: "EG_failed", RoomId: "RM_recording", Status: livekit.EgressStatus_EGRESS_FAILED, Error: "provider-secret"}},
		{Event: "egress_ended", Id: "EV_limit", CreatedAt: at(base), EgressInfo: &livekit.EgressInfo{EgressId: "EG_limit", Status: livekit.EgressStatus_EGRESS_LIMIT_REACHED}},
		{Event: "egress_ended", Id: "EV_ok", CreatedAt: at(base), EgressInfo: &livekit.EgressInfo{EgressId: "EG_ok", Status: livekit.EgressStatus_EGRESS_COMPLETE}},
		{Event: "egress_updated", Id: "EV_update", CreatedAt: at(base), EgressInfo: &livekit.EgressInfo{EgressId: "EG_update", Status: livekit.EgressStatus_EGRESS_FAILED}},
	} {
		f.appendWebhook(ev)
	}
	f.store("recording-worker")
	rows, err := f.pool.Query(context.Background(), "SELECT source_id,call_id::text,detail::text FROM issue_occurrences WHERE project_id=$1 AND issue_id='recording-lost' ORDER BY source_id", f.project)
	if err != nil {
		t.Fatal(err)
	}
	defer rows.Close()
	count := 0
	for rows.Next() {
		var id, detail string
		var call *string
		if err := rows.Scan(&id, &call, &detail); err != nil {
			t.Fatal(err)
		}
		count++
		if strings.Contains(detail, "provider-secret") {
			t.Fatal("raw provider error leaked")
		}
		switch id {
		case "webhook:EV_failed":
			if call == nil {
				t.Fatal("recording call not linked")
			}
		case "webhook:EV_limit":
			if call != nil || !strings.Contains(detail, "cut short") {
				t.Fatalf("unlinked limit: %v %s", call, detail)
			}
		default:
			t.Fatalf("unexpected occurrence: %s", id)
		}
	}
	if err := rows.Err(); err != nil {
		t.Fatal(err)
	}
	if count != 2 {
		t.Fatalf("got %d findings", count)
	}
}
