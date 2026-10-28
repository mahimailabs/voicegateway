package main

import (
	"bytes"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"

	"go.opentelemetry.io/collector/pdata/pmetric"
	"go.opentelemetry.io/collector/pdata/pmetric/pmetricotlp"

	"github.com/mahimailabs/voicegateway/sdk/contract"
)

// The captured call every replay copies: one LiveKit Agents 1.8.7 call through livekit-server,
// as internal/api tests store it, plus the agent's metric exports and the SDK's example batch.
const (
	webhookFixture = "internal/api/testdata/full-call.webhooks.jsonl"
	spanFixture    = "internal/api/testdata/full-call.spans.jsonl"
	reportFixture  = "internal/api/testdata/full-call.session_report.json"
	sdkExample     = "examples/valid/full-call.json"
)

// metricFixtures are the two exports of one captured call; files 3 and 4 are another call's.
var metricFixtures = []string{
	"internal/join/testdata/agents-1.8.7-metrics-1.binpb",
	"internal/join/testdata/agents-1.8.7-metrics-2.binpb",
}

// fixture holds the captured call as text templates. Each group is replayed against its own
// earliest timestamp, so a template captured on another day still lands inside the replayed call.
type fixture struct {
	webhooks []string // LiveKit WebhookEvent JSON, in delivery order
	sentAt   []time.Time
	spans    []string // one span per line, the shape internal/api's fixture reads
	header   string   // MetricsRecordingHeader JSON of the session report
	chat     string   // chat_history.json of the session report
	metrics  []string // OTLP ExportMetricsServiceRequest JSON, one per export
	sdk      string   // one SDK batch

	roomSID, roomName, jobID string

	callBase, metricsBase, sdkBase time.Time
}

func loadFixture(root string) (*fixture, error) {
	f := &fixture{}
	read := func(rel string) ([]byte, error) { return os.ReadFile(filepath.Join(root, rel)) } // #nosec G304 -- fixture paths in this repo
	b, err := read(webhookFixture)
	if err != nil {
		return nil, err
	}
	for _, line := range bytes.Split(bytes.TrimSpace(b), []byte("\n")) {
		var w struct {
			Payload string `json:"payload"`
		}
		if err := json.Unmarshal(line, &w); err != nil {
			return nil, fmt.Errorf("%s: %w", webhookFixture, err)
		}
		var ev struct {
			CreatedAt string `json:"createdAt"`
		}
		if err := json.Unmarshal([]byte(w.Payload), &ev); err != nil {
			return nil, fmt.Errorf("%s: %w", webhookFixture, err)
		}
		at, _ := parseEpoch(ev.CreatedAt)
		f.webhooks = append(f.webhooks, w.Payload)
		f.sentAt = append(f.sentAt, at)
	}
	if b, err = read(spanFixture); err != nil {
		return nil, err
	}
	f.spans = strings.Split(strings.TrimSpace(string(b)), "\n")
	if b, err = read(reportFixture); err != nil {
		return nil, err
	}
	var report struct {
		Header      json.RawMessage `json:"header"`
		ChatHistory string          `json:"chat_history"`
	}
	if err := json.Unmarshal(b, &report); err != nil {
		return nil, fmt.Errorf("%s: %w", reportFixture, err)
	}
	f.header, f.chat = string(report.Header), report.ChatHistory

	var first struct {
		Room struct{ Sid, Name string }
	}
	if err := json.Unmarshal([]byte(f.webhooks[0]), &first); err != nil {
		return nil, err
	}
	f.roomSID, f.roomName = first.Room.Sid, first.Room.Name
	var header struct{ JobID string }
	if err := json.Unmarshal(report.Header, &header); err != nil {
		return nil, err
	}
	f.jobID = header.JobID

	// The metric exports and the SDK batch were captured on other calls: point them at this one.
	adopt := func(s string) string {
		s = roomSIDs.ReplaceAllString(s, f.roomSID)
		return jobIDs.ReplaceAllString(s, f.jobID)
	}
	for _, p := range metricFixtures {
		b, err := read(p)
		if err != nil {
			return nil, err
		}
		req := pmetricotlp.NewExportRequest()
		if err := req.UnmarshalProto(b); err != nil {
			return nil, fmt.Errorf("%s: %w", p, err)
		}
		j, err := req.MarshalJSON()
		if err != nil {
			return nil, err
		}
		f.metrics = append(f.metrics, adopt(string(j)))
	}
	if b, err = contract.Examples.ReadFile(sdkExample); err != nil {
		return nil, err
	}
	var batch map[string]any
	if err := json.Unmarshal(b, &batch); err != nil {
		return nil, err
	}
	batch["call"] = map[string]any{"jobId": f.jobID, "roomSid": f.roomSID, "roomName": f.roomName}
	if b, err = json.Marshal(batch); err != nil {
		return nil, err
	}
	f.sdk = string(b)

	f.callBase = earliest(append(append([]string{f.header, f.chat}, f.webhooks...), f.spans...)...)
	f.metricsBase = earliest(f.metrics...)
	f.sdkBase = earliest(f.sdk)
	for name, t := range map[string]time.Time{"call": f.callBase, "metrics": f.metricsBase, "sdk": f.sdkBase} {
		if t.IsZero() {
			return nil, fmt.Errorf("fixture %s has no timestamps", name)
		}
	}
	return f, nil
}

var (
	// LiveKit ids: a short upper-case prefix, an underscore and a random base62 suffix.
	livekitIDs = regexp.MustCompile(`\b(RM|EV|AJ|PA|TR|AD|AW|A)_[A-Za-z0-9]{6,}\b`)
	roomSIDs   = regexp.MustCompile(`\bRM_[A-Za-z0-9]{6,}\b`)
	jobIDs     = regexp.MustCompile(`\bAJ_[A-Za-z0-9]{6,}\b`)
	traceIDs   = regexp.MustCompile(`"trace_id":"([0-9a-f]{32})"`)
	// Instants as the fixtures write them: RFC 3339 strings, and Unix seconds, milliseconds,
	// microseconds or nanoseconds from 2023 to 2027 (seconds may carry a fraction).
	isoTimes   = regexp.MustCompile(`\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z`)
	epochTimes = regexp.MustCompile(`\b1[789]\d{8}(?:\d{9}|\d{6}|\d{3})?(?:\.\d+)?\b`)
)

func parseEpoch(s string) (time.Time, int) {
	whole, frac, _ := strings.Cut(s, ".")
	n, err := strconv.ParseInt(whole, 10, 64)
	if err != nil {
		return time.Time{}, 0
	}
	switch len(whole) {
	case 10:
		t := time.Unix(n, 0)
		if frac != "" {
			f, _ := strconv.ParseFloat("0."+frac, 64)
			t = t.Add(time.Duration(f * float64(time.Second)))
		}
		return t, 10
	case 13:
		return time.UnixMilli(n), 13
	case 16:
		return time.UnixMicro(n), 16
	default:
		return time.Unix(0, n), 19
	}
}

func formatEpoch(t time.Time, digits int, fraction bool) string {
	switch digits {
	case 13:
		return strconv.FormatInt(t.UnixMilli(), 10)
	case 16:
		return strconv.FormatInt(t.UnixMicro(), 10)
	case 19:
		return strconv.FormatInt(t.UnixNano(), 10)
	}
	if fraction {
		return strconv.FormatFloat(float64(t.UnixMicro())/1e6, 'f', 6, 64)
	}
	return strconv.FormatInt(t.Unix(), 10)
}

func earliest(texts ...string) time.Time {
	var min time.Time
	keep := func(t time.Time) {
		if !t.IsZero() && (min.IsZero() || t.Before(min)) {
			min = t
		}
	}
	for _, s := range texts {
		for _, m := range isoTimes.FindAllString(s, -1) {
			t, err := time.Parse(time.RFC3339Nano, m)
			if err == nil {
				keep(t)
			}
		}
		for _, m := range epochTimes.FindAllString(s, -1) {
			t, _ := parseEpoch(m)
			keep(t)
		}
	}
	return min
}

// clock maps a captured instant onto a replayed call: the call's base instant lands at start, and
// every gap is multiplied by stretch.
type clock struct {
	base, start time.Time
	stretch     float64
}

func (c clock) at(t time.Time) time.Time {
	return c.start.Add(time.Duration(math.Round(float64(t.Sub(c.base)) * c.stretch)))
}

// shift rewrites every instant in s through c.
func (c clock) shift(s string) string {
	s = isoTimes.ReplaceAllStringFunc(s, func(m string) string {
		t, err := time.Parse(time.RFC3339Nano, m)
		if err != nil {
			return m
		}
		return c.at(t).UTC().Format(time.RFC3339Nano)
	})
	return epochTimes.ReplaceAllStringFunc(s, func(m string) string {
		t, digits := parseEpoch(m)
		if digits == 0 {
			return m
		}
		return formatEpoch(c.at(t), digits, strings.Contains(m, "."))
	})
}

// ids gives one replayed call its own LiveKit ids, room name and trace ids. Every id keeps its
// prefix and gains the run tag and call number, so a run's rows are found by prefix.
type ids struct {
	run      string
	call     int
	roomName string
}

func (d ids) rewrite(s, fixtureRoom string) string {
	s = livekitIDs.ReplaceAllStringFunc(s, func(m string) string {
		prefix, rest, _ := strings.Cut(m, "_")
		return fmt.Sprintf("%s_%s%05d%s", prefix, d.run, d.call, rest)
	})
	s = strings.ReplaceAll(s, fixtureRoom, d.roomName)
	return traceIDs.ReplaceAllStringFunc(s, func(m string) string {
		orig := traceIDs.FindStringSubmatch(m)[1]
		return fmt.Sprintf(`"trace_id":"%s"`, d.traceID(orig))
	})
}

// traceID keeps the captured trace id's last half and puts the run and call in its first.
func (d ids) traceID(orig string) string {
	return hex.EncodeToString([]byte(d.run)) + fmt.Sprintf("%08x", d.call) + orig[16:]
}

// tracePrefix is the hex every trace id of a run starts with.
func tracePrefix(run string) string { return hex.EncodeToString([]byte(run)) }

func metricsProto(j string) ([]byte, error) {
	req := pmetricotlp.NewExportRequest()
	if err := req.UnmarshalJSON([]byte(j)); err != nil {
		return nil, err
	}
	return req.MarshalProto()
}

// metricsEnd is the latest data point time in an export, the moment the agent would send it.
func metricsEnd(j string) (time.Time, error) {
	req := pmetricotlp.NewExportRequest()
	if err := req.UnmarshalJSON([]byte(j)); err != nil {
		return time.Time{}, err
	}
	var end time.Time
	rms := req.Metrics().ResourceMetrics()
	for i := range rms.Len() {
		sms := rms.At(i).ScopeMetrics()
		for j := range sms.Len() {
			ms := sms.At(j).Metrics()
			for k := range ms.Len() {
				for _, t := range pointTimes(ms.At(k)) {
					if t.After(end) {
						end = t
					}
				}
			}
		}
	}
	return end, nil
}

func pointTimes(m pmetric.Metric) []time.Time {
	var out []time.Time
	switch m.Type() {
	case pmetric.MetricTypeGauge:
		for i := range m.Gauge().DataPoints().Len() {
			out = append(out, m.Gauge().DataPoints().At(i).Timestamp().AsTime())
		}
	case pmetric.MetricTypeSum:
		for i := range m.Sum().DataPoints().Len() {
			out = append(out, m.Sum().DataPoints().At(i).Timestamp().AsTime())
		}
	case pmetric.MetricTypeHistogram:
		for i := range m.Histogram().DataPoints().Len() {
			out = append(out, m.Histogram().DataPoints().At(i).Timestamp().AsTime())
		}
	}
	return out
}
