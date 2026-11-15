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

// SlowAnswer is the answer time above which an answer counts as slow in a report: one second
// from the end of the caller's line to the agent's first sound.
const SlowAnswer = 1.0

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
	LiveKitHost   string       `json:"livekitHost"`
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
	Steps         []ReportStep `json:"steps"`
	NotMeasured   []string     `json:"notMeasured"`
}

// ReportStep is one step: how many calls were up at once and what they saw.
type ReportStep struct {
	Calls       int        `json:"calls"`
	Refine      bool       `json:"refine"`
	Outcome     string     `json:"outcome"`
	StartedAt   time.Time  `json:"startedAt"`
	EndedAt     *time.Time `json:"endedAt"`
	Joined      int        `json:"joined"`
	Pending     int        `json:"pending"`
	Dropped     int        `json:"dropped"`
	Late        int        `json:"late"`
	NoAnswer    int        `json:"noAnswer"`
	SlowestJoin *float64   `json:"slowestJoinSeconds"`
	Answers     Tail       `json:"answers"`
	TesterCPU   *float64   `json:"testerCpuPercent"`
	// JoinSeconds are the joins of the calls this step added; AnswerSeconds every answer heard.
	JoinSeconds   []float64 `json:"joinSeconds"`
	AnswerSeconds []float64 `json:"answerSeconds"`
}

// Tail is a step's answer times: the typical one and the slow end. Each is null with no answers.
type Tail struct {
	Count     int      `json:"count"`
	Median    *float64 `json:"medianSeconds"`
	P95       *float64 `json:"p95Seconds"`
	SlowShare *float64 `json:"overOneSecondShare"`
	Slowest   *float64 `json:"slowestSeconds"`
}

// TailOf summarises answer times. P95 is the nearest-rank 95th percentile.
func TailOf(answers []float64) Tail {
	t := Tail{Count: len(answers)}
	if len(answers) == 0 {
		return t
	}
	s := slices.Clone(answers)
	slices.Sort(s)
	med, _ := median(s)
	p95 := s[int(math.Ceil(0.95*float64(len(s))))-1]
	slow := 0
	for _, a := range s {
		if a > SlowAnswer {
			slow++
		}
	}
	share := float64(slow) / float64(len(s))
	last := s[len(s)-1]
	t.Median, t.P95, t.SlowShare, t.Slowest = &med, &p95, &share, &last
	return t
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
		NotMeasured: []string{
			"CPU and memory on the agent's own servers.",
			"Time spent in each stage of a turn: speech to text, the model and the voice.",
			"Phone calls: the test callers join over WebRTC, not through a SIP trunk.",
			"How good the answers were: every caller says the same short English line.",
		},
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
			Dropped: s.Failed, Late: s.Late, NoAnswer: s.NoAnswer, Answers: TailOf(s.AnswerSeconds),
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
// counts of the step that stopped it.
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
		verdict = fmt.Sprintf("The agent carried %d %s at once with every call answered.", *r.Capacity, plural(*r.Capacity, "call", "calls"))
	}
	if limit == store.LimitMaxReached {
		return verdict + fmt.Sprintf(" That was the most this test placed (%d), so the real limit is higher.", r.MaxStep), []string{"Nothing yet: every step passed up to the maximum the test was asked to place."}
	}
	at := "Then "
	if stopped != nil {
		at = fmt.Sprintf("At %d %s, ", stopped.Calls, plural(stopped.Calls, "call", "calls"))
	} else {
		stopped = &ReportStep{}
	}
	switch limit {
	case store.LimitPending, store.LimitPlan:
		if r.Capacity == nil && stopped.Joined == 0 {
			return "No worker took the first call, so the test has no capacity number.", []string{
				fmt.Sprintf("The agent did not join within %d s. Check that the agent name matches the agent_name its worker registers, and that a worker is running and connected to this LiveKit server.", int(PendingAfter.Seconds())),
			}
		}
		got := ""
		if stopped.Joined > 0 {
			got = fmt.Sprintf(", while %d did", stopped.Joined)
		}
		why := []string{fmt.Sprintf("%sthe agent refused new calls: %d of %d never got the agent within %d s%s.", at, stopped.Pending, stopped.Calls, int(PendingAfter.Seconds()), got)}
		if stopped.Dropped > 0 {
			why = append(why, fmt.Sprintf("It also left %d %s it had joined.", stopped.Dropped, plural(stopped.Dropped, "call", "calls")))
		}
		if limit == store.LimitPlan {
			why = append(why, fmt.Sprintf("It stopped at %d calls, LiveKit Cloud's Build plan limit on agent sessions at once. A paid LiveKit plan lifts it.", PlanSessions))
		} else {
			why = append(why, "This is the agent's workers saying they are full. LiveKit Agents stops taking jobs when a worker's load passes its load threshold (0.7 by default), so more workers, bigger servers or a higher threshold raise it.")
		}
		return verdict + " " + past(r) + "it refused new calls (worker full).", why
	case store.LimitJobFailed:
		if stopped != nil && stopped.Dropped > 0 {
			return verdict + " " + past(r) + "it dropped calls it had answered.", []string{fmt.Sprintf("%sthe agent left %d %s it had joined, while the caller was still there. Its own logs say why (a crash, an error or a timeout in a provider).", at, stopped.Dropped, plural(stopped.Dropped, "call", "calls"))}
		}
		return verdict + " " + past(r) + "LiveKit refused to send the agent.", []string{at + "LiveKit refused a dispatch to the agent. Check that the agent name is right and that a worker is registered."}
	case store.LimitAnswer:
		why := at + fmt.Sprintf("answers slowed past the test's rule: a median more than %d%% above the one-call step, or a line with no answer in %d s.", int(math.Round((SlowerBy-1)*100)), int(AnswerWithin.Seconds()))
		return verdict + " " + past(r) + "answers slowed down.", []string{why}
	case store.LimitTester:
		return verdict + " Then the machine placing the calls ran out of CPU, so the agent's limit was not reached.", []string{"The tester, not the agent: its CPU averaged over 90% across the step. Run the test from a bigger machine."}
	case store.LimitAgentBusy:
		return "The test did not start: the agent already had a call.", []string{"The test runs only against an idle agent, so it never shares a worker with a real caller. Run it when the agent is idle, or on a staging agent."}
	case store.LimitRealCall:
		return verdict + " Then a real call reached the agent, so the test stopped.", []string{"A room the test did not create got the agent. The test stops so it never slows a real caller."}
	case store.LimitStopped:
		return verdict + " Then the test was stopped by hand.", []string{"Stopped before the agent reached a limit."}
	case store.LimitRunnerLost, store.LimitNoWorker:
		return verdict + " Then the test was interrupted.", []string{"The test did not finish, so this is not the agent's limit."}
	}
	reason := "The test failed before it reached the agent's limit."
	if r.Error != nil {
		reason += " Error: " + *r.Error + "."
	}
	return verdict, []string{reason}
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
	fmt.Fprintf(&b, "# %s: %s\n\n", r.Title, r.Agent)
	if r.Capacity != nil {
		fmt.Fprintf(&b, "**%d calls at once.** %s\n\n", *r.Capacity, r.Verdict)
	} else {
		fmt.Fprintf(&b, "**No capacity number.** %s\n\n", r.Verdict)
	}
	b.WriteString("## What limited it\n\n")
	for _, l := range r.Limit {
		fmt.Fprintf(&b, "- %s\n", l)
	}
	b.WriteString("\n## Step by step\n\n")
	b.WriteString("| Calls at once | Result | Got the agent | Refused | Dropped | Answers | Median | p95 | Over 1 s | Slowest | Slowest join | Tester CPU |\n")
	b.WriteString("|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|\n")
	for _, s := range r.Ordered() {
		fmt.Fprintf(&b, "| %d%s | %s | %d | %d | %d | %d | %s | %s | %s | %s | %s | %s |\n",
			s.Calls, refineMark(s.Refine), s.Outcome, s.Joined, s.Pending, s.Dropped, s.Answers.Count,
			secs(s.Answers.Median), secs(s.Answers.P95), pct(s.Answers.SlowShare), secs(s.Answers.Slowest), secs(s.SlowestJoin), pct100(s.TesterCPU))
	}
	b.WriteString("\nAnswer time runs from the end of the caller's line to the agent's first sound. Each step lasts 60 s and keeps the calls of the step before. ")
	if slices.ContainsFunc(r.Steps, func(s ReportStep) bool { return s.Refine }) {
		b.WriteString("Steps marked * re-tested a number between the last pass and the first fail, from no calls up.")
	}
	b.WriteString("\n\n## What this test does not measure\n\n")
	for _, n := range r.NotMeasured {
		fmt.Fprintf(&b, "- %s\n", n)
	}
	fmt.Fprintf(&b, "\nRun %s · agent `%s` on %s · %s\n\n%s %s: %s\n", r.RunID, r.Agent, r.LiveKitHost, r.When(), r.MeasuredWith, r.Version, r.Link)
	return []byte(b.String())
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

func hostOf(raw string) string {
	u, err := url.Parse(raw)
	if err != nil || u.Host == "" {
		return "unknown host"
	}
	return u.Host
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
