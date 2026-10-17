package turn

import (
	"bufio"
	"encoding/json"
	"math"
	"os"
	"strconv"
	"testing"
	"time"

	"github.com/mahimailabs/voicegateway/internal/event"
	"github.com/mahimailabs/voicegateway/internal/store"
)

// consoleCapture reads dashboard/src/mocks/capture-session.json: a real LiveKit Agents 1.8.6 call
// with its spans and the turns the console normalised from them.
func consoleCapture(t *testing.T) ([]store.Span, []map[string]any, map[string]any) {
	t.Helper()
	b, err := os.ReadFile("../../dashboard/src/mocks/capture-session.json")
	if err != nil {
		t.Fatal(err)
	}
	var doc struct {
		Call struct {
			Spans []struct {
				Name       string            `json:"name"`
				SpanID     string            `json:"spanId"`
				ParentID   *string           `json:"parentId"`
				StartMs    float64           `json:"startMs"`
				DurationMs float64           `json:"durationMs"`
				Attrs      map[string]string `json:"attrs"`
			} `json:"spans"`
			Turns []map[string]any `json:"turns"`
			Usage map[string]any   `json:"usage"`
		} `json:"call"`
	}
	if err := json.Unmarshal(b, &doc); err != nil {
		t.Fatal(err)
	}
	base := time.Date(2026, 10, 14, 0, 0, 0, 0, time.UTC)
	at := func(ms float64) time.Time { return base.Add(time.Duration(ms * float64(time.Millisecond))) }
	var spans []store.Span
	for _, s := range doc.Call.Spans {
		parent := ""
		if s.ParentID != nil {
			parent = *s.ParentID
		}
		spans = append(spans, store.Span{
			Name: s.Name, SpanID: s.SpanID, ParentSpanID: parent,
			Start: at(s.StartMs), End: at(s.StartMs + s.DurationMs), Attributes: s.Attrs,
		})
	}
	return spans, doc.Call.Turns, doc.Call.Usage
}

func near(got *float64, want any) bool {
	if want == nil {
		return got == nil
	}
	w, ok := want.(float64)
	return ok && got != nil && math.Abs(*got-w) <= 0.1
}

func show(v *float64) string {
	if v == nil {
		return "null"
	}
	return strconv.FormatFloat(*v, 'f', 1, 64)
}

// Acceptance check 1.
func TestBuildMatchesConsoleCapture(t *testing.T) {
	spans, want, _ := consoleCapture(t)
	if len(spans) != 126 {
		t.Fatalf("fixture spans = %d, want 126", len(spans))
	}
	got := Build(spans, nil).Turns
	if len(got) != 5 || len(want) != 5 {
		t.Fatalf("turns = %d, fixture = %d; want 5", len(got), len(want))
	}
	for i := range got {
		fields := []struct {
			name string
			got  *float64
		}{
			{"ttftMs", got[i].TTFTMs},
			{"ttsMs", got[i].TTSMs},
			{"endOfTurnMs", got[i].EndOfTurnMs},
			{"transcriptionDelayMs", got[i].TranscriptionDelayMs},
			{"e2eMs", got[i].E2EMs},
		}
		for _, f := range fields {
			if !near(f.got, want[i][f.name]) {
				t.Errorf("turn %d %s = %s, want %v", i+1, f.name, show(f.got), want[i][f.name])
			}
		}
	}
}

// Acceptance check 2.
func TestUsageMatchesConsoleCapture(t *testing.T) {
	spans, _, want := consoleCapture(t)
	u := Build(spans, nil).Usage
	if u.InputTokens.Sum == nil || float64(*u.InputTokens.Sum) != want["llmInputTokens"] || u.InputTokens.Missing != 0 {
		t.Errorf("input tokens = %+v, want %v with 0 missing", u.InputTokens, want["llmInputTokens"])
	}
	if u.OutputTokens.Sum == nil || float64(*u.OutputTokens.Sum) != want["llmOutputTokens"] || u.OutputTokens.Missing != 0 {
		t.Errorf("output tokens = %+v, want %v with 0 missing", u.OutputTokens, want["llmOutputTokens"])
	}
}

// span builds a test span starting at offset ms and lasting dur ms.
func span(name, id, parent string, offset, dur float64, attrs map[string]string) store.Span {
	base := time.Date(2026, 10, 17, 17, 0, 0, 0, time.UTC)
	start := base.Add(time.Duration(offset * float64(time.Millisecond)))
	return store.Span{Name: name, SpanID: id, ParentSpanID: parent, Start: start, End: start.Add(time.Duration(dur * float64(time.Millisecond))), Attributes: attrs}
}

// Acceptance check 3.
func TestSentinelsBecomeNull(t *testing.T) {
	r := Build([]store.Span{
		span("user_turn", "u", "s", 0, 900, map[string]string{"lk.end_of_turn_delay": "0", "lk.transcription_delay": "0.25"}),
		span("agent_turn", "a", "s", 1000, 2000, nil),
		span("llm_node", "l", "a", 1001, 500, map[string]string{"lk.response.ttft": "-1"}),
		span("tts_node", "t", "a", 1600, 500, map[string]string{"lk.response.ttfb": "0"}),
		span("tts_request", "tr", "t", 1600, 500, map[string]string{"lk.tts_metrics": `{"characters_count":12,"audio_duration":0}`}),
	}, nil)
	tu := r.Turns[0]
	if tu.TTFTMs != nil || tu.EndOfTurnMs != nil || tu.TTSAudioSeconds != nil {
		t.Errorf("sentinels = ttft %s, end of turn %s, tts audio %s; want all null", show(tu.TTFTMs), show(tu.EndOfTurnMs), show(tu.TTSAudioSeconds))
	}
	if tu.TTSMs == nil || *tu.TTSMs != 0 {
		t.Errorf("ttfb 0 = %s, want a measured 0", show(tu.TTSMs))
	}
	if !near(tu.TranscriptionDelayMs, 250.0) || tu.TTSCharacters == nil || *tu.TTSCharacters != 12 {
		t.Errorf("transcription delay %s, characters %v; want 250 and 12", show(tu.TranscriptionDelayMs), tu.TTSCharacters)
	}
}

// Acceptance check 4.
func TestToolTurnSplitsLLM(t *testing.T) {
	r := Build([]store.Span{
		span("agent_turn", "a1", "s", 0, 5000, nil),
		span("llm_node", "l1", "a1", 1, 400, map[string]string{"lk.response.ttft": "0.3"}),
		span("function_tool", "f", "a1", 450, 250, nil),
		span("llm_node", "l2", "a1", 710, 400, map[string]string{"lk.response.ttft": "0.2"}),
		span("tts_node", "t1", "a1", 900, 1000, map[string]string{"lk.response.ttfb": "0.15"}),
		span("agent_turn", "a2", "s", 6000, 3000, nil),
		span("llm_node", "l3", "a2", 6001, 400, map[string]string{"lk.response.ttft": "0.4"}),
		span("tts_node", "t2", "a2", 6300, 1000, nil),
	}, nil)
	tool, plain := r.Turns[0], r.Turns[1]
	if !near(tool.LLM1Ms, 300.0) || !near(tool.LLM2Ms, 200.0) || !near(tool.ToolsMs, 250.0) || !near(tool.TTFTMs, 200.0) {
		t.Errorf("tool turn llm1 %s, llm2 %s, tools %s, ttft %s; want 300, 200, 250, 200", show(tool.LLM1Ms), show(tool.LLM2Ms), show(tool.ToolsMs), show(tool.TTFTMs))
	}
	if plain.ToolsMs != nil || plain.LLM2Ms != nil || !near(plain.LLM1Ms, 400.0) {
		t.Errorf("plain turn tools %s, llm2 %s, llm1 %s; want null, null, 400", show(plain.ToolsMs), show(plain.LLM2Ms), show(plain.LLM1Ms))
	}
}

// Acceptance check 5.
func TestUsageCountsMissingNotZero(t *testing.T) {
	req := func(id string, attrs map[string]string) store.Span { return span("llm_request", id, "x", 0, 1, attrs) }
	r := Build([]store.Span{
		req("r1", map[string]string{"gen_ai.usage.input_tokens": "100"}),
		req("r2", map[string]string{"lk.llm_metrics": `{"prompt_tokens":50,"completion_tokens":5}`}),
		req("r3", nil),
	}, nil)
	if in := r.Usage.InputTokens; in.Sum == nil || *in.Sum != 150 || in.Missing != 1 {
		t.Errorf("input tokens = %+v, want 150 with 1 missing", in)
	}
	none := Build([]store.Span{req("r1", nil)}, nil).Usage
	if none.InputTokens.Sum != nil || none.InputTokens.Missing != 1 {
		t.Errorf("unreported input tokens = %+v, want null with 1 missing", none.InputTokens)
	}
	if none.STTAudioSeconds.Sum != nil {
		t.Errorf("STT audio = %v, want null", *none.STTAudioSeconds.Sum)
	}
}

// history encodes chat_history.json messages: role, text, seconds after the test base.
func history(t *testing.T, msgs ...[3]string) []byte {
	t.Helper()
	base := float64(time.Date(2026, 10, 17, 17, 0, 0, 0, time.UTC).Unix())
	var items []map[string]any
	for _, m := range msgs {
		at, err := strconv.ParseFloat(m[2], 64)
		if err != nil {
			t.Fatal(err)
		}
		items = append(items, map[string]any{"type": "message", "role": m[0], "content": []string{m[1]}, "created_at": base + at})
	}
	b, err := json.Marshal(map[string]any{"items": items})
	if err != nil {
		t.Fatal(err)
	}
	return b
}

func str(s *string) string {
	if s == nil {
		return "<null>"
	}
	return *s
}

// Acceptance check 6.
func TestTextPairsByTime(t *testing.T) {
	r := Build([]store.Span{
		span("agent_turn", "a1", "s", 1000, 2000, nil),
		span("agent_turn", "a2", "s", 5000, 2000, nil),
		span("agent_turn", "a3", "s", 9000, 1000, nil),
	}, history(t,
		[3]string{"user", "hello", "0.5"},
		[3]string{"assistant", "hi there", "1.5"},
		[3]string{"user", "opening hours?", "4.5"},
		[3]string{"assistant", "nine to five", "5.5"},
	))
	want := [][2]string{{"hello", "hi there"}, {"opening hours?", "nine to five"}, {"<null>", "<null>"}}
	for i, w := range want {
		if got := [2]string{str(r.Turns[i].UserText), str(r.Turns[i].AgentText)}; got != w {
			t.Errorf("turn %d text = %q, want %q", i+1, got, w)
		}
	}
}

// realCall reads the 2026-10-17 LiveKit Agents 1.8.7 call on the LiveKit Cloud test project: its
// stored spans and session report. The caller was synthetic, so the text is scripted.
func realCall(t *testing.T) ([]store.Span, []byte) {
	t.Helper()
	f, err := os.Open("testdata/agents-1.8.7-call.spans.jsonl")
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = f.Close() }()
	var spans []store.Span
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 1<<20), 1<<24)
	for sc.Scan() {
		var row struct {
			TraceID    string            `json:"trace_id"`
			SpanID     string            `json:"span_id"`
			ParentID   string            `json:"parent_span_id"`
			Name       string            `json:"name"`
			Start      time.Time         `json:"start"`
			End        time.Time         `json:"end"`
			Attributes map[string]string `json:"attributes"`
		}
		if err := json.Unmarshal(sc.Bytes(), &row); err != nil {
			t.Fatal(err)
		}
		spans = append(spans, store.Span{TraceID: row.TraceID, SpanID: row.SpanID, ParentSpanID: row.ParentID, Name: row.Name, Start: row.Start, End: row.End, Attributes: row.Attributes})
	}
	if err := sc.Err(); err != nil {
		t.Fatal(err)
	}
	b, err := os.ReadFile("testdata/agents-1.8.7-call.session_report.json")
	if err != nil {
		t.Fatal(err)
	}
	var report event.SessionReport
	if err := json.Unmarshal(b, &report); err != nil {
		t.Fatal(err)
	}
	return spans, []byte(report.ChatHistory)
}

// Acceptance check 9.
func TestRealCallPreemptiveGeneration(t *testing.T) {
	spans, hist := realCall(t)
	r := Build(spans, hist)
	if len(r.Turns) != 3 {
		t.Fatalf("turns = %d, want 3", len(r.Turns))
	}
	third := r.Turns[2]
	if !near(third.TTFTMs, 342.1) || !near(third.LLM1Ms, 342.1) {
		t.Errorf("turn 3 ttft %s, llm1 %s; want 342.1 (the spoken generation), not 472.6", show(third.TTFTMs), show(third.LLM1Ms))
	}
	if third.InputTokens == nil || *third.InputTokens != 67+73 {
		t.Errorf("turn 3 input tokens = %v, want 140 (both requests)", third.InputTokens)
	}
	for i, tu := range r.Turns {
		if tu.AgentText == nil {
			t.Errorf("turn %d has no agent text", i+1)
		}
		if (i == 0) != (tu.UserText == nil) {
			t.Errorf("turn %d user text = %q; want none for the greeting only", i+1, str(tu.UserText))
		}
	}
}
