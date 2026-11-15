package capacity

import (
	"encoding/json"
	"io"
	"log/slog"
	"strings"
	"testing"
	"testing/synctest"
	"time"

	"github.com/mahimailabs/voicegateway/internal/store"
)

func TestTailOf(t *testing.T) {
	tests := []struct {
		name                     string
		in                       []float64
		median, p95, share, slow float64
	}{
		{name: "one answer", in: []float64{0.6}, median: 0.6, p95: 0.6, share: 0, slow: 0.6},
		{name: "the slow end shows past the median", in: []float64{0.5, 0.6, 0.6, 0.6, 0.7, 0.6, 0.6, 0.6, 0.6, 0.6, 0.6, 0.6, 0.6, 0.6, 0.6, 0.6, 0.6, 0.6, 1.4, 2.7}, median: 0.6, p95: 1.4, share: 0.1, slow: 2.7},
		{name: "exactly one second is not slow", in: []float64{1, 1}, median: 1, p95: 1, share: 0, slow: 1},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := TailOf(tt.in)
			if *got.Median != tt.median || *got.P95 != tt.p95 || *got.SlowShare != tt.share || *got.Slowest != tt.slow || got.Count != len(tt.in) {
				t.Errorf("TailOf = median %v p95 %v share %v slowest %v", *got.Median, *got.P95, *got.SlowShare, *got.Slowest)
			}
		})
	}
	if got := TailOf(nil); got.Median != nil || got.P95 != nil || got.SlowShare != nil || got.Slowest != nil {
		t.Errorf("no answers must be unknown, not zero: %+v", got)
	}
}

// TestReportSaysTheAgentRefused is the AWS run of 2026-11-15 (capacity 16; at 32 the worker took
// 23 calls, left 8 waiting and dropped 1). Whichever limit the run recorded, the report leads with
// what most calls saw: the agent refused new calls.
func TestReportSaysTheAgentRefused(t *testing.T) {
	for _, limit := range []string{store.LimitPending, store.LimitJobFailed} {
		t.Run(limit, func(t *testing.T) {
			sixteen := 16
			start := time.Date(2026, 11, 15, 14, 49, 37, 0, time.UTC)
			run := store.CapacityRun{
				ID: "319eabb4-2216-4d33-86cb-86de7cdecd60", Agent: "demo-runner", MaxStep: 32, StartedAt: &start,
				Capacity: &sixteen, LimitedBy: &limit,
				Steps: []store.CapacityStep{
					{Step: 16, Outcome: store.StepPassed, Calls: 16, Joined: 16, AnswerSeconds: []float64{0.6, 0.6, 1.2}},
					{Step: 32, Outcome: store.StepFailed, Calls: 32, Joined: 23, Pending: 8, Failed: 1},
				},
			}
			r := NewReport(run, "wss://lk.example.com/path?token=abc", "1.0.0-preview.12")
			if !strings.Contains(r.Verdict, "16 calls at once") || !strings.Contains(r.Verdict, "refused new calls (worker full)") {
				t.Errorf("verdict = %q", r.Verdict)
			}
			if !strings.Contains(r.Limit[0], "8 never got the agent, 1 was dropped, 23 got it") || !strings.Contains(r.Limit[1], "between 16 and 31") {
				t.Errorf("limit = %q", r.Limit)
			}
			if r.LiveKitHost == nil || *r.LiveKitHost != "lk.example.com" {
				t.Errorf("host = %v, want only the host", r.LiveKitHost)
			}
			if s := r.Steps[1]; s.Answers.Median != nil || s.SlowestJoin != nil {
				t.Errorf("a step with no answers or joins must read unknown: %+v", s)
			}
		})
	}
}

// TestReportHidesWhatItDoesNotKnow: no LiveKit host and no server size given means neither appears,
// and no servers-needed line is made up.
func TestReportHidesWhatItDoesNotKnow(t *testing.T) {
	c, limit := 16, store.LimitPending
	r := NewReport(store.CapacityRun{
		ID: "r", Agent: "a", MaxStep: 32, Capacity: &c, LimitedBy: &limit,
		Steps: []store.CapacityStep{{Step: 16, Outcome: store.StepPassed, Joined: 16}, {Step: 32, Outcome: store.StepFailed, Joined: 20, Pending: 12}},
	}, "", "dev")
	html, err := r.HTML()
	if err != nil {
		t.Fatal(err)
	}
	for _, unknown := range []string{"unknown host", "LiveKit ", " on <", "What it takes"} {
		if strings.Contains(string(html), unknown) {
			t.Errorf("the report shows %q for something it does not know", unknown)
		}
	}
	if r.LiveKitHost != nil || r.Next != nil {
		t.Errorf("host %v next %v, want both unknown", r.LiveKitHost, r.Next)
	}
}

func TestReportSetupWorksOutServers(t *testing.T) {
	c := 16
	base := NewReport(store.CapacityRun{ID: "r", Agent: "a", MaxStep: 32, Capacity: &c}, "", "dev")
	tests := []struct {
		name            string
		servers, target int
		want            []string
	}{
		{name: "one server", servers: 1, target: 100, want: []string{"For 100 calls at once: 7 servers like these"}},
		{name: "two servers", servers: 2, target: 100, want: []string{"Each server carried about 8 calls at once.", "For 100 calls at once: 13 servers like these"}},
		{name: "servers unknown", target: 40, want: []string{"For 40 calls at once: 3 times this setup"}},
		{name: "nothing given", want: nil},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			r := base.Setup("4 vCPU", tt.servers, tt.target)
			if len(r.Next) != len(tt.want) {
				t.Fatalf("next = %q, want %q", r.Next, tt.want)
			}
			for i, w := range tt.want {
				if !strings.HasPrefix(r.Next[i], w) {
					t.Errorf("next[%d] = %q, want %q", i, r.Next[i], w)
				}
			}
			html, err := r.HTML()
			if err != nil || !strings.Contains(string(html), "calls at once on 4 vCPU") {
				t.Errorf("html with a server size: %v", err)
			}
			if r.AgentServer == nil || *r.AgentServer != "4 vCPU" {
				t.Errorf("server = %v", r.AgentServer)
			}
		})
	}
}

func TestReportRefineStepsAreMarked(t *testing.T) {
	limit := store.LimitPending
	run := store.CapacityRun{ID: "0b7d8f8e-1111", Agent: "a", MaxStep: 32, LimitedBy: &limit, Steps: []store.CapacityStep{
		{Step: 16, Outcome: store.StepPassed}, {Step: 32, Outcome: store.StepFailed, Pending: 8, Calls: 32}, {Step: 24, Outcome: store.StepPassed},
	}}
	r := NewReport(run, "wss://lk", "dev")
	got := []int{}
	for _, s := range r.Ordered() {
		got = append(got, s.Calls)
	}
	if want := []int{16, 24, 32}; !slicesEqual(got, want) {
		t.Errorf("ordered = %v, want %v", got, want)
	}
	if r.Steps[2].Refine != true || r.Steps[1].Refine != false {
		t.Errorf("refine marks = %v %v", r.Steps[1].Refine, r.Steps[2].Refine)
	}
	if !strings.Contains(r.Table(), "24*") {
		t.Errorf("the table must mark the refine step:\n%s", r.Table())
	}
}

// TestLocalRunWritesReports runs the command's path end to end on a fake agent that takes 24 calls:
// the in-memory store, the refine pass, and the three files, which carry the measured-with link and
// never the key or secret.
func TestLocalRunWritesReports(t *testing.T) {
	a := &fakeAgent{jobs: 24, join: 500 * time.Millisecond, answer: steady}
	a.rooms, a.assigned, a.callers, a.done = map[string]bool{}, map[string]bool{}, map[string]chan<- Event{}, map[string]chan struct{}{}
	var local *Local
	synctest.Test(t, func(t *testing.T) {
		local = NewLocal("7c1e0a55-0000-4000-8000-000000000001", "receptionist", 32, time.Now())
		local.LiveKitURL, local.APIKey, local.APISecret, local.JoinMs = "wss://lk.example.com", "APIkeyVALUE", "secretVALUE", 2000
		r := &Runner{
			Runs: local, Projects: local, Dial: func(url, key, secret string) LiveKit {
				if key != "APIkeyVALUE" || secret != "secretVALUE" {
					t.Errorf("dial got %q %q", key, secret)
				}
				return a
			},
			CPU: func() (float64, error) { return 3, nil }, ID: "local", Log: slog.New(slog.NewTextHandler(io.Discard, nil)), Now: time.Now,
			Poll: time.Second, Beat: time.Second, Lost: 30 * time.Second, Unclaimed: time.Minute,
			StepFor: time.Minute, BusyEvery: 10 * time.Second, Timeout: 10 * time.Second, Refine: true, Cooldown: 20 * time.Second,
		}
		if err := r.Tick(t.Context()); err != nil {
			t.Fatal(err)
		}
		a.wg.Wait()
	})
	rep := NewReport(local.Run(), local.LiveKitURL, "1.0.0-test")
	if rep.Capacity == nil || *rep.Capacity != 24 {
		t.Fatalf("capacity = %v, want 24", rep.Capacity)
	}
	html, err := rep.HTML()
	if err != nil {
		t.Fatal(err)
	}
	js, err := rep.JSON()
	if err != nil {
		t.Fatal(err)
	}
	for name, body := range map[string]string{"html": string(html), "md": string(rep.Markdown()), "json": string(js)} {
		if strings.Contains(body, "APIkeyVALUE") || strings.Contains(body, "secretVALUE") {
			t.Errorf("%s report carries the key or secret", name)
		}
		if !strings.Contains(body, MeasuredWithURL) {
			t.Errorf("%s report has no %s link", name, MeasuredWith)
		}
	}
	for _, offline := range []string{"<script", "http://", "fonts.googleapis", "<link"} {
		if strings.Contains(string(html), offline) {
			t.Errorf("the HTML report must open offline, but carries %q", offline)
		}
	}
	var back Report
	if err := json.Unmarshal(js, &back); err != nil || back.Capacity == nil || *back.Capacity != 24 {
		t.Errorf("json round trip: %v %+v", err, back.Capacity)
	}
	if n := a.open(); n != 0 {
		t.Errorf("%d test rooms left open", n)
	}
}

func slicesEqual(a, b []int) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}
