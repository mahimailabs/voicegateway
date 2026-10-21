package turn

import (
	"encoding/json"
	"fmt"
	"slices"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// Findings evaluates reported turn metrics and span outcomes. It does not interpret missing
// metrics as zero or claim that a provider error caused a call to fail. The worker persists the
// result so issue lists and alert delivery can consume the same evidence.
func Findings(spans []store.Span, result Result, thresholds store.Thresholds) []store.Finding {
	out := []store.Finding{}
	add := func(id, severity, title, detail string, turns []int) {
		if turns == nil {
			turns = []int{}
		}
		out = append(out, store.Finding{ID: id, Severity: severity, Title: title, Detail: detail, Turns: turns})
	}
	var slow, cut, late, speech []int
	for i, turn := range result.Turns {
		if turn.E2EMs != nil && *turn.E2EMs > thresholds.AnswerMs {
			slow = append(slow, i)
		}
		if turn.Interrupted != nil && *turn.Interrupted {
			cut = append(cut, i)
		}
		if turn.TranscriptionDelayMs != nil && *turn.TranscriptionDelayMs > 1000 {
			late = append(late, i)
		}
		if turn.TTSMs != nil && *turn.TTSMs > thresholds.TTSStartMs {
			speech = append(speech, i)
		}
	}
	if len(slow) > 0 {
		add("slow", "medium", "Slow answers", fmt.Sprintf("%d turns exceeded the %.0f ms response budget", len(slow), thresholds.AnswerMs), slow)
	}
	if len(cut) > 0 {
		add("cut", "high", "Replies cut off by the caller", fmt.Sprintf("%d replies reported interruption", len(cut)), cut)
	}
	if len(late) > 0 {
		add("late", "medium", "Transcript arrived late", fmt.Sprintf("%d turns reported transcription delay over 1000 ms", len(late)), late)
	}
	if len(speech) > 0 {
		add("tts", "medium", "Slow start of speech", fmt.Sprintf("%d turns exceeded the %.0f ms TTS start budget", len(speech), thresholds.TTSStartMs), speech)
	}
	requests, zeroInput, failed, stalls := 0, 0, 0, 0
	for _, span := range spans {
		switch span.Name {
		case spanLLMRequest:
			requests++
			if span.Attributes[attrInputTokens] == "0" {
				zeroInput++
			}
			if hasErrorReason(span.Attributes["gen_ai.response.finish_reasons"]) {
				failed++
			}
		case "event_loop_blocked":
			stalls++
		}
	}
	if failed > 0 {
		add("discard", "medium", "Model requests reported errors", fmt.Sprintf("%d of %d requests reported finish reason error. This alone does not distinguish provider failure from cancellation.", failed, requests), nil)
	}
	if requests > 0 && zeroInput == requests {
		add("zero", "info", "Model reported zero input tokens", "Every reported model request had zero input tokens. Output usage is independent.", nil)
	}
	if stalls > 0 {
		add("stall", "medium", "Agent process stalled", fmt.Sprintf("%d event_loop_blocked spans were reported", stalls), nil)
	}
	if pipeline(spans) == PipelineRealtime {
		add("rt", "info", "Realtime inference", "Speech and language inference are combined; separate STT, LLM and TTS timings may not exist.", nil)
	}
	return out
}

func hasErrorReason(raw string) bool {
	if raw == "error" {
		return true
	}
	var reasons []string
	return json.Unmarshal([]byte(raw), &reasons) == nil && slices.Contains(reasons, "error")
}

// FindingCoverage identifies rules for which every turn has an explicit observation. Positive
// findings are always retained; absence clears prior evidence only for these covered rules.
// Span absence alone does not prove that provider errors or process stalls recovered.
func FindingCoverage(result Result) []string {
	covered := []string{}
	if len(result.Turns) == 0 {
		return covered
	}
	slow, cut, late, speech := true, true, true, true
	for _, turn := range result.Turns {
		slow = slow && turn.E2EMs != nil
		cut = cut && turn.Interrupted != nil
		late = late && turn.TranscriptionDelayMs != nil
		speech = speech && turn.TTSMs != nil
	}
	for _, rule := range []struct {
		id    string
		known bool
	}{{"slow", slow}, {"cut", cut}, {"late", late}, {"tts", speech}} {
		if rule.known {
			covered = append(covered, rule.id)
		}
	}
	return covered
}
