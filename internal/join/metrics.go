package join

import (
	"cmp"
	"context"
	"slices"
	"strings"

	"go.opentelemetry.io/collector/pdata/pmetric"
	"go.opentelemetry.io/collector/pdata/pmetric/pmetricotlp"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// usagePrefix names the LiveKit Agents usage counters stored as points
// (livekit/agents/telemetry/otel_metrics.py in 1.8.7).
const usagePrefix = "lk.agents.usage."

// metrics decodes an OTLP metric export and stores every data point of an lk.agents.usage.* sum,
// each joined by its own room_id, else job_id. One export can carry points of two jobs of the same
// process, so the export's own row names no call; the points do. An export it cannot decode is
// stored as before, with no points, since metric exports are not counted as unmatched.
func (j *Joiner) metrics(ctx context.Context, r *store.Event) error {
	req := pmetricotlp.NewExportRequest()
	if err := req.UnmarshalProto(r.Payload); err != nil {
		return nil //nolint:nilerr // an undecodable export is still stored whole; it just has no points
	}
	r.MetricPoints = usagePoints(req.Metrics())

	byRef := map[ref][]int{}
	var refs []ref
	for i, p := range r.MetricPoints {
		k := pointRef(p)
		if k == (ref{}) {
			continue
		}
		if _, ok := byRef[k]; !ok {
			refs = append(refs, k)
		}
		byRef[k] = append(byRef[k], i)
	}
	// Room SIDs first, as for spans, so a job-only point finds the call its room opened rather than
	// opening a second one.
	slices.SortStableFunc(refs, func(a, b ref) int {
		return cmp.Compare(boolInt(a.roomSID == ""), boolInt(b.roomSID == ""))
	})
	for _, k := range refs {
		idx := byRef[k]
		items := make([]window, len(idx))
		for n, i := range idx {
			items[n] = window{r.MetricPoints[i].Start, r.MetricPoints[i].End}
		}
		callID, joined, err := j.joinRef(ctx, r, k, items, "metric points")
		if err != nil {
			return err
		}
		for n, i := range idx {
			if joined[n] {
				r.MetricPoints[i].CallID = callID
			}
		}
	}
	return nil
}

// pointRef is a point's own room SID, with its job id, else its job id alone.
func pointRef(p store.MetricPoint) ref {
	if p.RoomID != "" {
		return ref{roomSID: p.RoomID, jobID: p.JobID}
	}
	return ref{jobID: p.JobID}
}

// usagePoints flattens the lk.agents.usage.* sums of an export into points. Resource attributes are
// never read for ids.
func usagePoints(md pmetric.Metrics) []store.MetricPoint {
	var out []store.MetricPoint
	rms := md.ResourceMetrics()
	for i := range rms.Len() {
		sms := rms.At(i).ScopeMetrics()
		for k := range sms.Len() {
			ms := sms.At(k).Metrics()
			for m := range ms.Len() {
				mt := ms.At(m)
				// Only delta points add up. A cumulative sum (another SDK, a collector in between)
				// repeats its running total in every export and would be counted many times.
				if mt.Type() != pmetric.MetricTypeSum || !strings.HasPrefix(mt.Name(), usagePrefix) ||
					mt.Sum().AggregationTemporality() != pmetric.AggregationTemporalityDelta {
					continue
				}
				dps := mt.Sum().DataPoints()
				for d := range dps.Len() {
					dp := dps.At(d)
					var v float64
					switch dp.ValueType() {
					case pmetric.NumberDataPointValueTypeDouble:
						v = dp.DoubleValue()
					case pmetric.NumberDataPointValueTypeInt:
						v = float64(dp.IntValue())
					default:
						continue // no value: nothing was measured
					}
					attrs := attrMap(dp.Attributes())
					out = append(out, store.MetricPoint{
						Name:       mt.Name(),
						Value:      v,
						Start:      timestamp(dp.StartTimestamp()),
						End:        timestamp(dp.Timestamp()),
						RoomID:     attrs[attrRoomID],
						JobID:      attrs[attrJobID],
						Attributes: attrs,
					})
				}
			}
		}
	}
	return out
}
