package event_test

import (
	"testing"

	"github.com/mahimailabs/voicegateway/internal/event"
	"github.com/mahimailabs/voicegateway/sdk/contract"
)

// An SDK batch names its call by room SID and job id, so a refused quota can still let an
// already-admitted call continue.
func TestSDKBatchCallIdentities(t *testing.T) {
	for name, want := range map[string]event.CallIdentity{
		"full-call.json":       {RoomSID: "RM_fixture0001", JobID: "AJ_fixture0001"},
		"fields-job-only.json": {JobID: "AJ_fixture0002"},
	} {
		b, err := contract.Examples.ReadFile("examples/valid/" + name)
		if err != nil {
			t.Fatal(err)
		}
		got, err := event.CallIdentities(event.KindSDKEvents, b)
		if err != nil || len(got) != 1 || got[0] != want {
			t.Errorf("%s: %v, %v; want %v", name, got, err, want)
		}
	}
}
