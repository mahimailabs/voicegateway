package join_test

import (
	"context"
	"encoding/json"
	"fmt"
	"testing"

	"github.com/mahimailabs/voicegateway/internal/event"
	"github.com/mahimailabs/voicegateway/internal/store"
	"github.com/mahimailabs/voicegateway/sdk/contract"
)

func sdkExample(t *testing.T, name string) []byte {
	t.Helper()
	b, err := contract.Examples.ReadFile("examples/valid/" + name)
	if err != nil {
		t.Fatal(err)
	}
	return b
}

func (f *fixture) fields(callID string) map[string]string {
	f.t.Helper()
	var raw []byte
	if err := f.pool.QueryRow(context.Background(), `SELECT fields FROM calls WHERE id = $1`, callID).Scan(&raw); err != nil {
		f.t.Fatal(err)
	}
	out := map[string]string{}
	if err := json.Unmarshal(raw, &out); err != nil {
		f.t.Fatal(err)
	}
	return out
}

// docs/specs/python-sdk.md, engine items 3 and 4: an SDK batch opens or joins its call by room SID,
// else by job id, its fields land on the call, a redelivered batch is stored once, and a call with
// only SDK data reads back with turns and live state.
func TestSDKBatchJoinsCall(t *testing.T) {
	f := newFixture(t)
	full := sdkExample(t, "full-call.json")
	f.append(event.KindSDKEvents, "sdk:b-2026-10-21-full-call-0001", full)
	f.append(event.KindSDKEvents, "sdk:b-2026-10-21-full-call-0001", full)
	f.append(event.KindSDKEvents, "sdk:b-2026-10-21-job-only-0001", sdkExample(t, "fields-job-only.json"))
	f.store("w1")

	calls := f.calls()
	room, job := calls["RM_fixture0001"], calls["job:AJ_fixture0002"]
	if len(calls) != 2 || room.ID == "" || job.ID == "" || room.JobID != "AJ_fixture0001" {
		t.Fatalf("calls = %+v, want one by room SID and one by job id", calls)
	}
	if got := f.fields(room.ID); got["customer"] != "acme" || got["plan"] != "trial" || len(got) != 2 {
		t.Errorf("room call fields = %v", got)
	}
	if got := f.fields(job.ID); got["experiment"] != "b" || len(got) != 1 {
		t.Errorf("job call fields = %v", got)
	}
	events, n := f.eventCalls()
	if n != 2 || events["sdk:b-2026-10-21-full-call-0001"] != room.ID {
		t.Errorf("stored %d events %v; want the redelivered batch once, joined to the room call", n, events)
	}

	_, res, err := f.events.CallTrace(context.Background(), "default", f.project, room.ID)
	if err != nil {
		t.Fatal(err)
	}
	if len(res.Turns) != 1 || res.Turns[0].AgentText == nil || res.Live == nil || res.Live.Agent == nil {
		t.Fatalf("turns %d, live %+v; want one SDK turn and the agent's state", len(res.Turns), res.Live)
	}
}

// A call holds at most store.MaxCallFields custom fields across its batches: new keys past the cap
// are dropped and the batch is still stored; a key the call already has still takes a new value.
func TestSDKCallFieldsCapped(t *testing.T) {
	f := newFixture(t)
	batch := func(n int, fields map[string]string) []byte {
		b, err := json.Marshal(map[string]any{
			"version": 1, "batchId": fmt.Sprintf("b-cap-%04d", n),
			"sdk": map[string]string{"name": "test", "version": "1"}, "framework": map[string]string{"name": "test", "version": "1"},
			"call":   map[string]any{"roomSid": "RM_cap", "roomName": "cap", "jobId": nil},
			"events": []map[string]any{{"type": "fields", "at": "2026-10-21T03:00:00Z", "data": map[string]any{"fields": fields}}},
		})
		if err != nil {
			t.Fatal(err)
		}
		return b
	}
	for n := range 3 {
		fields := map[string]string{}
		for k := range 30 {
			fields[fmt.Sprintf("k%02d", n*30+k)] = "v"
		}
		f.append(event.KindSDKEvents, fmt.Sprintf("sdk:b-cap-%04d", n), batch(n, fields))
	}
	f.append(event.KindSDKEvents, "sdk:b-cap-0003", batch(3, map[string]string{"k00": "changed", "k89": "new"}))
	f.store("w1")

	got := f.fields(f.calls()["RM_cap"].ID)
	if len(got) != store.MaxCallFields {
		t.Fatalf("call holds %d fields, want the cap %d", len(got), store.MaxCallFields)
	}
	if got["k00"] != "changed" || got["k63"] != "v" {
		t.Errorf("k00 = %q, k63 = %q; want an existing key updated at the cap and the first 64 kept", got["k00"], got["k63"])
	}
	if _, ok := got["k64"]; ok {
		t.Error("a key past the cap was kept")
	}
	if _, ok := got["k89"]; ok {
		t.Error("a new key past the cap was kept")
	}
	if _, n := f.eventCalls(); n != 4 {
		t.Errorf("stored %d batches, want all 4", n)
	}
}
