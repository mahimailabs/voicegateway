package api

import (
	"cmp"
	"math"
	"net/http"
	"slices"
	"time"

	"github.com/go-chi/chi/v5"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// Bounds of getCallStats (spec 04e): the longest range, the hours byHour covers, and how many
// answer values and per-agent calls travel with the totals.
const (
	maxStatsRange  = 400 * 24 * time.Hour
	maxStatsHours  = 168
	maxStatsValues = 5000
	maxAgentCalls  = 500
	statsDay       = 24 * time.Hour
)

// callStats is the contract's CallStats. Fields this server does not compute yet are null, never
// an empty list or zero (spec 04e).
type callStats struct {
	From        string          `json:"from"`
	To          string          `json:"to"`
	Complete    bool            `json:"complete"`
	CallCount   *int            `json:"callCount"`
	ByChannel   statsChannels   `json:"byChannel"`
	ByDay       []statsDayCount `json:"byDay"`
	ByHour      []statsHour     `json:"byHour"`
	Answers     statsAnswers    `json:"answers"`
	IssueCounts statsIssues     `json:"issueCounts"`
	// AnatomyTurns, Stages and Chain need each turn's stage timings, which the summaries lack.
	AnatomyTurns *int         `json:"anatomyTurns"`
	Stages       *[]struct{}  `json:"stages"`
	Chain        *[]struct{}  `json:"chain"`
	Agents       []statsAgent `json:"agents"`
}

// statsChannels counts calls by listCalls' channel rule. Console and local are null: no stored
// source tells those channels apart yet.
type statsChannels struct {
	Phone   *int `json:"phone"`
	Web     *int `json:"web"`
	Console *int `json:"console"`
	Local   *int `json:"local"`
	Unknown *int `json:"unknown"`
}

type statsDayCount struct {
	Date      string `json:"date"`
	CallCount *int   `json:"callCount"`
}

type statsHour struct {
	Hour        string   `json:"hour"`
	CallCount   *int     `json:"callCount"`
	AnswerP50Ms *float64 `json:"answerP50Ms"`
}

type statsAnswers struct {
	Turns           *int          `json:"turns"`
	OverBudget      *int          `json:"overBudget"`
	BudgetMs        float64       `json:"budgetMs"`
	P50Ms           *float64      `json:"p50Ms"`
	P90Ms           *float64      `json:"p90Ms"`
	Values          []answerValue `json:"values"`
	ValuesTruncated bool          `json:"valuesTruncated"`
}

// answerValue is one measured turn. Turn is null: a summary keeps measured answers only, so the
// turn's position in the call is not stored.
type answerValue struct {
	Ms       *float64 `json:"ms"`
	CallID   string   `json:"callId"`
	Room     string   `json:"room"`
	Turn     *int     `json:"turn"`
	AgentKey *string  `json:"agentKey"`
}

// statsIssues is null throughout until the Issues engine computes issues.
type statsIssues struct {
	High   *int `json:"high"`
	Medium *int `json:"medium"`
	Low    *int `json:"low"`
	Info   *int `json:"info"`
}

type statsAgent struct {
	Key            *string     `json:"key"`
	Name           *string     `json:"name"`
	CallCount      *int        `json:"callCount"`
	AnswerTurns    *int        `json:"answerTurns"`
	AnswerP50Ms    *float64    `json:"answerP50Ms"`
	Issues         *int        `json:"issues"`
	Models         []string    `json:"models"`
	Providers      *[]struct{} `json:"providers"`
	AnatomyTurns   *int        `json:"anatomyTurns"`
	Stages         *[]struct{} `json:"stages"`
	Chain          *[]struct{} `json:"chain"`
	Calls          []agentCall `json:"calls"`
	CallsTruncated bool        `json:"callsTruncated"`
}

type agentCall struct {
	CallID      string   `json:"callId"`
	Room        string   `json:"room"`
	AnswerP50Ms *float64 `json:"answerP50Ms"`
}

// getCallStats serves operationId getCallStats over a project's servable calls that started in
// [from, to), from the call summaries the worker keeps.
func (a *API) getCallStats(w http.ResponseWriter, r *http.Request) {
	from, to, msg := statsRange(r)
	if msg != "" {
		writeError(w, r, http.StatusBadRequest, codeInvalidRequest, msg)
		return
	}
	read, err := a.Stats.StatsCalls(r.Context(), chi.URLParam(r, "orgId"), chi.URLParam(r, "projectId"), from, to)
	if err != nil {
		a.fail(w, r, err, "project not found")
		return
	}
	writeJSON(w, http.StatusOK, buildStats(read, from, to, float64(a.budget())))
}

// statsRange reads the required from and to, from before to and at most 400 days apart.
func statsRange(r *http.Request) (from, to time.Time, msg string) {
	q := r.URL.Query()
	var err error
	if from, err = time.Parse(time.RFC3339Nano, q.Get("from")); err != nil {
		return from, to, "from must be an RFC 3339 date-time"
	}
	if to, err = time.Parse(time.RFC3339Nano, q.Get("to")); err != nil {
		return from, to, "to must be an RFC 3339 date-time"
	}
	from, to = from.UTC(), to.UTC()
	if !from.Before(to) {
		return from, to, "from must be before to"
	}
	if to.Sub(from) > maxStatsRange {
		return from, to, "the range must be at most 400 days"
	}
	return from, to, ""
}

func buildStats(read store.StatsRead, from, to time.Time, budget float64) callStats {
	calls := read.Calls
	out := callStats{
		From: from.Format(time.RFC3339Nano), To: to.Format(time.RFC3339Nano), Complete: true,
		CallCount: ptr(len(calls)), Agents: []statsAgent{},
	}
	byChannel := map[string]int{}
	for _, c := range calls {
		byChannel[c.Channel]++
	}
	out.ByChannel = statsChannels{Phone: ptr(byChannel["phone"]), Web: ptr(byChannel["web"]), Unknown: ptr(byChannel["unknown"])}
	out.ByDay = dayCounts(read, from, to)
	out.ByHour = hourCounts(read, from, to)
	out.Answers = answersOf(calls, budget)
	out.Agents = agentsOf(calls)
	return out
}

// bucketCount is a day's or hour's call count. A bucket with stored calls counts them. An empty one
// is 0 only when it could have held a stored call: not wholly before the project was connected,
// and not wholly to come. A partial bucket counts the calls so far, 0 included.
func bucketCount(read store.StatsRead, start, end time.Time, n int) *int {
	if n == 0 && (!end.After(read.Connected) || start.After(read.Now)) {
		return nil
	}
	return &n
}

// dayCounts is one entry per UTC day of the range, oldest first.
func dayCounts(read store.StatsRead, from, to time.Time) []statsDayCount {
	out := []statsDayCount{}
	for d := from.Truncate(statsDay); d.Before(to); d = d.Add(statsDay) {
		e := statsDayCount{Date: d.Format(time.DateOnly)}
		n := 0
		for _, c := range read.Calls {
			if !c.StartAt.Before(d) && c.StartAt.Before(d.Add(statsDay)) {
				n++
			}
		}
		e.CallCount = bucketCount(read, d, d.Add(statsDay), n)
		out = append(out, e)
	}
	return out
}

// hourCounts is one entry per UTC hour over the last 168 hours of the range, oldest first, each with
// its call count and the median answer over its calls' measured turns.
func hourCounts(read store.StatsRead, from, to time.Time) []statsHour {
	first := from.Truncate(time.Hour)
	last := to.Add(-time.Nanosecond).Truncate(time.Hour)
	if n := int(last.Sub(first)/time.Hour) + 1; n > maxStatsHours {
		first = last.Add(-time.Duration(maxStatsHours-1) * time.Hour)
	}
	out := []statsHour{}
	for h := first; !h.After(last); h = h.Add(time.Hour) {
		e := statsHour{Hour: h.Format(time.RFC3339)}
		var answers []float64
		n := 0
		for _, c := range read.Calls {
			if !c.StartAt.Before(h) && c.StartAt.Before(h.Add(time.Hour)) {
				n++
				answers = append(answers, c.AnswerMs...)
			}
		}
		e.CallCount = bucketCount(read, h, h.Add(time.Hour), n)
		e.AnswerP50Ms = median(answers)
		out = append(out, e)
	}
	return out
}

// answersOf totals every measured answer. values lists them most recent call first, each call's in
// turn order, at most 5,000; the counts and percentiles use them all.
func answersOf(calls []store.StatsCall, budget float64) statsAnswers {
	out := statsAnswers{BudgetMs: budget, Values: []answerValue{}}
	var all []float64
	over := 0
	for i := len(calls) - 1; i >= 0; i-- {
		c := calls[i]
		for _, ms := range c.AnswerMs {
			all = append(all, ms)
			if ms > budget {
				over++
			}
			if len(out.Values) < maxStatsValues {
				out.Values = append(out.Values, answerValue{Ms: ptr(ms), CallID: c.ID, Room: c.Room, AgentKey: c.AgentKey})
			} else {
				out.ValuesTruncated = true
			}
		}
	}
	out.Turns, out.OverBudget = ptr(len(all)), ptr(over)
	out.P50Ms, out.P90Ms = median(all), p90(all)
	return out
}

// agentsOf is one row per agent key, most calls first, then the row of calls with no agent.
func agentsOf(calls []store.StatsCall) []statsAgent {
	type group struct {
		key    *string
		calls  []store.StatsCall
		models map[string]int
	}
	byKey := map[string]*group{}
	var none *group
	for _, c := range calls {
		var g *group
		if c.AgentKey == nil {
			if none == nil {
				none = &group{models: map[string]int{}}
			}
			g = none
		} else {
			if byKey[*c.AgentKey] == nil {
				byKey[*c.AgentKey] = &group{key: c.AgentKey, models: map[string]int{}}
			}
			g = byKey[*c.AgentKey]
		}
		g.calls = append(g.calls, c)
		for _, m := range c.Models {
			g.models[m]++
		}
	}
	groups := make([]*group, 0, len(byKey)+1)
	for _, g := range byKey {
		groups = append(groups, g)
	}
	slices.SortFunc(groups, func(a, b *group) int {
		return cmp.Or(cmp.Compare(len(b.calls), len(a.calls)), cmp.Compare(*a.key, *b.key))
	})
	if none != nil {
		groups = append(groups, none)
	}
	out := make([]statsAgent, 0, len(groups))
	for _, g := range groups {
		var answers []float64
		for _, c := range g.calls {
			answers = append(answers, c.AnswerMs...)
		}
		models := make([]string, 0, len(g.models))
		for m := range g.models {
			models = append(models, m)
		}
		slices.SortFunc(models, func(a, b string) int { return cmp.Or(cmp.Compare(g.models[b], g.models[a]), cmp.Compare(a, b)) })
		recent := g.calls
		if len(recent) > maxAgentCalls {
			recent = recent[len(recent)-maxAgentCalls:]
		}
		strip := make([]agentCall, 0, len(recent))
		for _, c := range recent {
			strip = append(strip, agentCall{CallID: c.ID, Room: c.Room, AnswerP50Ms: median(c.AnswerMs)})
		}
		out = append(out, statsAgent{
			Key: g.key, CallCount: ptr(len(g.calls)), AnswerTurns: ptr(len(answers)), AnswerP50Ms: median(answers),
			Models: models, Calls: strip, CallsTruncated: len(g.calls) > maxAgentCalls,
		})
	}
	return out
}

// median is the console's rule (features/call/call-model.ts): the upper-middle value; nil for none.
func median(xs []float64) *float64 {
	if len(xs) == 0 {
		return nil
	}
	s := slices.Sorted(slices.Values(xs))
	return &s[len(s)/2]
}

// p90 is the console's rule (mocks/call-stats.ts): the value at rank ceil(0.9 n); nil for none.
func p90(xs []float64) *float64 {
	if len(xs) == 0 {
		return nil
	}
	s := slices.Sorted(slices.Values(xs))
	return &s[min(len(s)-1, int(math.Ceil(float64(len(s))*0.9))-1)]
}

func ptr[T any](v T) *T { return &v }
