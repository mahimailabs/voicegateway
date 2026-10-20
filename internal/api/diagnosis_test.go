package api

import (
	"testing"

	"github.com/mahimailabs/voicegateway/internal/turn"
)

func TestDiagnosisUsagePreservesUnknownAndCachedSubset(t *testing.T) {
	zero := int64(0)
	seconds := 1.25
	u := turn.CallUsage{CachedTokens: turn.Total[int64]{Sum: &zero}, TTSAudioSeconds: turn.Total[float64]{Sum: &seconds}}
	got := toUsage(u)
	if got == nil || got.LLMCachedTokens == nil || *got.LLMCachedTokens != 0 || !near(got.TTSSeconds, 1.25, 0.001) || got.LLMInputTokens != nil {
		t.Fatalf("usage: %+v", got)
	}
	u.CachedTokens.Missing = 1
	u.TTSAudioSeconds.Missing = 1
	got = toUsage(u)
	if got.LLMCachedTokens != nil || got.TTSSeconds != nil {
		t.Fatalf("partial totals must be unknown: %+v", got)
	}
	if toUsage(turn.CallUsage{}) != nil {
		t.Fatal("missing usage must remain absent")
	}
}

func TestDiagnosisTurnKeepsSourceSpanIdentity(t *testing.T) {
	got := toTurns([]turn.Turn{{SpanID: "reported-turn"}})
	if len(got) != 1 || got[0].SpanID == nil || *got[0].SpanID != "reported-turn" {
		t.Fatalf("turn identity: %+v", got)
	}
}

func TestDiagnosisMissingSpanIdentityIsNull(t *testing.T) {
	if got := toTurns([]turn.Turn{{}}); got[0].SpanID != nil {
		t.Fatal("missing span identity must be null")
	}
}
