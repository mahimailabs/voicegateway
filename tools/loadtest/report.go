package main

import (
	"fmt"
	"io"
	"maps"
	"slices"
	"time"
)

type report struct {
	Run           string                     `json:"run"`
	Calls         int                        `json:"calls"`
	Ramp          string                     `json:"ramp"`
	Stretch       float64                    `json:"stretch"`
	Started       time.Time                  `json:"started"`
	SendTook      string                     `json:"send_took"`
	DrainTook     string                     `json:"drain_after_last_send"`
	SummariesTook string                     `json:"summaries_after_last_send"`
	PeakLive      int64                      `json:"peak_live_calls"`
	PeakLag       int64                      `json:"peak_stream_backlog"`
	Sent          map[kind]*counts           `json:"sent"`
	Latency       map[kind]map[string]string `json:"latency"`
	WebhookQueue  map[string]string          `json:"webhook_queue_wait"`
	DropsFrom     *time.Time                 `json:"drops_from,omitempty"`
	DropsUntil    *time.Time                 `json:"drops_until,omitempty"`
	Errors        map[string]int             `json:"errors,omitempty"`
	Stored        stored                     `json:"stored"`
	Checks        []check                    `json:"checks"`
	Pass          bool                       `json:"pass"`
	Samples       []sample                   `json:"samples"`
	p99           map[kind]time.Duration
}

type check struct {
	Name string `json:"name"`
	Want string `json:"want"`
	Got  string `json:"got"`
	Pass bool   `json:"pass"`
}

func (r *recorder) report(c config, run string, started, sent, drained time.Time, samples []sample) *report {
	r.mu.Lock()
	defer r.mu.Unlock()
	rep := &report{
		Run: run, Calls: c.calls, Ramp: c.ramp.String(), Stretch: c.stretch, Started: started,
		SendTook: sent.Sub(started).Round(time.Millisecond).String(), DrainTook: drained.Sub(sent).Round(time.Millisecond).String(),
		Sent: r.counts, Latency: map[kind]map[string]string{}, p99: map[kind]time.Duration{}, Errors: r.errs, Samples: samples,
	}
	for k, ds := range r.latencies {
		rep.Latency[k] = quantiles(ds)
		sorted := slices.Clone(ds)
		slices.Sort(sorted)
		rep.p99[k] = quantile(sorted, 0.99)
	}
	rep.WebhookQueue = quantiles(r.webhookQueue)
	if n := len(r.dropTimes); n > 0 {
		rep.DropsFrom, rep.DropsUntil = &r.dropTimes[0], &r.dropTimes[n-1]
	}
	for _, s := range samples {
		rep.PeakLive = max(rep.PeakLive, s.Live)
		rep.PeakLag = max(rep.PeakLag, s.Lag+s.Pending)
	}
	return rep
}

// judge compares what the stack stored with what was delivered and accepted. Accepted counts come
// from the answers; the call count, the participants and the spans also come from the captured call
// itself, so a refused delivery cannot lower the bar.
func (rep *report) judge(calls int, p99 time.Duration) {
	acc := func(k kind) int {
		if rep.Sent[k] == nil {
			return 0
		}
		return rep.Sent[k].Accepted
	}
	queued := func(k kind) int {
		if rep.Sent[k] == nil {
			return 0
		}
		return rep.Sent[k].Queued
	}
	spans := 0
	if rep.Sent[kindTraces] != nil {
		spans = rep.Sent[kindTraces].Spans
	}
	add := func(name string, want int, got any) {
		rep.Checks = append(rep.Checks, check{Name: name, Want: fmt.Sprint(want), Got: fmt.Sprint(got), Pass: fmt.Sprint(want) == fmt.Sprint(got)})
	}
	failed := 0
	for _, c := range rep.Sent {
		failed += c.Failed
	}
	add("deliveries refused or given up", 0, failed)
	add("webhook events stored", queued(kindWebhook), rep.Stored.Webhooks)
	add("trace exports stored", queued(kindTraces), rep.Stored.Traces)
	add("spans stored", spans, rep.Stored.Spans)
	add("metric exports stored", queued(kindMetrics), rep.Stored.Metrics)
	add("session reports stored", queued(kindReport), rep.Stored.Reports)
	if queued(kindSDK) > 0 {
		add("SDK batches stored", queued(kindSDK), rep.Stored.SDK)
	}
	add("calls", calls, rep.Stored.Calls)
	add("calls ended as observed", calls, rep.Stored.CallsObserved)
	add("calls with their agent job", calls, rep.Stored.CallsWithJob)
	add("participants (caller and agent)", 2*calls, rep.Stored.Participants)
	add("webhooks joined to a call", acc(kindWebhook), rep.Stored.WebhooksOnACall)
	add("spans joined to a call", spans, rep.Stored.SpansOnACall)
	add("session reports joined to a call", acc(kindReport), rep.Stored.ReportsOnACall)
	add("call summaries built", calls, rep.Stored.Summaries)
	add("distinct turn counts (same call)", 1, rep.Stored.TurnCounts)
	add("events with no call", 0, rep.Stored.Unmatched)
	add("new dead letters", 0, rep.Stored.DeadLetters)
	kinds := slices.Sorted(maps.Keys(rep.p99))
	for _, k := range kinds {
		got := rep.p99[k].Round(10 * time.Microsecond)
		rep.Checks = append(rep.Checks, check{Name: fmt.Sprintf("%s p99 answer", k), Want: "< " + p99.String(), Got: got.String(), Pass: rep.p99[k] < p99})
	}
	rep.Pass = true
	for _, c := range rep.Checks {
		rep.Pass = rep.Pass && c.Pass
	}
}

func (rep *report) print(w io.Writer) {
	p := func(format string, a ...any) { _, _ = fmt.Fprintf(w, format, a...) }
	p("\nrun %s: %d calls over %s, stretch %g; peak %d live calls, peak worker backlog %d entries\n",
		rep.Run, rep.Calls, rep.Ramp, rep.Stretch, rep.PeakLive, rep.PeakLag)
	p("sent in %s; stored and acknowledged %s after the last send; every call summarised %s after it\n\n", rep.SendTook, rep.DrainTook, rep.SummariesTook)
	kinds := make([]kind, 0, len(rep.Sent))
	for k := range rep.Sent {
		kinds = append(kinds, k)
	}
	slices.Sort(kinds)
	p("%-15s %8s %8s %8s %8s %10s %10s %10s %10s\n", "upload", "sent", "accepted", "failed", "non-2xx", "p50", "p95", "p99", "max")
	for _, k := range kinds {
		c, l := rep.Sent[k], rep.Latency[k]
		p("%-15s %8d %8d %8d %8d %10s %10s %10s %10s\n", k, c.Queued, c.Accepted, c.Failed, c.Retried, l["p50"], l["p95"], l["p99"], l["max"])
	}
	p("\nwebhooks waited in LiveKit's notifier queue: p50 %s, p99 %s, max %s\n", rep.WebhookQueue["p50"], rep.WebhookQueue["p99"], rep.WebhookQueue["max"])
	if rep.DropsFrom != nil {
		p("the notifier dropped webhooks from %s to %s\n", rep.DropsFrom.Sub(rep.Started).Round(time.Second), rep.DropsUntil.Sub(rep.Started).Round(time.Second))
	}
	if len(rep.Errors) > 0 {
		p("\nerrors:\n")
		for e, n := range rep.Errors {
			p("  %5d  %s\n", n, e)
		}
	}
	p("\n%-36s %10s %10s\n", "check", "want", "got")
	for _, c := range rep.Checks {
		mark := "ok"
		if !c.Pass {
			mark = "FAIL"
		}
		p("%-36s %10s %10s  %s\n", c.Name, c.Want, c.Got, mark)
	}
	p("\n%s\n", map[bool]string{true: "PASS: every delivered event was stored, joined and summarised, within the p99 target", false: "FAIL"}[rep.Pass])
}
