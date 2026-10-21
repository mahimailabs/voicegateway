package turn

import (
	"encoding/json"
	"math"
	"sort"
	"time"

	"github.com/mahimailabs/voicegateway/internal/event"
)

// LiveState is the latest state an SDK reported for one side of a call (docs/specs/python-sdk.md).
type LiveState struct {
	State string
	At    time.Time
}

// Live is the latest user and agent state of a call; nil when the SDK never reported it.
type Live struct {
	User, Agent *LiveState
}

// sdkMessageMetrics is the part of LiveKit Agents' per-message MetricsReport this reads, in
// seconds; a key the report left out stays nil.
type sdkMessageMetrics struct {
	StartedSpeakingAt  *float64     `json:"started_speaking_at"`
	StoppedSpeakingAt  *float64     `json:"stopped_speaking_at"`
	TranscriptionDelay *float64     `json:"transcription_delay"`
	EndOfTurnDelay     *float64     `json:"end_of_turn_delay"`
	LLMTTFT            *float64     `json:"llm_node_ttft"`
	TTSTTFB            *float64     `json:"tts_node_ttfb"`
	LLMMetadata        *sdkMetadata `json:"llm_metadata"`
	TTSMetadata        *sdkMetadata `json:"tts_metadata"`
}

type sdkMetadata struct {
	ModelName *string `json:"model_name"`
}

// sdkModelUsage is one model's usage in LiveKit Agents' session usage, by its type.
type sdkModelUsage struct {
	Type              string   `json:"type"`
	InputTokens       *int64   `json:"input_tokens"`
	InputCachedTokens *int64   `json:"input_cached_tokens"`
	OutputTokens      *int64   `json:"output_tokens"`
	CharactersCount   *int64   `json:"characters_count"`
	AudioDuration     *float64 `json:"audio_duration"`
}

// sdkMessage is a decoded message event.
type sdkMessage struct {
	at      time.Time
	data    event.SDKMessageData
	metrics sdkMessageMetrics
}

// WithSDK adds what a call's SDK batches report to a result built from its spans. Spans stay the
// source of turns and usage: a call with an agent_turn span, or a session report that says it had
// none, keeps them, so a call with both sources is never counted twice. Otherwise each assistant
// message is a turn, with the user message before it, timed by the framework's per-message
// metrics, and the call's usage is the latest cumulative usage the SDK reported. The latest user
// and agent state is always the SDK's. What the SDK did not report stays nil; E2E latency, which
// LiveKit reports only on spans, stays unknown.
func WithSDK(res Result, batches []event.SDKBatch) Result {
	var events []event.SDKEvent
	for _, b := range batches {
		events = append(events, b.Events...)
	}
	sort.SliceStable(events, func(i, k int) bool { return events[i].At.Before(events[k].At) })
	res.Live = live(events)
	if len(res.Turns) > 0 || res.NoReply {
		return res
	}
	res.Turns = sdkTurns(events)
	if u, ok := sdkUsage(events); ok {
		res.Usage = u
	}
	return res
}

func live(events []event.SDKEvent) *Live {
	var l Live
	for _, e := range events {
		if e.Type != event.SDKState {
			continue
		}
		var d event.SDKStateData
		if json.Unmarshal(e.Data, &d) != nil {
			continue
		}
		s := &LiveState{State: d.State, At: e.At.UTC()}
		switch d.Actor {
		case "user":
			l.User = s
		case "agent":
			l.Agent = s
		}
	}
	if l.User == nil && l.Agent == nil {
		return nil
	}
	return &l
}

// sdkTurns builds one turn per assistant message, with the last user message since the previous
// turn as its user side.
func sdkTurns(events []event.SDKEvent) []Turn {
	var turns []Turn
	var user *sdkMessage
	for _, e := range events {
		if e.Type != event.SDKMessage {
			continue
		}
		m := sdkMessage{at: e.At.UTC()}
		if json.Unmarshal(e.Data, &m.data) != nil {
			continue
		}
		var raw struct {
			Metrics json.RawMessage `json:"metrics"`
		}
		if json.Unmarshal(e.Data, &raw) == nil && len(raw.Metrics) > 0 {
			_ = json.Unmarshal(raw.Metrics, &m.metrics)
		}
		switch m.data.Role {
		case "user":
			user = &m
		case "assistant":
			turns = append(turns, sdkTurn(user, m))
			user = nil
		}
	}
	return turns
}

func sdkTurn(user *sdkMessage, agent sdkMessage) Turn {
	t := Turn{
		Start: cmpTime(agent.metrics.StartedSpeakingAt, agent.at), End: cmpTime(agent.metrics.StoppedSpeakingAt, agent.at),
		AgentText: agent.data.Text, Interrupted: agent.data.Interrupted,
		TTFTMs: secondsToMs(agent.metrics.LLMTTFT), TTSMs: secondsToMs(agent.metrics.TTSTTFB),
	}
	if md := agent.metrics.LLMMetadata; md != nil {
		t.LLMModel = md.ModelName
	}
	if md := agent.metrics.TTSMetadata; md != nil {
		t.TTSModel = md.ModelName
	}
	if user != nil {
		t.UserText = user.data.Text
		t.EndOfTurnMs = secondsToMs(user.metrics.EndOfTurnDelay)
		t.TranscriptionDelayMs = secondsToMs(user.metrics.TranscriptionDelay)
		if user.metrics.StartedSpeakingAt != nil {
			t.Start = cmpTime(user.metrics.StartedSpeakingAt, t.Start)
		}
	}
	return t
}

// sdkUsage is the call's usage from its latest usage event: LiveKit reports the session's running
// total per model.
func sdkUsage(events []event.SDKEvent) (CallUsage, bool) {
	var latest *event.SDKEvent
	for i := range events {
		if events[i].Type == event.SDKUsage {
			latest = &events[i]
		}
	}
	if latest == nil {
		return CallUsage{}, false
	}
	var d struct {
		Models []sdkModelUsage `json:"models"`
	}
	if json.Unmarshal(latest.Data, &d) != nil {
		return CallUsage{}, false
	}
	var u CallUsage
	for _, m := range d.Models {
		switch m.Type {
		case "llm_usage":
			u.InputTokens.add(m.InputTokens)
			u.OutputTokens.add(m.OutputTokens)
			u.CachedTokens.add(m.InputCachedTokens)
		case "tts_usage":
			u.TTSCharacters.add(m.CharactersCount)
			u.TTSAudioSeconds.add(m.AudioDuration)
		case "stt_usage":
			u.STTAudioSeconds.add(m.AudioDuration)
		}
	}
	return u, true
}

// cmpTime is the epoch seconds the framework reported, or fallback when it reported none.
func cmpTime(epoch *float64, fallback time.Time) time.Time {
	if epoch == nil || *epoch <= 0 {
		return fallback
	}
	sec, frac := math.Modf(*epoch)
	return time.Unix(int64(sec), int64(frac*1e9)).UTC()
}

// secondsToMs converts seconds to milliseconds; nil stays nil.
func secondsToMs(s *float64) *float64 {
	if s == nil {
		return nil
	}
	ms := *s * 1000
	return &ms
}
