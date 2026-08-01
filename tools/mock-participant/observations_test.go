package main

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"regexp"
	"strings"
	"sync"
	"testing"
	"time"
)

type capturedPost struct {
	mu     sync.Mutex
	path   string
	auth   string
	ctype  string
	body   map[string]any
	status int
	count  int
}

func (c *capturedPost) server(t *testing.T) *httptest.Server {
	t.Helper()
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		c.mu.Lock()
		defer c.mu.Unlock()
		c.count++
		c.path = r.URL.Path
		c.auth = r.Header.Get("Authorization")
		c.ctype = r.Header.Get("Content-Type")
		_ = json.NewDecoder(r.Body).Decode(&c.body)
		code := c.status
		if code == 0 {
			code = http.StatusAccepted
		}
		w.WriteHeader(code)
	}))
	t.Cleanup(s.Close)
	return s
}

func (c *capturedPost) seen() map[string]any {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.body
}

func statsWith(joined, firstPacket time.Time) *CallStats {
	s := &CallStats{}
	if !joined.IsZero() {
		s.markJoined(joined)
	}
	if !firstPacket.IsZero() {
		s.firstPacketNanos.Store(firstPacket.UnixNano())
	}
	return s
}

func TestReportingIsOffByDefault(t *testing.T) {
	// A load run nobody is collecting is a legitimate way to use this tool, and
	// a worker that fails without a collector would be useless for it.
	r := &Reporter{}
	if r.Enabled() {
		t.Fatal("reporting enabled with no collector url")
	}
	if err := r.Report(context.Background(), Assignment{}, &CallStats{}, "P_1", ""); err != nil {
		t.Fatalf("disabled reporter errored: %v", err)
	}
}

func TestTheObservationGoesToTheRightPathWithTheWriteScopeToken(t *testing.T) {
	cap := &capturedPost{}
	srv := cap.server(t)
	r := &Reporter{BaseURL: srv.URL, APIKey: "vk_test", Client: srv.Client()}

	now := time.Now()
	stats := statsWith(now, now.Add(120*time.Millisecond))
	err := r.Report(context.Background(),
		Assignment{JobID: "JOB_1", RoomName: "loadtest"}, stats, "PA_abc", "run-7")
	if err != nil {
		t.Fatalf("Report: %v", err)
	}
	if cap.path != "/v1/calls/observations" {
		t.Errorf("path = %q, want /v1/calls/observations", cap.path)
	}
	if cap.auth != "Bearer vk_test" {
		t.Errorf("auth = %q; /v1/calls requires the write scope", cap.auth)
	}
	if !strings.HasPrefix(cap.ctype, "application/json") {
		t.Errorf("content-type = %q", cap.ctype)
	}
}

func TestTheOriginIsLoadgenAndNeverWebhook(t *testing.T) {
	// The endpoint accepts agent | loadgen and REFUSES webhook, because only the
	// signature-verified receiver may claim to be one.
	cap := &capturedPost{}
	srv := cap.server(t)
	r := &Reporter{BaseURL: srv.URL, Client: srv.Client()}
	now := time.Now()
	if err := r.Report(context.Background(), Assignment{RoomName: "x"},
		statsWith(now, now), "P", ""); err != nil {
		t.Fatalf("Report: %v", err)
	}
	if got := cap.seen()["origin"]; got != "loadgen" {
		t.Errorf("origin = %v, want loadgen", got)
	}
}

func TestUnmeasuredTimestampsAreOmittedNotSentAsZero(t *testing.T) {
	// A zero here would be an epoch timestamp in 1970 arriving as a
	// measurement. A call that joined and never received audio must show the
	// join and no first-audio time.
	cap := &capturedPost{}
	srv := cap.server(t)
	r := &Reporter{BaseURL: srv.URL, Client: srv.Client()}
	joined := time.Now()
	if err := r.Report(context.Background(), Assignment{RoomName: "x"},
		statsWith(joined, time.Time{}), "P_1", ""); err != nil {
		t.Fatalf("Report: %v", err)
	}
	legs, _ := cap.seen()["legs"].([]any)
	if len(legs) != 1 {
		t.Fatalf("legs = %v", cap.seen()["legs"])
	}
	leg, _ := legs[0].(map[string]any)
	if _, present := leg["first_audio_track_at_ms"]; present {
		t.Error("first_audio_track_at_ms was sent for a call that received no audio")
	}
	if _, present := leg["joined_at_ms"]; !present {
		t.Error("joined_at_ms was omitted for a call that did join")
	}
}

func TestTheFirstPacketBecomesTheFirstAudioTrackTime(t *testing.T) {
	// This is one of the two inputs the server derives answer latency from, so
	// it is the most valuable field this worker can supply.
	cap := &capturedPost{}
	srv := cap.server(t)
	r := &Reporter{BaseURL: srv.URL, Client: srv.Client()}
	joined := time.Now()
	first := joined.Add(87 * time.Millisecond)
	if err := r.Report(context.Background(), Assignment{RoomName: "x"},
		statsWith(joined, first), "P_1", ""); err != nil {
		t.Fatalf("Report: %v", err)
	}
	legs, _ := cap.seen()["legs"].([]any)
	leg, _ := legs[0].(map[string]any)
	got, _ := leg["first_audio_track_at_ms"].(float64)
	if int64(got) != first.UnixMilli() {
		t.Errorf("first_audio_track_at_ms = %v, want %d", got, first.UnixMilli())
	}
	if leg["kind"] != "AGENT" {
		t.Errorf("kind = %v, want AGENT (a closed Literal in the python model)", leg["kind"])
	}
}

func TestA422SaysTheShapeDisagreesRatherThanJustFailing(t *testing.T) {
	// extra="forbid" means a field mismatch rejects the WHOLE report. Without a
	// specific message this shows up only as an empty report at the end of a run.
	cap := &capturedPost{status: http.StatusUnprocessableEntity}
	srv := cap.server(t)
	r := &Reporter{BaseURL: srv.URL, Client: srv.Client()}
	err := r.Report(context.Background(), Assignment{}, &CallStats{}, "P", "")
	if err == nil {
		t.Fatal("a 422 was not reported as an error")
	}
	if !strings.Contains(err.Error(), "extra=forbid") {
		t.Errorf("error does not explain the 422: %v", err)
	}
}

func TestAnAuthFailureNamesTheScopeItNeeds(t *testing.T) {
	for _, code := range []int{http.StatusUnauthorized, http.StatusForbidden} {
		cap := &capturedPost{status: code}
		srv := cap.server(t)
		r := &Reporter{BaseURL: srv.URL, Client: srv.Client()}
		err := r.Report(context.Background(), Assignment{}, &CallStats{}, "P", "")
		if err == nil || !strings.Contains(err.Error(), "write scope") {
			t.Errorf("%d: error does not name the scope: %v", code, err)
		}
	}
}

func TestTheRunIDTravelsSoOneRunsCallsCanBeFoundTogether(t *testing.T) {
	cap := &capturedPost{}
	srv := cap.server(t)
	r := &Reporter{BaseURL: srv.URL, Client: srv.Client()}
	now := time.Now()
	if err := r.Report(context.Background(), Assignment{RoomName: "x"},
		statsWith(now, now), "P", "ramp-500"); err != nil {
		t.Fatalf("Report: %v", err)
	}
	if got := cap.seen()["run_id"]; got != "ramp-500" {
		t.Errorf("run_id = %v, want ramp-500", got)
	}
}

// TestEveryFieldWeSendExistsOnThePythonModel is the one that matters.
//
// CallObservation and LegObservation set extra="forbid", so a field this Go
// struct sends that the Python model does not declare is a 422 for the whole
// report, and a load run would finish having filed nothing. Rather than trust
// that the two stayed in step, this reads the field names straight out of the
// Python source and checks every json tag against them.
func TestEveryFieldWeSendExistsOnThePythonModel(t *testing.T) {
	const rel = "../../src/voicegateway/server/api/call_observations.py"
	source, err := os.ReadFile(rel)
	if err != nil {
		t.Skipf("python model not readable from here: %v", err)
	}
	declared := map[string]bool{}
	// Field lines look like `    room_name: _Id | None = None`.
	re := regexp.MustCompile(`(?m)^\s{4}([a-z_][a-z0-9_]*)\s*:`)
	for _, m := range re.FindAllStringSubmatch(string(source), -1) {
		declared[m[1]] = true
	}
	if len(declared) < 10 {
		t.Fatalf("only found %d field names; the parser is wrong, not the code", len(declared))
	}

	cap := &capturedPost{}
	srv := cap.server(t)
	r := &Reporter{BaseURL: srv.URL, Project: "p", Client: srv.Client()}
	now := time.Now()
	// Every optional field populated, so the check sees the widest payload.
	stats := statsWith(now, now.Add(time.Millisecond))
	if err := r.Report(context.Background(),
		Assignment{JobID: "J", RoomName: "r"}, stats, "P_1", "run"); err != nil {
		t.Fatalf("Report: %v", err)
	}

	body := cap.seen()
	for key := range body {
		if key == "legs" {
			continue
		}
		if !declared[key] {
			t.Errorf("CallObservation has no field %q: extra=forbid makes this a 422", key)
		}
	}
	legs, _ := body["legs"].([]any)
	if len(legs) == 0 {
		t.Fatal("no leg was sent")
	}
	leg, _ := legs[0].(map[string]any)
	for key := range leg {
		if !declared[key] {
			t.Errorf("LegObservation has no field %q: extra=forbid makes this a 422", key)
		}
	}
}
