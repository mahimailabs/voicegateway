package api

import (
	"context"
	"fmt"
	"net/http"
	"net/url"
	"testing"
	"time"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// statsCall stores a call that started at start, with a participant for its channel (phone, web,
// or none for unknown), and a summary with its agent and measured answers. It returns the call id.
func (f *fixture) statsCall(start time.Time, channel string, agent string, answers ...float64) string {
	f.t.Helper()
	ctx := context.Background()
	var id string
	if err := f.pool.QueryRow(ctx, `INSERT INTO calls (organisation_id, project_id, room_name, room_sid, started_at)
		VALUES ('default', $1, 'room-' || gen_random_uuid(), 'RM_' || gen_random_uuid(), $2) RETURNING id::text`, f.project, start).Scan(&id); err != nil {
		f.t.Fatal(err)
	}
	kind := map[string]string{"phone": "sip", "web": "standard"}[channel]
	if kind != "" {
		if _, err := f.pool.Exec(ctx, `INSERT INTO call_participants (call_id, participant_sid, kind) VALUES ($1::uuid, 'PA_' || $1::text, $2)`, id, kind); err != nil {
			f.t.Fatal(err)
		}
	}
	var key *string
	if agent != "" {
		key = &agent
	}
	var answerMs []float64
	if len(answers) > 0 {
		answerMs = answers
	}
	if _, err := f.pool.Exec(ctx, `INSERT INTO call_summaries (call_id, turn_count, answer_ms, agent_key, models, computed_at) VALUES ($1, $2, $3, $4, '{}', now())`,
		id, len(answers), answerMs, key); err != nil {
		f.t.Fatal(err)
	}
	return id
}

// connectedAt sets when the test's project was connected.
func (f *fixture) connectedAt(t time.Time) {
	f.t.Helper()
	if _, err := f.pool.Exec(context.Background(), `UPDATE projects SET created_at = $1 WHERE organisation_id = 'default' AND id = $2`, t, f.project); err != nil {
		f.t.Fatal(err)
	}
}

func (f *fixture) statsAPI() *API {
	a := f.api(nil)
	a.Stats = f.pg
	return a
}

func statsPath(project string, from, to time.Time) string {
	q := url.Values{"from": {from.Format(time.RFC3339)}, "to": {to.Format(time.RFC3339)}}
	return "/api/v1/org/default/project/" + project + "/call-stats?" + q.Encode()
}

type statsBody struct {
	CallCount *int `json:"callCount"`
	ByChannel map[string]*int
	ByDay     []struct {
		Date      string
		CallCount *int
	}
	ByHour []struct {
		Hour        string
		CallCount   *int
		AnswerP50Ms *float64
	}
	Answers struct {
		Turns, OverBudget *int
		P50Ms, P90Ms      *float64
		Values            []struct{ Ms *float64 }
		ValuesTruncated   bool
	}
	Agents []struct {
		Key            *string
		CallCount      *int
		AnswerP50Ms    *float64
		Calls          []struct{ CallID string }
		CallsTruncated bool
	}
}

func val[T any](p *T) string {
	if p == nil {
		return "null"
	}
	return fmt.Sprint(*p)
}

// A day well in the past, at midnight UTC, so every bucket in these tests is before now.
var statsDay0 = time.Now().UTC().Add(-20 * 24 * time.Hour).Truncate(24 * time.Hour)

// Acceptance check 1 of phase 04e.
func TestCallStatsCounts(t *testing.T) {
	f := newFixture(t)
	f.connectedAt(statsDay0.Add(-24 * time.Hour))
	d1, d3 := statsDay0.Add(time.Hour), statsDay0.Add(2*24*time.Hour+time.Hour)
	f.statsCall(d1, "phone", "alpha")
	f.statsCall(d1, "phone", "alpha")
	f.statsCall(d1, "web", "beta")
	f.statsCall(d3, "phone", "alpha")
	f.statsCall(d3, "web", "beta")
	f.statsCall(d3, "", "")
	// An unservable call: no room and no start.
	if _, err := f.pool.Exec(context.Background(), `INSERT INTO calls (organisation_id, project_id, job_id) VALUES ('default', $1, 'AJ_unservable')`, f.project); err != nil {
		t.Fatal(err)
	}
	var s statsBody
	if code := f.get(f.statsAPI(), statsPath(f.project, statsDay0, statsDay0.Add(3*24*time.Hour)), &s); code != http.StatusOK {
		t.Fatalf("status %d", code)
	}
	if val(s.CallCount) != "6" {
		t.Errorf("callCount = %s, want 6", val(s.CallCount))
	}
	ch := s.ByChannel
	if got := fmt.Sprintf("%s/%s/%s console %s local %s", val(ch["phone"]), val(ch["web"]), val(ch["unknown"]), val(ch["console"]), val(ch["local"])); got != "3/2/1 console null local null" {
		t.Errorf("byChannel = %s", got)
	}
	var days []string
	for _, d := range s.ByDay {
		days = append(days, val(d.CallCount))
	}
	if fmt.Sprint(days) != "[3 0 3]" {
		t.Errorf("byDay = %v, want [3 0 3]", days)
	}
	var agents []string
	sum := 0
	for _, a := range s.Agents {
		agents = append(agents, val(a.Key)+":"+val(a.CallCount))
		sum += *a.CallCount
	}
	if fmt.Sprint(agents) != "[alpha:3 beta:2 null:1]" || sum != 6 {
		t.Errorf("agents = %v (sum %d), want [alpha:3 beta:2 null:1]", agents, sum)
	}
}

// Acceptance check 2 of phase 04e.
func TestCallStatsAnswers(t *testing.T) {
	f := newFixture(t)
	f.connectedAt(statsDay0.Add(-24 * time.Hour))
	f.statsCall(statsDay0.Add(time.Hour), "web", "alpha", 100, 200)
	f.statsCall(statsDay0.Add(2*time.Hour), "web", "beta", 300, 400)
	a := f.statsAPI()
	if _, err := f.pg.UpdateProjectSettings(context.Background(), "default", f.project, 0, store.Thresholds{AnswerMs: 250, JoinMs: 2000, TTSStartMs: 1000}); err != nil {
		t.Fatal(err)
	}
	var s statsBody
	if code := f.get(a, statsPath(f.project, statsDay0, statsDay0.Add(24*time.Hour)), &s); code != http.StatusOK {
		t.Fatalf("status %d", code)
	}
	if got := fmt.Sprintf("turns %s over %s p50 %s p90 %s", val(s.Answers.Turns), val(s.Answers.OverBudget), val(s.Answers.P50Ms), val(s.Answers.P90Ms)); got != "turns 4 over 2 p50 300 p90 400" {
		t.Errorf("answers = %s, want turns 4 over 2 p50 300 p90 400", got)
	}
	var p50s []string
	for _, ag := range s.Agents {
		p50s = append(p50s, val(ag.Key)+":"+val(ag.AnswerP50Ms))
	}
	if fmt.Sprint(p50s) != "[alpha:200 beta:400]" {
		t.Errorf("agent medians = %v, want [alpha:200 beta:400]", p50s)
	}
	var order []string
	for _, v := range s.Answers.Values {
		order = append(order, val(v.Ms))
	}
	if fmt.Sprint(order) != "[300 400 100 200]" {
		t.Errorf("values = %v, want [300 400 100 200]: the most recent call first, each call's turns in order", order)
	}

	many := make([]float64, 5001)
	for i := range many {
		many[i] = float64(i)
	}
	f.statsCall(statsDay0.Add(3*time.Hour), "web", "alpha", many...)
	if code := f.get(a, statsPath(f.project, statsDay0, statsDay0.Add(24*time.Hour)), &s); code != http.StatusOK {
		t.Fatalf("status %d", code)
	}
	if len(s.Answers.Values) != 5000 || !s.Answers.ValuesTruncated || val(s.Answers.Turns) != "5005" {
		t.Errorf("values %d, truncated %v, turns %s; want 5000, true, 5005", len(s.Answers.Values), s.Answers.ValuesTruncated, val(s.Answers.Turns))
	}
}

// Check 2, the percentile rule exactly: over 1 to 10 the console's p90 is the value at rank
// ceil(0.9 n) = 9, which a max, floor or rounding rule would put at 10; its median is the
// upper-middle value, 6.
func TestCallStatsPercentileRule(t *testing.T) {
	f := newFixture(t)
	f.connectedAt(statsDay0.Add(-24 * time.Hour))
	f.statsCall(statsDay0.Add(time.Hour), "web", "alpha", 1, 2, 3, 4, 5, 6, 7, 8, 9, 10)
	var s statsBody
	if code := f.get(f.statsAPI(), statsPath(f.project, statsDay0, statsDay0.Add(24*time.Hour)), &s); code != http.StatusOK {
		t.Fatalf("status %d", code)
	}
	if got := val(s.Answers.P50Ms) + "/" + val(s.Answers.P90Ms); got != "6/9" {
		t.Errorf("p50/p90 = %s, want 6/9", got)
	}
}

// An agent's strip holds its most recent 500 calls, oldest first, and says it was cut; callCount
// counts all of them. A call starting exactly at the range's end is outside it.
func TestCallStatsAgentStripAndExclusiveEnd(t *testing.T) {
	f := newFixture(t)
	f.connectedAt(statsDay0.Add(-24 * time.Hour))
	var ids []string
	for i := range 501 {
		ids = append(ids, f.statsCall(statsDay0.Add(time.Duration(i)*time.Minute), "web", "alpha"))
	}
	end := statsDay0.Add(24 * time.Hour)
	f.statsCall(end, "web", "alpha")
	var s statsBody
	if code := f.get(f.statsAPI(), statsPath(f.project, statsDay0, end), &s); code != http.StatusOK {
		t.Fatalf("status %d", code)
	}
	if val(s.CallCount) != "501" {
		t.Errorf("callCount = %s, want 501: a call at the range's end is outside it", val(s.CallCount))
	}
	a := s.Agents[0]
	if len(a.Calls) != 500 || !a.CallsTruncated || val(a.CallCount) != "501" || a.Calls[0].CallID != ids[1] || a.Calls[499].CallID != ids[500] {
		t.Errorf("strip: %d calls, truncated %v, callCount %s; want the most recent 500, oldest first, truncated, of 501", len(a.Calls), a.CallsTruncated, val(a.CallCount))
	}
}

// Acceptance check 3 of phase 04e.
func TestCallStatsByHour(t *testing.T) {
	f := newFixture(t)
	f.connectedAt(statsDay0.Add(-24 * time.Hour))
	h0 := statsDay0.Add(5 * time.Hour)
	f.statsCall(h0.Add(10*time.Minute), "web", "alpha", 500)
	f.statsCall(h0.Add(2*time.Hour+10*time.Minute), "web", "alpha", 700)
	var s statsBody
	if code := f.get(f.statsAPI(), statsPath(f.project, h0, h0.Add(3*time.Hour)), &s); code != http.StatusOK {
		t.Fatalf("status %d", code)
	}
	var hours []string
	for _, h := range s.ByHour {
		hours = append(hours, val(h.CallCount)+"/"+val(h.AnswerP50Ms))
	}
	if fmt.Sprint(hours) != "[1/500 0/null 1/700]" {
		t.Errorf("byHour = %v, want [1/500 0/null 1/700]", hours)
	}

	to := statsDay0.Add(10 * 24 * time.Hour)
	if code := f.get(f.statsAPI(), statsPath(f.project, statsDay0, to), &s); code != http.StatusOK {
		t.Fatalf("status %d", code)
	}
	if len(s.ByHour) != 168 || s.ByHour[167].Hour != to.Add(-time.Hour).Format(time.RFC3339) {
		t.Errorf("10-day byHour: %d entries ending %s; want 168 ending %s", len(s.ByHour), s.ByHour[len(s.ByHour)-1].Hour, to.Add(-time.Hour).Format(time.RFC3339))
	}

	// The current hour counts the calls so far; the 2 hours to come are null.
	now := time.Now().UTC().Truncate(time.Hour)
	if code := f.get(f.statsAPI(), statsPath(f.project, now, now.Add(3*time.Hour)), &s); code != http.StatusOK {
		t.Fatalf("status %d", code)
	}
	hours = nil
	for _, h := range s.ByHour {
		hours = append(hours, val(h.CallCount))
	}
	if fmt.Sprint(hours) != "[0 null null]" {
		t.Errorf("hours from now = %v, want [0 null null]", hours)
	}
}

// Acceptance check 3a of phase 04e.
func TestCallStatsBeforeTheProject(t *testing.T) {
	f := newFixture(t)
	f.connectedAt(statsDay0.Add(2 * 24 * time.Hour))
	var s statsBody
	if code := f.get(f.statsAPI(), statsPath(f.project, statsDay0, statsDay0.Add(4*24*time.Hour)), &s); code != http.StatusOK {
		t.Fatalf("status %d", code)
	}
	var days []string
	for _, d := range s.ByDay {
		days = append(days, val(d.CallCount))
	}
	if fmt.Sprint(days) != "[null null 0 0]" {
		t.Errorf("byDay = %v, want [null null 0 0]", days)
	}
	// A partial bucket counts the calls so far: the project connected mid-day still shows its 0.
	f.connectedAt(statsDay0.Add(2*24*time.Hour + 6*time.Hour))
	if code := f.get(f.statsAPI(), statsPath(f.project, statsDay0, statsDay0.Add(4*24*time.Hour)), &s); code != http.StatusOK {
		t.Fatalf("status %d", code)
	}
	days = nil
	for _, d := range s.ByDay {
		days = append(days, val(d.CallCount))
	}
	if fmt.Sprint(days) != "[null null 0 0]" {
		t.Errorf("byDay connected mid-day = %v, want [null null 0 0]", days)
	}
}

// Acceptance check 4 of phase 04e: what this server does not compute is null, never empty or 0.
func TestCallStatsUnknownsStayUnknown(t *testing.T) {
	f := newFixture(t)
	f.connectedAt(statsDay0.Add(-24 * time.Hour))
	f.statsCall(statsDay0.Add(time.Hour), "web", "alpha", 400)
	f.statsCall(statsDay0.Add(2*time.Hour), "", "", 500)
	var raw map[string]any
	if code := f.get(f.statsAPI(), statsPath(f.project, statsDay0, statsDay0.Add(24*time.Hour)), &raw); code != http.StatusOK {
		t.Fatalf("status %d", code)
	}
	for _, k := range []string{"stages", "chain", "anatomyTurns"} {
		if raw[k] != nil {
			t.Errorf("%s = %v, want null", k, raw[k])
		}
	}
	for k, v := range raw["issueCounts"].(map[string]any) {
		if v != nil {
			t.Errorf("issueCounts.%s = %v, want null", k, v)
		}
	}
	agents := raw["agents"].([]any)
	if len(agents) != 2 {
		t.Fatalf("agents = %d, want alpha and the row with no agent", len(agents))
	}
	for _, a := range agents {
		agent := a.(map[string]any)
		for _, k := range []string{"stages", "chain", "providers", "issues", "anatomyTurns", "name"} {
			if agent[k] != nil {
				t.Errorf("agent %v %s = %v, want null", agent["key"], k, agent[k])
			}
		}
	}
}

// gateAs lets every request in as a signed-in user with these organisation roles.
func gateAs(roles map[string]string) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			next.ServeHTTP(w, r.WithContext(store.WithPrincipal(r.Context(), store.Principal{UserID: "u-test", Roles: roles})))
		})
	}
}

// Acceptance check 5 of phase 04e.
func TestCallStatsRange(t *testing.T) {
	f := newFixture(t)
	f.connectedAt(statsDay0.Add(-24 * time.Hour))
	f.statsCall(statsDay0.Add(time.Hour), "web", "alpha")
	other := f.project
	f.project = f.addProject(t.Name() + "-other")
	f.connectedAt(statsDay0.Add(-24 * time.Hour))
	f.statsCall(statsDay0.Add(time.Hour), "web", "alpha")
	f.statsCall(statsDay0.Add(2*time.Hour), "web", "alpha")

	base := "/api/v1/org/default/project/" + other + "/call-stats"
	for name, q := range map[string]string{
		"no from":       "?to=" + url.QueryEscape(statsDay0.Format(time.RFC3339)),
		"to before":     "?from=" + url.QueryEscape(statsDay0.Format(time.RFC3339)) + "&to=" + url.QueryEscape(statsDay0.Add(-time.Hour).Format(time.RFC3339)),
		"over 400 days": "?from=" + url.QueryEscape(statsDay0.Add(-401*24*time.Hour).Format(time.RFC3339)) + "&to=" + url.QueryEscape(statsDay0.Format(time.RFC3339)),
	} {
		if code := f.get(f.statsAPI(), base+q, nil); code != http.StatusBadRequest {
			t.Errorf("%s = %d, want 400", name, code)
		}
	}
	var s statsBody
	if code := f.get(f.statsAPI(), statsPath(other, statsDay0, statsDay0.Add(24*time.Hour)), &s); code != http.StatusOK || val(s.CallCount) != "1" {
		t.Errorf("one project = %d with %s calls, want 200 with 1: another project's calls never count", code, val(s.CallCount))
	}
	viewer := f.statsAPI()
	viewer.Gate = gateAs(map[string]string{"default": "viewer"})
	if code := f.get(viewer, statsPath(other, statsDay0, statsDay0.Add(24*time.Hour)), nil); code != http.StatusOK {
		t.Errorf("viewer = %d, want 200", code)
	}
	outsider := f.statsAPI()
	outsider.Gate = gateAs(map[string]string{"elsewhere": "admin"})
	if code := f.get(outsider, statsPath(other, statsDay0, statsDay0.Add(24*time.Hour)), nil); code != http.StatusForbidden {
		t.Errorf("non-member = %d, want 403", code)
	}
}
