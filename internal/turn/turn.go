package turn

import (
	"cmp"
	"encoding/json"
	"math"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// Span names LiveKit Agents 1.8.7 emits.
const (
	spanAgentTurn    = "agent_turn"
	spanUserTurn     = "user_turn"
	spanLLMNode      = "llm_node"
	spanLLMRequest   = "llm_request"
	spanTTSNode      = "tts_node"
	spanTTSRequest   = "tts_request"
	spanFunctionTool = "function_tool"
	spanRealtime     = "realtime_inference"
)

// Attribute keys, named after the ATTR_* constants they mirror.
const (
	attrAgentTurnID          = "lk.generation_id"                     // ATTR_AGENT_TURN_ID
	attrE2ELatency           = "lk.e2e_latency"                       // ATTR_E2E_LATENCY
	attrTranscriptionDelay   = "lk.transcription_delay"               // ATTR_TRANSCRIPTION_DELAY
	attrEndOfTurnDelay       = "lk.end_of_turn_delay"                 // ATTR_END_OF_TURN_DELAY
	attrOnUserTurnCompleted  = "lk.on_user_turn_completed_delay"      // ATTR_ON_USER_TURN_COMPLETED_DELAY
	attrResponseTTFT         = "lk.response.ttft"                     // ATTR_RESPONSE_TTFT
	attrResponseTTFB         = "lk.response.ttfb"                     // ATTR_RESPONSE_TTFB
	attrSpeechInterrupted    = "lk.interrupted"                       // ATTR_SPEECH_INTERRUPTED
	attrLLMMetrics           = "lk.llm_metrics"                       // ATTR_LLM_METRICS
	attrTTSMetrics           = "lk.tts_metrics"                       // ATTR_TTS_METRICS
	attrRealtimeModelMetrics = "lk.realtime_model_metrics"            // ATTR_REALTIME_MODEL_METRICS
	attrInputTokens          = "gen_ai.usage.input_tokens"            //nolint:gosec // ATTR_GEN_AI_USAGE_INPUT_TOKENS, an attribute name, not a credential
	attrOutputTokens         = "gen_ai.usage.output_tokens"           //nolint:gosec // ATTR_GEN_AI_USAGE_OUTPUT_TOKENS, an attribute name, not a credential
	attrCacheReadTokens      = "gen_ai.usage.cache_read.input_tokens" //nolint:gosec // ATTR_GEN_AI_USAGE_CACHE_READ_INPUT_TOKENS, an attribute name, not a credential
	attrRequestModel         = "gen_ai.request.model"                 // ATTR_GEN_AI_REQUEST_MODEL
)

// Turn is one agent reply. Every pointer is nil when no source reported the value.
type Turn struct {
	SpanID       string
	GenerationID string
	Start, End   time.Time

	UserText, AgentText          *string
	STTModel, LLMModel, TTSModel *string

	// Milliseconds. TTFTMs is the first token of the generation the caller first heard: the last
	// llm_node starting before the turn's first tts_node. LLM1Ms and LLM2Ms split a tool turn
	// around its tools.
	E2EMs, TranscriptionDelayMs, EndOfTurnMs, HookMs   *float64
	TTFTMs, LLM1Ms, LLM2Ms, ToolsMs, TTSMs, RealtimeMs *float64

	Interrupted *bool

	// The turn's usage over the requests under it, each with how many did not report it.
	InputTokens, OutputTokens, CachedTokens, TTSCharacters Total[int64]
	TTSAudioSeconds, STTAudioSeconds                       Total[float64]
}

// Total is a call-wide usage total: the sum of what the requests reported, nil when none did, and
// how many requests did not report it.
type Total[T int64 | float64] struct {
	Sum     *T
	Missing int
}

func (t *Total[T]) add(v *T) {
	if v == nil {
		t.Missing++
		return
	}
	if t.Sum == nil {
		t.Sum = new(T)
	}
	*t.Sum += *v
}

// CallUsage is a call's usage over every request in it, inside a turn or not. STT audio seconds
// come from no span: Build leaves them nil and the reader fills them from the agent's usage
// counter, lk.agents.usage.stt_audio_duration.
type CallUsage struct {
	InputTokens, OutputTokens, CachedTokens, TTSCharacters Total[int64]
	TTSAudioSeconds, STTAudioSeconds                       Total[float64]
}

// Result is a call's turns, in start order, and its usage.
type Result struct {
	Turns []Turn
	Usage CallUsage
	// NoReply is true when a session report's chat_history.json was read and holds no assistant
	// message: then a call with no agent_turn span is known to have had no turn.
	NoReply bool
	// Live is the latest user and agent state an SDK reported (WithSDK); nil without one.
	Live *Live
}

// Build builds the turns of one call from its spans and, when it has one, its session report's
// chat_history.json (nil otherwise).
func Build(spans []store.Span, history []byte) Result {
	children := map[string][]store.Span{}
	var agentTurns, userTurns []store.Span
	var res Result
	for _, sp := range spans {
		children[sp.ParentSpanID] = append(children[sp.ParentSpanID], sp)
		switch sp.Name {
		case spanAgentTurn:
			agentTurns = append(agentTurns, sp)
		case spanUserTurn:
			userTurns = append(userTurns, sp)
		case spanLLMRequest:
			in, out, cached := tokens(sp.Attributes)
			res.Usage.InputTokens.add(in)
			res.Usage.OutputTokens.add(out)
			res.Usage.CachedTokens.add(cached)
		case spanTTSRequest:
			chars, audio := ttsUsage(sp.Attributes)
			res.Usage.TTSCharacters.add(chars)
			res.Usage.TTSAudioSeconds.add(audio)
		}
	}
	byStart := func(a, b store.Span) int { return a.Start.Compare(b.Start) }
	slices.SortStableFunc(agentTurns, byStart)
	slices.SortStableFunc(userTurns, byStart)

	for i, at := range agentTurns {
		var prevStart time.Time
		if i > 0 {
			prevStart = agentTurns[i-1].Start
		}
		res.Turns = append(res.Turns, build(at, descendants(at.SpanID, children), userTurnOf(at, prevStart, userTurns)))
	}
	msgs, reported := messages(history)
	res.NoReply = reported && !slices.ContainsFunc(msgs, func(m message) bool { return m.role == "assistant" })
	pairText(res.Turns, msgs)
	return res
}

// build reads one turn from its agent_turn span, the spans under it and the user_turn it answers.
func build(at store.Span, under []store.Span, ut *store.Span) Turn {
	t := Turn{
		SpanID:       at.SpanID,
		GenerationID: at.Attributes[attrAgentTurnID],
		Start:        at.Start,
		End:          at.End,
		E2EMs:        millis(at.Attributes, attrE2ELatency, unavailableNone),
		HookMs:       millis(at.Attributes, attrOnUserTurnCompleted, unavailableNone),
		Interrupted:  boolean(at.Attributes, attrSpeechInterrupted),
	}
	t.TranscriptionDelayMs = millis(at.Attributes, attrTranscriptionDelay, unavailableZero)
	t.EndOfTurnMs = millis(at.Attributes, attrEndOfTurnDelay, unavailableZero)
	if ut != nil {
		t.TranscriptionDelayMs = cmp.Or(t.TranscriptionDelayMs, millis(ut.Attributes, attrTranscriptionDelay, unavailableZero))
		t.EndOfTurnMs = cmp.Or(t.EndOfTurnMs, millis(ut.Attributes, attrEndOfTurnDelay, unavailableZero))
		t.STTModel = text(ut.Attributes, attrRequestModel)
	}

	var llmNodes, ttsNodes, tools []store.Span
	for _, sp := range under {
		switch sp.Name {
		case spanLLMNode:
			llmNodes = append(llmNodes, sp)
		case spanTTSNode:
			ttsNodes = append(ttsNodes, sp)
		case spanFunctionTool:
			tools = append(tools, sp)
		case spanLLMRequest:
			in, out, cached := tokens(sp.Attributes)
			t.InputTokens.add(in)
			t.OutputTokens.add(out)
			t.CachedTokens.add(cached)
		case spanTTSRequest:
			chars, audio := ttsUsage(sp.Attributes)
			t.TTSCharacters.add(chars)
			t.TTSAudioSeconds.add(audio)
		case spanRealtime:
			if t.RealtimeMs == nil {
				t.RealtimeMs = jsonMillis(sp.Attributes[attrRealtimeModelMetrics], "ttft")
			}
		}
	}

	if len(ttsNodes) > 0 {
		t.TTSMs = millis(ttsNodes[0].Attributes, attrResponseTTFB, unavailableNone)
		t.TTSModel = text(ttsNodes[0].Attributes, attrRequestModel)
	}
	never := time.Unix(math.MaxInt32, 0)
	firstTTSAfter := func(from time.Time) time.Time {
		for _, n := range ttsNodes {
			if !n.Start.Before(from) {
				return n.Start
			}
		}
		return never
	}
	if n := lastStartingBefore(llmNodes, time.Time{}, firstTTSAfter(time.Time{})); n != nil {
		t.TTFTMs = millis(n.Attributes, attrResponseTTFT, unavailableMinusOne)
		t.LLMModel = text(n.Attributes, attrRequestModel)
	}
	if len(tools) == 0 {
		t.LLM1Ms = t.TTFTMs
		return t
	}
	var toolMs float64
	for _, tool := range tools {
		if !tool.Start.IsZero() && !tool.End.IsZero() {
			toolMs += float64(tool.End.Sub(tool.Start)) / float64(time.Millisecond)
		}
	}
	t.ToolsMs = &toolMs
	firstTool, lastTool := tools[0].Start, tools[len(tools)-1].Start
	if n := lastStartingBefore(llmNodes, time.Time{}, firstTool); n != nil {
		t.LLM1Ms = millis(n.Attributes, attrResponseTTFT, unavailableMinusOne)
	}
	if n := lastStartingBefore(llmNodes, lastTool, firstTTSAfter(lastTool)); n != nil {
		t.LLM2Ms = millis(n.Attributes, attrResponseTTFT, unavailableMinusOne)
	}
	return t
}

// lastStartingBefore is the last span starting at or after from and before until: the generation
// that was spoken, when earlier ones in the same window were discarded.
func lastStartingBefore(spans []store.Span, from, until time.Time) *store.Span {
	var out *store.Span
	for i := range spans {
		if !spans[i].Start.Before(from) && spans[i].Start.Before(until) {
			out = &spans[i]
		}
	}
	return out
}

// descendants returns every span under id, at any depth, in start order.
func descendants(id string, children map[string][]store.Span) []store.Span {
	var out []store.Span
	stack := []string{id}
	for len(stack) > 0 {
		cur := stack[len(stack)-1]
		stack = stack[:len(stack)-1]
		for _, c := range children[cur] {
			out = append(out, c)
			stack = append(stack, c.SpanID)
		}
	}
	slices.SortStableFunc(out, func(a, b store.Span) int { return a.Start.Compare(b.Start) })
	return out
}

// userTurnOf is the user_turn an agent_turn answers: the last one starting after the previous
// agent_turn started and before this one did. With preemptive generation the user_turn can end
// after the agent_turn starts, so its end is not compared.
func userTurnOf(at store.Span, prevStart time.Time, userTurns []store.Span) *store.Span {
	return lastStartingBefore(userTurns, prevStart, at.Start)
}

// unavailable says which reported value means LiveKit could not measure it.
type unavailable int

const (
	unavailableNone     unavailable = iota
	unavailableZero                 // EOUMetrics delays: 0 when the end of speech was not detected
	unavailableMinusOne             // LLMMetrics and RealtimeModelMetrics ttft: -1 when no token came
)

// millis reads a seconds attribute as milliseconds; nil when absent, unparsable or the sentinel.
func millis(attrs map[string]string, key string, u unavailable) *float64 {
	s, ok := attrs[key]
	if !ok {
		return nil
	}
	v, err := strconv.ParseFloat(s, 64)
	if err != nil || (u == unavailableZero && v == 0) || (u == unavailableMinusOne && v == -1) {
		return nil
	}
	ms := v * 1000
	return &ms
}

// jsonMillis reads a seconds field of a metrics JSON attribute as milliseconds, -1 being unknown.
func jsonMillis(raw, field string) *float64 {
	var m map[string]json.Number
	if raw == "" || json.Unmarshal([]byte(raw), &m) != nil {
		return nil
	}
	n, ok := m[field]
	if !ok {
		return nil
	}
	v, err := n.Float64()
	if err != nil || v == -1 {
		return nil
	}
	ms := v * 1000
	return &ms
}

func boolean(attrs map[string]string, key string) *bool {
	v, err := strconv.ParseBool(attrs[key])
	if err != nil {
		return nil
	}
	return &v
}

func text(attrs map[string]string, key string) *string {
	if v := attrs[key]; v != "" {
		return &v
	}
	return nil
}

// tokens reads an llm_request's usage: the OpenTelemetry attributes, else lk.llm_metrics.
func tokens(attrs map[string]string) (in, out, cached *int64) {
	metrics := numbers(attrs[attrLLMMetrics])
	return cmp.Or(integer(attrs[attrInputTokens]), metrics.integer("prompt_tokens")),
		cmp.Or(integer(attrs[attrOutputTokens]), metrics.integer("completion_tokens")),
		cmp.Or(integer(attrs[attrCacheReadTokens]), metrics.integer("prompt_cached_tokens"))
}

// ttsUsage reads a tts_request's characters and audio seconds from lk.tts_metrics; an
// audio_duration of 0 is TTSMetrics' unknown.
func ttsUsage(attrs map[string]string) (chars *int64, audio *float64) {
	m := numbers(attrs[attrTTSMetrics])
	chars = m.integer("characters_count")
	if a := m.float("audio_duration"); a != nil && *a != 0 {
		audio = a
	}
	return chars, audio
}

type jsonNumbers map[string]json.RawMessage

func numbers(raw string) jsonNumbers {
	var m jsonNumbers
	if raw == "" || json.Unmarshal([]byte(raw), &m) != nil {
		return nil
	}
	return m
}

func (m jsonNumbers) float(key string) *float64 {
	var v float64
	if raw, ok := m[key]; !ok || json.Unmarshal(raw, &v) != nil {
		return nil
	}
	return &v
}

func (m jsonNumbers) integer(key string) *int64 {
	f := m.float(key)
	if f == nil || *f != math.Trunc(*f) {
		return nil
	}
	v := int64(*f)
	return &v
}

func integer(s string) *int64 {
	v, err := strconv.ParseInt(s, 10, 64)
	if err != nil {
		return nil
	}
	return &v
}

// message is a chat_history.json message.
type message struct {
	role    string
	text    string
	created time.Time
}

// messages reads the chat messages of a chat_history.json, in order, and whether there was a
// history to read.
func messages(history []byte) ([]message, bool) {
	var doc struct {
		Items []struct {
			Type      string            `json:"type"`
			Role      string            `json:"role"`
			Content   []json.RawMessage `json:"content"`
			CreatedAt float64           `json:"created_at"`
		} `json:"items"`
	}
	if t := strings.TrimSpace(string(history)); t == "" || t == "null" || json.Unmarshal(history, &doc) != nil {
		return nil, false
	}
	var out []message
	for _, it := range doc.Items {
		if it.Type != "message" || it.CreatedAt <= 0 {
			continue
		}
		var parts []string
		for _, c := range it.Content {
			var s string
			if json.Unmarshal(c, &s) == nil && s != "" {
				parts = append(parts, s)
			}
		}
		sec, frac := math.Modf(it.CreatedAt)
		out = append(out, message{role: it.Role, text: strings.Join(parts, " "), created: time.Unix(int64(sec), int64(frac*1e9)).UTC()})
	}
	return out, true
}

// pairText gives each turn the assistant messages created inside its agent_turn, joined in order (a
// tool turn speaks before and after its tool), and the user messages created after the previous
// turn's last assistant message and up to this turn's last one. A turn with no assistant message,
// one interrupted before it spoke, takes user messages only up to its own start, so an interruption
// belongs to the turn that answers it. A turn with no reported start or end gets no text.
func pairText(turns []Turn, msgs []message) {
	used := map[int]bool{}
	var after time.Time
	for i := range turns {
		t := &turns[i]
		if t.Start.IsZero() || t.End.IsZero() {
			continue
		}
		until := t.Start
		var agent []string
		for k, m := range msgs {
			if m.role == "assistant" && !used[k] && !m.created.Before(t.Start) && !m.created.After(t.End) {
				used[k] = true
				if m.text != "" {
					agent = append(agent, m.text)
				}
				until = m.created
			}
		}
		var user []string
		for _, m := range msgs {
			if m.role == "user" && m.created.After(after) && !m.created.After(until) && m.text != "" {
				user = append(user, m.text)
			}
		}
		t.AgentText = nonEmpty(strings.Join(agent, " "))
		t.UserText = nonEmpty(strings.Join(user, " "))
		if until.After(after) {
			after = until
		}
	}
}

func nonEmpty(s string) *string {
	if s == "" {
		return nil
	}
	return &s
}
