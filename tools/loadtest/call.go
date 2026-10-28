package main

import (
	"bytes"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"mime/multipart"
	"net/textproto"
	"slices"
	"time"

	"github.com/livekit/protocol/livekit"
	"github.com/livekit/protocol/utils/protojson"
	"go.opentelemetry.io/collector/pdata/pcommon"
	"go.opentelemetry.io/collector/pdata/ptrace"
	"go.opentelemetry.io/collector/pdata/ptrace/ptraceotlp"
	"google.golang.org/protobuf/proto"
)

// The uploads of one call, each sent at its moment.
type kind string

const (
	kindWebhook kind = "webhook"
	kindTraces  kind = "traces"
	kindMetrics kind = "metrics"
	kindReport  kind = "session_report"
	kindSDK     kind = "sdk"
)

type step struct {
	at      time.Time
	kind    kind
	webhook *livekit.WebhookEvent
	body    []byte
	ctype   string // Content-Type of body
	spans   int    // spans in a traces body
}

// spanBatch is how often the agent's batch span processor exports: OpenTelemetry's default
// schedule delay, which LiveKit Agents keeps.
const spanBatch = 5 * time.Second

// plan builds call n of a run: the captured call with fresh ids, its instants moved to start and
// its gaps stretched, as a list of uploads in send order.
func (f *fixture) plan(run string, n int, start time.Time, stretch float64, withSDK bool) ([]step, error) {
	d := ids{run: run, call: n, roomName: fmt.Sprintf("vg-load-%s-%05d", run, n)}
	call := clock{base: f.callBase, start: start, stretch: stretch}
	rw := func(s string) string { return call.shift(d.rewrite(s, f.roomName)) }

	var steps []step
	for i, raw := range f.webhooks {
		var ev livekit.WebhookEvent
		if err := (protojson.UnmarshalOptions{DiscardUnknown: true}).Unmarshal([]byte(rw(raw)), &ev); err != nil {
			return nil, fmt.Errorf("webhook: %w", err)
		}
		// Sent at the captured moment moved onto this call, not at the rewritten createdAt: that has
		// whole seconds only, and would send every call's webhooks of one second at the same instant.
		steps = append(steps, step{at: call.at(f.sentAt[i]), kind: kindWebhook, webhook: &ev})
	}

	traces, last, err := f.traceBatches(rw)
	if err != nil {
		return nil, err
	}
	steps = append(steps, traces...)

	header := &livekit.MetricsRecordingHeader{}
	if err := (protojson.UnmarshalOptions{DiscardUnknown: true}).Unmarshal([]byte(rw(f.header)), header); err != nil {
		return nil, fmt.Errorf("report header: %w", err)
	}
	report, ctype, err := sessionReport(header, rw(f.chat))
	if err != nil {
		return nil, err
	}
	// The agent uploads its report when the job shuts down, after its last span.
	steps = append(steps, step{at: last.Add(time.Second), kind: kindReport, body: report, ctype: ctype})

	// Metric exports and the SDK batch were captured elsewhere; they start a second into the call.
	metrics := clock{base: f.metricsBase, start: start.Add(time.Second), stretch: stretch}
	for _, m := range f.metrics {
		j := metrics.shift(d.rewrite(m, f.roomName))
		body, err := metricsProto(j)
		if err != nil {
			return nil, fmt.Errorf("metrics: %w", err)
		}
		end, err := metricsEnd(j)
		if err != nil {
			return nil, err
		}
		steps = append(steps, step{at: end, kind: kindMetrics, body: body, ctype: "application/x-protobuf"})
	}
	if withSDK {
		sdk := clock{base: f.sdkBase, start: start.Add(time.Second), stretch: stretch}
		var batch map[string]any
		if err := json.Unmarshal([]byte(sdk.shift(d.rewrite(f.sdk, f.roomName))), &batch); err != nil {
			return nil, err
		}
		batch["batchId"] = fmt.Sprintf("load-%s-%05d", run, n)
		body, err := json.Marshal(batch)
		if err != nil {
			return nil, err
		}
		// The SDK flushes its batch when the session closes.
		steps = append(steps, step{at: last.Add(time.Second), kind: kindSDK, body: body, ctype: "application/json"})
	}
	slices.SortStableFunc(steps, func(a, b step) int { return a.at.Compare(b.at) })
	return steps, nil
}

// traceBatches groups the call's spans the way the batch span processor exports them: every
// spanBatch, the spans that ended since the last export. It returns the last span's end too.
func (f *fixture) traceBatches(rw func(string) string) ([]step, time.Time, error) {
	type row struct {
		TraceID    string            `json:"trace_id"`
		SpanID     string            `json:"span_id"`
		ParentID   string            `json:"parent_span_id"`
		Name       string            `json:"name"`
		Start      time.Time         `json:"start"`
		End        time.Time         `json:"end"`
		ScopeName  string            `json:"scope_name"`
		Attributes map[string]string `json:"attributes"`
	}
	rows := make([]row, 0, len(f.spans))
	var first, last time.Time
	for _, line := range f.spans {
		var r row
		if err := json.Unmarshal([]byte(rw(line)), &r); err != nil {
			return nil, time.Time{}, fmt.Errorf("span: %w", err)
		}
		rows = append(rows, r)
		if first.IsZero() || r.Start.Before(first) {
			first = r.Start
		}
		if r.End.After(last) {
			last = r.End
		}
	}
	batches := map[int][]row{}
	for _, r := range rows {
		i := int(r.End.Sub(first) / spanBatch)
		batches[i] = append(batches[i], r)
	}
	var steps []step
	for i, rs := range batches {
		td := ptrace.NewTraces()
		ss := td.ResourceSpans().AppendEmpty().ScopeSpans().AppendEmpty()
		ss.Scope().SetName("livekit-agents")
		for _, r := range rs {
			sp := ss.Spans().AppendEmpty()
			var tid pcommon.TraceID
			var sid, pid pcommon.SpanID
			if _, err := hex.Decode(tid[:], []byte(r.TraceID)); err != nil {
				return nil, time.Time{}, err
			}
			if _, err := hex.Decode(sid[:], []byte(r.SpanID)); err != nil {
				return nil, time.Time{}, err
			}
			sp.SetTraceID(tid)
			sp.SetSpanID(sid)
			if r.ParentID != "" {
				if _, err := hex.Decode(pid[:], []byte(r.ParentID)); err != nil {
					return nil, time.Time{}, err
				}
				sp.SetParentSpanID(pid)
			}
			sp.SetName(r.Name)
			sp.SetStartTimestamp(pcommon.NewTimestampFromTime(r.Start))
			sp.SetEndTimestamp(pcommon.NewTimestampFromTime(r.End))
			for k, v := range r.Attributes {
				sp.Attributes().PutStr(k, v)
			}
		}
		body, err := ptraceotlp.NewExportRequestFromTraces(td).MarshalProto()
		if err != nil {
			return nil, time.Time{}, err
		}
		at := first.Add(time.Duration(i+1) * spanBatch)
		if at.After(last) {
			at = last
		}
		steps = append(steps, step{at: at, kind: kindTraces, body: body, ctype: "application/x-protobuf", spans: len(rs)})
	}
	return steps, last, nil
}

// sessionReport encodes the multipart upload LiveKit Agents sends when a job ends: the recording
// header and the chat history, with no audio (record={"audio": False}).
func sessionReport(header *livekit.MetricsRecordingHeader, chat string) ([]byte, string, error) {
	hb, err := proto.Marshal(header)
	if err != nil {
		return nil, "", err
	}
	var buf bytes.Buffer
	mw := multipart.NewWriter(&buf)
	parts := []struct {
		name, file, ctype string
		body              []byte
	}{
		{"header", "header.binpb", "application/protobuf", hb},
		{"chat_history", "chat_history.json", "application/json", []byte(chat)},
	}
	for _, p := range parts {
		h := textproto.MIMEHeader{}
		h.Set("Content-Disposition", fmt.Sprintf(`form-data; name=%q; filename=%q`, p.name, p.file))
		h.Set("Content-Type", p.ctype)
		w, err := mw.CreatePart(h)
		if err != nil {
			return nil, "", err
		}
		if _, err := w.Write(p.body); err != nil {
			return nil, "", err
		}
	}
	if err := mw.Close(); err != nil {
		return nil, "", err
	}
	return buf.Bytes(), mw.FormDataContentType(), nil
}
