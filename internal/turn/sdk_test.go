package turn

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/mahimailabs/voicegateway/internal/event"
	"github.com/mahimailabs/voicegateway/internal/store"
	"github.com/mahimailabs/voicegateway/sdk/contract"
)

func fullCallBatch(t *testing.T) event.SDKBatch {
	t.Helper()
	b, err := contract.Examples.ReadFile("examples/valid/full-call.json")
	if err != nil {
		t.Fatal(err)
	}
	var batch event.SDKBatch
	if err := json.Unmarshal(b, &batch); err != nil {
		t.Fatal(err)
	}
	return batch
}

func approx(v *float64, want float64) bool { return v != nil && *v > want-0.001 && *v < want+0.001 }

// A call with only SDK data gets one turn per assistant message, timed by the framework's
// per-message metrics, the latest cumulative usage and the latest state per side.
func TestWithSDKBuildsTurnsFromMessages(t *testing.T) {
	res := WithSDK(Result{}, []event.SDKBatch{fullCallBatch(t)})
	if len(res.Turns) != 1 {
		t.Fatalf("turns = %d, want 1", len(res.Turns))
	}
	tn := res.Turns[0]
	if tn.UserText == nil || *tn.UserText != "Hi, what time do you open tomorrow?" || tn.AgentText == nil || *tn.AgentText != "We open at nine tomorrow." {
		t.Errorf("text = %v / %v", tn.UserText, tn.AgentText)
	}
	if !approx(tn.TTFTMs, 430) || !approx(tn.TTSMs, 470) || !approx(tn.EndOfTurnMs, 540) || !approx(tn.TranscriptionDelayMs, 310) {
		t.Errorf("ttft %v tts %v eot %v transcription %v", tn.TTFTMs, tn.TTSMs, tn.EndOfTurnMs, tn.TranscriptionDelayMs)
	}
	if tn.E2EMs != nil {
		t.Error("E2E latency invented from SDK data")
	}
	if tn.LLMModel == nil || *tn.LLMModel != "google/gemma-4-31b-it" || tn.TTSModel == nil || *tn.TTSModel != "fishaudio/s2.1-pro" {
		t.Errorf("models %v %v", tn.LLMModel, tn.TTSModel)
	}
	u := res.Usage
	if u.InputTokens.Sum == nil || *u.InputTokens.Sum != 180 || u.OutputTokens.Sum == nil || *u.OutputTokens.Sum != 12 ||
		u.TTSCharacters.Sum == nil || *u.TTSCharacters.Sum != 42 || !approx(u.STTAudioSeconds.Sum, 2.2) {
		t.Errorf("usage %+v", u)
	}
	if res.Live == nil || res.Live.User == nil || res.Live.User.State != "listening" || res.Live.Agent == nil || res.Live.Agent.State != "listening" {
		t.Errorf("live %+v", res.Live)
	}
}

// With spans, the spans' turns and usage stand and the SDK adds only the live state: one turn,
// not two.
func TestWithSDKKeepsSpanTurns(t *testing.T) {
	spans := []store.Span{span("agent_turn", "a", "", 0, 1000, map[string]string{"lk.e2e_latency": "0.9"})}
	res := WithSDK(Build(spans, nil), []event.SDKBatch{fullCallBatch(t)})
	if len(res.Turns) != 1 || res.Turns[0].SpanID != "a" || !approx(res.Turns[0].E2EMs, 900) {
		t.Fatalf("turns = %+v, want the span's one turn", res.Turns)
	}
	if res.Usage.InputTokens.Sum != nil {
		t.Errorf("SDK usage added to a call with spans: %v", *res.Usage.InputTokens.Sum)
	}
	if res.Live == nil {
		t.Error("live state dropped")
	}
}

// No SDK data changes nothing and reports no live state.
func TestWithSDKWithoutBatches(t *testing.T) {
	if res := WithSDK(Result{}, nil); len(res.Turns) != 0 || res.Live != nil || res.Usage.InputTokens.Sum != nil {
		t.Fatalf("result %+v", res)
	}
}

// A reply the agent started (thinking) but never played leaves no assistant message: the SDK-only
// call still has that turn, interrupted, with the user side and the agent side unknown, as the
// uploader's spans report it.
func TestWithSDKUnplayedReplyIsInterruptedTurn(t *testing.T) {
	b := fullCallBatch(t)
	var events []event.SDKEvent
	for _, e := range b.Events {
		var m event.SDKMessageData
		if e.Type == event.SDKMessage && json.Unmarshal(e.Data, &m) == nil && m.Role == "assistant" {
			continue
		}
		if e.Type == event.SDKState && strings.Contains(string(e.Data), `"speaking"`) && strings.Contains(string(e.Data), `"agent"`) {
			continue
		}
		events = append(events, e)
	}
	b.Events = events
	res := WithSDK(Result{}, []event.SDKBatch{b})
	if len(res.Turns) != 1 {
		t.Fatalf("turns = %d, want 1", len(res.Turns))
	}
	tn := res.Turns[0]
	if tn.Interrupted == nil || !*tn.Interrupted || tn.UserText == nil || tn.AgentText != nil {
		t.Errorf("turn = interrupted %v, user %v, agent %v", tn.Interrupted, tn.UserText, tn.AgentText)
	}
	if tn.TTFTMs != nil || tn.TTSMs != nil || tn.LLMModel != nil || !approx(tn.EndOfTurnMs, 540) {
		t.Errorf("agent timings %v %v %v, end of turn %v; want agent side unknown, user side kept", tn.TTFTMs, tn.TTSMs, tn.LLMModel, tn.EndOfTurnMs)
	}
}
