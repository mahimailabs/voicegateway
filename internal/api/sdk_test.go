package api

import (
	"net/http"
	"net/url"
	"strings"
	"testing"

	"github.com/mahimailabs/voicegateway/internal/event"
	sdkcontract "github.com/mahimailabs/voicegateway/sdk/contract"
)

// docs/specs/python-sdk.md, engine item 5: a call with only SDK data reads back with its fields,
// live state and turns, against the contract, and listCalls filters by field.
func TestSDKFieldsLiveAndFilter(t *testing.T) {
	f := newFixture(t)
	full, err := sdkcontract.Examples.ReadFile("examples/valid/full-call.json")
	if err != nil {
		t.Fatal(err)
	}
	other := strings.NewReplacer("RM_fixture0001", "RM_fixture0009", "AJ_fixture0001", "AJ_fixture0009",
		`"plan": "trial"`, `"plan": "pro"`, "full-call-0001", "full-call-0009").Replace(string(full))
	f.append(event.KindSDKEvents, "sdk:full-call-0001", full)
	f.append(event.KindSDKEvents, "sdk:full-call-0009", []byte(other))
	f.store()
	f.summarize()

	var c call
	if code := f.get(f.api(nil), "/api/v1/org/default/project/"+f.project+"/calls/"+f.callID("RM_fixture0001"), &c); code != http.StatusOK {
		t.Fatalf("getCall: %d", code)
	}
	if c.Fields["customer"] != "acme" || c.Fields["plan"] != "trial" {
		t.Errorf("fields = %v", c.Fields)
	}
	if c.Live == nil || c.Live.Agent == nil || c.Live.Agent.State != "listening" || c.Live.User == nil {
		t.Errorf("live = %+v", c.Live)
	}
	if c.Turns == nil || len(*c.Turns) != 1 {
		t.Errorf("turns = %v, want one built from the SDK's messages", c.Turns)
	}

	for _, tc := range []struct {
		fields []string
		want   int
	}{
		{nil, 2},
		{[]string{"plan:trial"}, 1},
		{[]string{"plan:pro", "customer:acme"}, 1},
		{[]string{"plan:pro", "plan:trial"}, 0},
		{[]string{"plan:enterprise"}, 0},
	} {
		p, code := f.list(url.Values{"field": tc.fields})
		if code != http.StatusOK || len(p.Items) != tc.want || p.Totals.CallCount == nil || *p.Totals.CallCount != int64(tc.want) {
			t.Errorf("field=%v: status %d, %d items, %v total; want %d", tc.fields, code, len(p.Items), p.Totals.CallCount, tc.want)
		}
	}
	if _, code := f.list(url.Values{"field": {"Not A Key:x"}}); code != http.StatusBadRequest {
		t.Errorf("bad field key: status %d, want 400", code)
	}
}
