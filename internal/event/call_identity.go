package event

import (
	"cmp"
	"encoding/json"
	"errors"

	"github.com/livekit/protocol/livekit"
	"go.opentelemetry.io/collector/pdata/pcommon"
	"go.opentelemetry.io/collector/pdata/plog/plogotlp"
	"go.opentelemetry.io/collector/pdata/pmetric"
	"go.opentelemetry.io/collector/pdata/pmetric/pmetricotlp"
	"go.opentelemetry.io/collector/pdata/ptrace/ptraceotlp"
	"google.golang.org/protobuf/encoding/protojson"
)

// CallIdentity uses the same room-first identity as the worker joiner. Room names are not IDs.
type CallIdentity struct{ RoomSID, JobID string }

// CallIdentities reads every record, not just the first record in a mixed-job export.
// Unattributed records cannot use the existing-call exception to a quota refusal.
// LiveKit Agents stamps room_id/job_id on individual metric points because its meter
// provider outlives jobs (agents/telemetry/traces.py and otel_metrics.py).
func CallIdentities(kind Kind, body []byte) ([]CallIdentity, error) {
	var refs []CallIdentity
	add := func(a pcommon.Map) {
		get := func(k string) string {
			v, ok := a.Get(k)
			if !ok || v.Type() != pcommon.ValueTypeStr {
				return ""
			}
			return v.Str()
		}
		refs = append(refs, CallIdentity{cmp.Or(get("room_id"), get("lk.room_sid")), cmp.Or(get("job_id"), get("lk.job_id"))})
	}
	switch kind {
	case KindLiveKitWebhook:
		var e livekit.WebhookEvent
		if err := (protojson.UnmarshalOptions{DiscardUnknown: true}).Unmarshal(body, &e); err != nil {
			return nil, err
		}
		refs = append(refs, CallIdentity{RoomSID: cmp.Or(e.GetRoom().GetSid(), e.GetEgressInfo().GetRoomId(), e.GetJob().GetRoom().GetSid()), JobID: e.GetJob().GetId()})
	case KindAgentSessionReport:
		var r SessionReport
		var h livekit.MetricsRecordingHeader
		if err := json.Unmarshal(body, &r); err != nil {
			return nil, err
		}
		if err := (protojson.UnmarshalOptions{DiscardUnknown: true}).Unmarshal(r.Header, &h); err != nil {
			return nil, err
		}
		refs = append(refs, CallIdentity{h.GetRoomId(), h.GetJobId()})
	case KindAgentTraces:
		r := ptraceotlp.NewExportRequest()
		if err := r.UnmarshalProto(body); err != nil {
			return nil, err
		}
		resources := r.Traces().ResourceSpans()
		for i := 0; i < resources.Len(); i++ {
			scopes := resources.At(i).ScopeSpans()
			for j := 0; j < scopes.Len(); j++ {
				spans := scopes.At(j).Spans()
				for k := 0; k < spans.Len(); k++ {
					add(spans.At(k).Attributes())
				}
			}
		}
	case KindAgentLogs:
		r := plogotlp.NewExportRequest()
		if err := r.UnmarshalProto(body); err != nil {
			return nil, err
		}
		resources := r.Logs().ResourceLogs()
		for i := 0; i < resources.Len(); i++ {
			scopes := resources.At(i).ScopeLogs()
			for j := 0; j < scopes.Len(); j++ {
				records := scopes.At(j).LogRecords()
				for k := 0; k < records.Len(); k++ {
					add(records.At(k).Attributes())
				}
			}
		}
	case KindAgentMetrics:
		r := pmetricotlp.NewExportRequest()
		if err := r.UnmarshalProto(body); err != nil {
			return nil, err
		}
		resources := r.Metrics().ResourceMetrics()
		for i := 0; i < resources.Len(); i++ {
			scopes := resources.At(i).ScopeMetrics()
			for j := 0; j < scopes.Len(); j++ {
				metrics := scopes.At(j).Metrics()
				for k := 0; k < metrics.Len(); k++ {
					m := metrics.At(k)
					switch m.Type() {
					case pmetric.MetricTypeSum:
						ps := m.Sum().DataPoints()
						for n := 0; n < ps.Len(); n++ {
							add(ps.At(n).Attributes())
						}
					case pmetric.MetricTypeGauge:
						ps := m.Gauge().DataPoints()
						for n := 0; n < ps.Len(); n++ {
							add(ps.At(n).Attributes())
						}
					case pmetric.MetricTypeHistogram:
						ps := m.Histogram().DataPoints()
						for n := 0; n < ps.Len(); n++ {
							add(ps.At(n).Attributes())
						}
					case pmetric.MetricTypeExponentialHistogram:
						ps := m.ExponentialHistogram().DataPoints()
						for n := 0; n < ps.Len(); n++ {
							add(ps.At(n).Attributes())
						}
					case pmetric.MetricTypeSummary:
						ps := m.Summary().DataPoints()
						for n := 0; n < ps.Len(); n++ {
							add(ps.At(n).Attributes())
						}
					default:
						return nil, errors.New("unidentified metric")
					}
				}
			}
		}
	default:
		return nil, errors.New("unsupported telemetry kind")
	}
	if len(refs) == 0 {
		return nil, errors.New("no call identity")
	}
	seen := map[CallIdentity]bool{}
	unique := make([]CallIdentity, 0, len(refs))
	for _, r := range refs {
		if r.RoomSID == "" && r.JobID == "" {
			return nil, errors.New("unidentified telemetry record")
		}
		if !seen[r] {
			seen[r] = true
			unique = append(unique, r)
		}
	}
	return unique, nil
}
