package capacity

import (
	"bytes"
	_ "embed"
	"encoding/json"
	"fmt"
	"html/template"
	"math"
	"net/url"
	"slices"
	"strings"
	"text/tabwriter"
	"time"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// MeasuredWith is the line every report carries, with its link (Mahimai, 2026-11-15).
const (
	MeasuredWith    = "Measured with VoiceGateway"
	MeasuredWithURL = "https://voicegateway.dev"
)

// Report is one capacity run as the files `voicegateway capacity` writes and the console downloads
// describe it. It holds measured facts only; an unknown value is null, never zero.
type Report struct {
	Title         string       `json:"title"`
	MeasuredWith  string       `json:"measuredWith"`
	Link          string       `json:"link"`
	Version       string       `json:"version"`
	RunID         string       `json:"runId"`
	Agent         string       `json:"agent"`
	LiveKitHost   *string      `json:"livekitHost"`
	AgentServer   *string      `json:"agentServer"`
	AgentServers  *int         `json:"agentServers"`
	TargetCalls   *int         `json:"targetCalls"`
	MaxStep       int          `json:"maxStep"`
	StartedAt     *time.Time   `json:"startedAt"`
	EndedAt       *time.Time   `json:"endedAt"`
	JoinThreshold *float64     `json:"joinThresholdSeconds"`
	CloudAgent    *bool        `json:"cloudAgent"`
	Capacity      *int         `json:"capacity"`
	LimitedBy     *string      `json:"limitedBy"`
	Error         *string      `json:"error"`
	Verdict       string       `json:"verdict"`
	Limit         []string     `json:"limit"`
	Next          []string     `json:"next"`
	Steps         []ReportStep `json:"steps"`
	NotMeasured   []string     `json:"notMeasured"`
}

// ReportStep is one step: how many calls were up at once and what they saw.
type ReportStep struct {
	Calls       int              `json:"calls"`
	Refine      bool             `json:"refine"`
	Outcome     string           `json:"outcome"`
	StartedAt   time.Time        `json:"startedAt"`
	EndedAt     *time.Time       `json:"endedAt"`
	Joined      int              `json:"joined"`
	Pending     int              `json:"pending"`
	Dropped     int              `json:"dropped"`
	Late        int              `json:"late"`
	NoAnswer    int              `json:"noAnswer"`
	SlowestJoin *float64         `json:"slowestJoinSeconds"`
	Answers     store.AnswerTail `json:"answers"`
	TesterCPU   *float64         `json:"testerCpuPercent"`
	// JoinSeconds are the joins of the calls this step added; AnswerSeconds every answer heard.
	JoinSeconds   []float64 `json:"joinSeconds"`
	AnswerSeconds []float64 `json:"answerSeconds"`
}

// NewReport builds the report of a run. version names the VoiceGateway that measured it; the
// LiveKit URL is reduced to its host, so no path or query reaches the file.
func NewReport(run store.CapacityRun, livekitURL, version string) Report {
	r := Report{
		Title:        "Voice agent capacity test",
		MeasuredWith: MeasuredWith, Link: MeasuredWithURL, Version: version,
		RunID: run.ID, Agent: run.Agent, LiveKitHost: hostOf(livekitURL), MaxStep: run.MaxStep,
		StartedAt: run.StartedAt, EndedAt: run.EndedAt, CloudAgent: run.CloudAgent,
		Capacity: run.Capacity, LimitedBy: run.LimitedBy, Error: run.Error,
		NotMeasured: []string{"the agent servers' CPU and memory", "time per stage (speech to text, model, voice)", "phone (SIP) calls", "answer quality"},
	}
	if run.JoinThresholdMs != nil {
		s := float64(*run.JoinThresholdMs) / 1000
		r.JoinThreshold = &s
	}
	ladder := Ladder(run.MaxStep)
	for _, s := range run.Steps {
		rs := ReportStep{
			Calls: s.Step, Refine: !slices.Contains(ladder, s.Step), Outcome: s.Outcome,
			StartedAt: s.StartedAt, EndedAt: s.EndedAt, Joined: s.Joined, Pending: s.Pending,
			Dropped: s.Failed, Late: s.Late, NoAnswer: s.NoAnswer, Answers: store.TailOf(s.AnswerSeconds),
			TesterCPU: s.TesterCPUPercent, JoinSeconds: nonNil(s.JoinSeconds), AnswerSeconds: nonNil(s.AnswerSeconds),
		}
		if len(s.JoinSeconds) > 0 {
			m := slices.Max(s.JoinSeconds)
			rs.SlowestJoin = &m
		}
		r.Steps = append(r.Steps, rs)
	}
	r.Verdict, r.Limit = explain(r)
	return r
}

// Ordered is the steps by calls at once, so refine steps sit between the steps around them.
func (r Report) Ordered() []ReportStep {
	s := slices.Clone(r.Steps)
	slices.SortStableFunc(s, func(a, b ReportStep) int { return a.Calls - b.Calls })
	return s
}

// explain says in plain words what the run found and what limited it, from the stop reason and the
// counts of the step that stopped it. When a step saw both, it leads with what most calls saw: a
// worker that leaves 8 calls waiting and drops 1 reads as full, not as dropping calls.
func explain(r Report) (string, []string) {
	limit := ""
	if r.LimitedBy != nil {
		limit = *r.LimitedBy
	}
	var stopped *ReportStep
	for i := range r.Steps {
		if r.Steps[i].Outcome == store.StepFailed || r.Steps[i].Outcome == store.StepInconclusive {
			if stopped == nil || r.Steps[i].Calls < stopped.Calls {
				stopped = &r.Steps[i]
			}
		}
	}
	verdict := "No step passed, so the test has no capacity number."
	if r.Capacity != nil {
		verdict = fmt.Sprintf("%d %s at once, every call answered.", *r.Capacity, plural(*r.Capacity, "call", "calls"))
	}
	if limit == store.LimitMaxReached {
		return verdict + fmt.Sprintf(" That was the most this test placed, so the limit is at least %d; it was not tested above that.", r.MaxStep), nil
	}
	if stopped == nil {
		stopped = &ReportStep{}
	}
	if limit == store.LimitPending || limit == store.LimitPlan || limit == store.LimitJobFailed {
		if r.Capacity == nil && stopped.Joined == 0 && stopped.Dropped == 0 && limit != store.LimitJobFailed {
			return "No worker took the first call, so the test has no capacity number.", []string{
				"Check that the agent name matches the agent_name its worker registers, and that a worker is connected to this LiveKit server.",
			}
		}
		why := []string{seen(*stopped)}
		if gap := between(r, *stopped); gap != "" {
			why = append(why, gap)
		}
		switch {
		case limit == store.LimitPlan:
			why = append(why, fmt.Sprintf("LiveKit Cloud's Build plan caps agent sessions at %d; a paid plan lifts it.", PlanSessions))
			return verdict + " " + past(r) + "LiveKit's plan limit stopped new calls.", why
		case stopped.Pending > 0 && stopped.Pending >= stopped.Dropped:
			why = append(why, "More agent workers or bigger servers raise it.")
			return verdict + " " + past(r) + "it refused new calls (worker full).", why
		case stopped.Dropped > 0:
			why = append(why, "The agent's own logs say why it left (a crash, an error or a provider timeout).")
			return verdict + " " + past(r) + "it dropped calls it had answered.", why
		}
		return verdict + " " + past(r) + "LiveKit refused to send the agent.", []string{"Check that the agent name is right and that a worker is registered."}
	}
	switch limit {
	case store.LimitAnswer:
		why := []string{fmt.Sprintf("At %d calls, answers slowed: a median over %d%% above one call's, or a line with no answer in %d s.", stopped.Calls, int(math.Round((SlowerBy-1)*100)), int(AnswerWithin.Seconds()))}
		if gap := between(r, *stopped); gap != "" {
			why = append(why, gap)
		}
		return verdict + " " + past(r) + "answers slowed down.", why
	case store.LimitTester:
		return verdict + " Then the machine placing the calls ran out of CPU, so the agent's limit was not reached.", []string{"Run the test from a bigger machine."}
	case store.LimitAgentBusy:
		return "The test did not start: the agent already had a call.", []string{"Run it when the agent is idle, or on a staging agent."}
	case store.LimitRealCall:
		return verdict + " Then a real call reached the agent, so the test stopped.", nil
	case store.LimitStopped:
		return verdict + " Then the test was stopped by hand, before the agent reached a limit.", nil
	case store.LimitRunnerLost, store.LimitNoWorker:
		return verdict + " Then the test was interrupted, so this is not the agent's limit.", nil
	}
	if r.Error != nil {
		return verdict, []string{"The test failed before it reached the agent's limit: " + *r.Error + "."}
	}
	return verdict, []string{"The test failed before it reached the agent's limit."}
}

// seen is what the calls of the step that stopped the run saw, naming only what happened.
func seen(s ReportStep) string {
	parts := []string{}
	if s.Pending > 0 {
		parts = append(parts, fmt.Sprintf("%d never got the agent", s.Pending))
	}
	if s.Dropped > 0 {
		parts = append(parts, fmt.Sprintf("%d %s dropped", s.Dropped, plural(s.Dropped, "was", "were")))
	}
	if s.Joined > 0 && len(parts) > 0 {
		parts = append(parts, fmt.Sprintf("%d got it", s.Joined))
	}
	if len(parts) == 0 {
		return fmt.Sprintf("At %d calls, LiveKit refused to send the agent.", s.Calls)
	}
	return fmt.Sprintf("At %d calls, %s.", s.Calls, strings.Join(parts, ", "))
}

// between says how exact the number is: the limit pinned to one call, or the range left untested.
func between(r Report, stopped ReportStep) string {
	if r.Capacity == nil || stopped.Calls == 0 {
		return ""
	}
	if stopped.Calls == *r.Capacity+1 {
		return fmt.Sprintf("Exact: %d passed, %d failed.", *r.Capacity, stopped.Calls)
	}
	if slices.ContainsFunc(r.Steps, func(s ReportStep) bool { return s.Refine }) {
		return fmt.Sprintf("The re-test narrowed it to between %d and %d.", *r.Capacity, stopped.Calls-1)
	}
	return fmt.Sprintf("The real limit is between %d and %d; this run did not test the numbers in between.", *r.Capacity, stopped.Calls-1)
}

// Setup adds what the person told the command about the agent's servers and the calls they need,
// and works out the servers that need takes. Nothing here is measured, so it is only what was given.
func (r Report) Setup(server string, servers, target int) Report {
	if server != "" {
		r.AgentServer = &server
	}
	if servers > 0 {
		r.AgentServers = &servers
	}
	if target > 0 {
		r.TargetCalls = &target
	}
	r.Next = nil
	if r.Capacity == nil || *r.Capacity == 0 {
		return r
	}
	// Servers needed come from the whole measured ratio, rounded up once, so three servers
	// that carried 16 together never come out as needing four for 16.
	n := 1
	unit := "times this setup"
	if r.AgentServers != nil {
		n = *r.AgentServers
		unit = "servers like these"
		if n > 1 && *r.Capacity >= n {
			avg := fmt.Sprintf("%.1f", float64(*r.Capacity)/float64(n))
			if *r.Capacity%n == 0 {
				avg = fmt.Sprint(*r.Capacity / n)
			}
			r.Next = append(r.Next, fmt.Sprintf("Estimated average: about %s calls per server (the test measured them together).", avg))
		}
	}
	if r.TargetCalls != nil {
		need := (*r.TargetCalls*n + *r.Capacity - 1) / *r.Capacity
		r.Next = append(r.Next, fmt.Sprintf("For %d calls at once: %d %s, at the measured limit with no headroom.", *r.TargetCalls, need, unit))
	}
	return r
}

// JSON is the report as indented JSON.
func (r Report) JSON() ([]byte, error) {
	b, err := json.MarshalIndent(r, "", "  ")
	if err != nil {
		return nil, fmt.Errorf("capacity report json: %w", err)
	}
	return append(b, '\n'), nil
}

// Markdown is the report as Markdown, for a pull request, an issue or an email.
func (r Report) Markdown() []byte {
	var b strings.Builder
	fmt.Fprintf(&b, "# Capacity: %s\n\n", r.Agent)
	fmt.Fprintf(&b, "**%s**\n\n", r.Verdict)
	if r.AgentServer != nil {
		fmt.Fprintf(&b, "Agent server: %s\n\n", *r.AgentServer)
	}
	for _, l := range append(slices.Clone(r.Limit), r.Next...) {
		fmt.Fprintf(&b, "- %s\n", l)
	}
	b.WriteString("\n| Calls at once | Result | Got the agent | Median answer | p95 answer |\n|---:|---|---:|---:|---:|\n")
	for _, s := range r.Ordered() {
		fmt.Fprintf(&b, "| %d%s | %s | %d of %d | %s | %s |\n", s.Calls, refineMark(s.Refine), s.Outcome, s.Joined, s.Calls, secs(s.Answers.Median), secs(s.Answers.P95))
	}
	b.WriteString("\n<details><summary>All measurements</summary>\n\n")
	b.WriteString("| Calls | Refused | Dropped | Answers | Over 1 s | Slowest | Slowest join | Tester CPU |\n|---:|---:|---:|---:|---:|---:|---:|---:|\n")
	for _, s := range r.Ordered() {
		fmt.Fprintf(&b, "| %d%s | %d | %d | %d | %s | %s | %s | %s |\n", s.Calls, refineMark(s.Refine), s.Pending, s.Dropped, s.Answers.Count,
			pct(s.Answers.SlowShare), secs(s.Answers.Slowest), secs(s.SlowestJoin), pct100(s.TesterCPU))
	}
	fmt.Fprintf(&b, "\n%s\n\n</details>\n\n", r.Method())
	fmt.Fprintf(&b, "%s · [%s](%s) %s · run %s\n", r.When(), r.MeasuredWith, r.Link, r.Version, r.RunID)
	return []byte(b.String())
}

// ServerCount is how many agent servers the person said ran the agent, in words, or empty.
func (r Report) ServerCount() string {
	if r.AgentServers == nil {
		return ""
	}
	return fmt.Sprintf("%d agent %s", *r.AgentServers, plural(*r.AgentServers, "server", "servers"))
}

// Method is the one paragraph a reader needs to trust the number, and what the number leaves out.
func (r Report) Method() string {
	m := "Each step holds that many calls for 60 s. Callers say one line after each answer; answer time runs from the end of the line to the agent's first sound."
	if slices.ContainsFunc(r.Steps, func(s ReportStep) bool { return s.Refine }) {
		m += " Steps marked * re-test a number between the last pass and the first fail."
	}
	return m + " Not measured: " + strings.Join(r.NotMeasured, ", ") + "."
}

// Table is the steps as a plain text table for a terminal.
func (r Report) Table() string {
	var b bytes.Buffer
	w := tabwriter.NewWriter(&b, 0, 0, 2, ' ', tabwriter.AlignRight)
	_, _ = fmt.Fprintln(w, "calls\tresult\tgot agent\trefused\tdropped\tmedian\tp95\tover 1 s\tslowest\t")
	for _, s := range r.Ordered() {
		_, _ = fmt.Fprintf(w, "%d%s\t%s\t%d\t%d\t%d\t%s\t%s\t%s\t%s\t\n", s.Calls, refineMark(s.Refine), s.Outcome, s.Joined, s.Pending, s.Dropped,
			secs(s.Answers.Median), secs(s.Answers.P95), pct(s.Answers.SlowShare), secs(s.Answers.Slowest))
	}
	_ = w.Flush()
	return b.String()
}

// When is the run's time span in UTC, or unknown.
func (r Report) When() string {
	if r.StartedAt == nil {
		return "time unknown"
	}
	s := r.StartedAt.UTC().Format("2 Jan 2006, 15:04")
	if r.EndedAt != nil {
		s += " to " + r.EndedAt.UTC().Format("15:04")
	}
	return s + " UTC"
}

//go:embed report.html.tmpl
var reportHTML string

var reportTemplate = template.Must(template.New("report").Funcs(template.FuncMap{
	"secs": secs, "pct": pct, "pct100": pct100, "mark": refineMark,
	"chart": func(r Report) template.HTML { return chart(r.Ordered()) },
}).Parse(reportHTML))

// HTML is the report as one self-contained page: no script, font or image from the network, light
// and dark, readable at phone width.
func (r Report) HTML() ([]byte, error) {
	var b bytes.Buffer
	if err := reportTemplate.Execute(&b, r); err != nil {
		return nil, fmt.Errorf("capacity report html: %w", err)
	}
	return b.Bytes(), nil
}

// chart draws calls that got the agent per step as bars and the median and p95 answer as lines.
// It is server-rendered SVG, so the page needs no script; no charting library renders to a
// static string without a browser.
func chart(steps []ReportStep) template.HTML {
	if len(steps) == 0 {
		return ""
	}
	const w, h, left, right, top, bottom = 640.0, 260.0, 44.0, 44.0, 16.0, 40.0
	maxCalls, maxSec := 1, 1.0
	for _, s := range steps {
		maxCalls = max(maxCalls, s.Calls)
		if s.Answers.P95 != nil {
			maxSec = max(maxSec, *s.Answers.P95)
		}
	}
	maxSec = math.Ceil(maxSec)
	band := (w - left - right) / float64(len(steps))
	y := func(v, top0 float64) float64 { return top + (h-top-bottom)*(1-v/top0) }
	var b strings.Builder
	fmt.Fprintf(&b, `<svg viewBox="0 0 %.0f %.0f" role="img" aria-label="Calls that got the agent, and median and p95 answer time, per step">`, w, h)
	fmt.Fprintf(&b, `<line x1="%.0f" y1="%.1f" x2="%.0f" y2="%.1f" class="axis"/>`, left, h-bottom, w-right, h-bottom)
	fmt.Fprintf(&b, `<text x="%.0f" y="%.0f" class="tick" text-anchor="end">%d</text>`, left-6, top+4, maxCalls)
	fmt.Fprintf(&b, `<text x="%.0f" y="%.0f" class="tick" text-anchor="start">%.0f s</text>`, w-right+6, top+4, maxSec)
	var med, p95 []string
	for i, s := range steps {
		x := left + band*float64(i) + band*0.2
		bw := band * 0.6
		fmt.Fprintf(&b, `<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" class="bar"><title>%d calls: %d got the agent</title></rect>`,
			x, y(float64(s.Joined), float64(maxCalls)), bw, (h-top-bottom)*float64(s.Joined)/float64(maxCalls), s.Calls, s.Joined)
		if s.Pending > 0 {
			fmt.Fprintf(&b, `<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" class="bar refused"><title>%d refused</title></rect>`,
				x, y(float64(s.Joined+s.Pending), float64(maxCalls)), bw, (h-top-bottom)*float64(s.Pending)/float64(maxCalls), s.Pending)
		}
		cx := x + bw/2
		fmt.Fprintf(&b, `<text x="%.1f" y="%.0f" class="tick" text-anchor="middle">%d%s</text>`, cx, h-bottom+18, s.Calls, refineMark(s.Refine))
		if s.Answers.Median != nil {
			med = append(med, fmt.Sprintf("%.1f,%.1f", cx, y(*s.Answers.Median, maxSec)))
		}
		if s.Answers.P95 != nil {
			p95 = append(p95, fmt.Sprintf("%.1f,%.1f", cx, y(*s.Answers.P95, maxSec)))
		}
	}
	fmt.Fprintf(&b, `<polyline points="%s" class="line p95"/>`, strings.Join(p95, " "))
	fmt.Fprintf(&b, `<polyline points="%s" class="line median"/>`, strings.Join(med, " "))
	fmt.Fprintf(&b, `<text x="%.0f" y="%.0f" class="tick" text-anchor="middle">calls at once</text>`, w/2, h-4)
	b.WriteString(`</svg>`)
	return template.HTML(b.String()) //nolint:gosec // built from numbers only
}

// past opens the sentence on what happened above the capacity, or at the first step with none.
func past(r Report) string {
	if r.Capacity == nil {
		return "At the first step, "
	}
	return "Past that, "
}

func hostOf(raw string) *string {
	u, err := url.Parse(raw)
	if err != nil || u.Host == "" {
		return nil
	}
	return &u.Host
}

func nonNil(xs []float64) []float64 {
	if xs == nil {
		return []float64{}
	}
	return xs
}

func plural(n int, one, many string) string {
	if n == 1 {
		return one
	}
	return many
}

func refineMark(refine bool) string {
	if refine {
		return "*"
	}
	return ""
}

func secs(v *float64) string {
	if v == nil {
		return "unknown"
	}
	return fmt.Sprintf("%.2f s", *v)
}

func pct(v *float64) string {
	if v == nil {
		return "unknown"
	}
	return fmt.Sprintf("%.0f%%", *v*100)
}

func pct100(v *float64) string {
	if v == nil {
		return "unknown"
	}
	return fmt.Sprintf("%.0f%%", *v)
}
