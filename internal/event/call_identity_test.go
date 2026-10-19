package event

import (
	"testing"

	"go.opentelemetry.io/collector/pdata/pmetric/pmetricotlp"
	"go.opentelemetry.io/collector/pdata/ptrace/ptraceotlp"
)

func TestCallIdentitiesInspectEverySpan(t *testing.T) {
	req := ptraceotlp.NewExportRequest()
	spans := req.Traces().ResourceSpans().AppendEmpty().ScopeSpans().AppendEmpty().Spans()
	spans.AppendEmpty().Attributes().PutStr("room_id", "RM_old")
	spans.AppendEmpty().Attributes().PutStr("room_id", "RM_new")
	body, err := req.MarshalProto()
	if err != nil {
		t.Fatal(err)
	}
	refs, err := CallIdentities(KindAgentTraces, body)
	if err != nil || len(refs) != 2 || refs[1].RoomSID != "RM_new" {
		t.Fatalf("mixed identities %+v %v", refs, err)
	}
	spans.AppendEmpty()
	body, err = req.MarshalProto()
	if err != nil {
		t.Fatal(err)
	}
	if _, err := CallIdentities(KindAgentTraces, body); err == nil {
		t.Fatal("unidentified span admitted")
	}
}

func TestMetricIdentityIsPerPoint(t *testing.T) {
	req := pmetricotlp.NewExportRequest()
	resource := req.Metrics().ResourceMetrics().AppendEmpty()
	resource.Resource().Attributes().PutStr("room_id", "RM_old")
	points := resource.ScopeMetrics().AppendEmpty().Metrics().AppendEmpty().SetEmptySum().DataPoints()
	points.AppendEmpty().Attributes().PutStr("room_id", "RM_new")
	body, err := req.MarshalProto()
	if err != nil {
		t.Fatal(err)
	}
	refs, err := CallIdentities(KindAgentMetrics, body)
	if err != nil || len(refs) != 1 || refs[0].RoomSID != "RM_new" {
		t.Fatalf("point identity %+v %v", refs, err)
	}
}
