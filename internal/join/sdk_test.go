package join_test

import (
	"context"
	"encoding/json"
	"testing"

	"github.com/mahimailabs/voicegateway/internal/event"
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
