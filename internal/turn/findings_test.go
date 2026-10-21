package turn

import (
	"testing"

	"github.com/mahimailabs/voicegateway/internal/store"
)

func TestFindingsMissingMetricsAreNotHealthyOrZero(t *testing.T) {
	thresholds := store.Thresholds{AnswerMs: 1500, JoinMs: 2000, TTSStartMs: 1000}
	if got := Findings(nil, Result{Turns: []Turn{{}}}, thresholds); len(got) != 0 {
		t.Fatalf("missing observations created findings: %+v", got)
	}
	spans := []store.Span{{Name: spanLLMRequest, Attributes: map[string]string{attrInputTokens: "0"}}, {Name: spanLLMRequest}}
	if got := Findings(spans, Result{}, thresholds); len(got) != 0 {
		t.Fatalf("unreported input was treated as zero: %+v", got)
	}
}

func TestFindingsUseSavedThresholdsAndReportedInterruption(t *testing.T) {
	answer, speech, late, interrupted := 1500.5, 1000.5, 1000.0, true
	res := Result{Turns: []Turn{{E2EMs: &answer, TTSMs: &speech, TranscriptionDelayMs: &late, Interrupted: &interrupted}}}
	thresholds := store.Thresholds{AnswerMs: 1500, JoinMs: 2000, TTSStartMs: 1000}
	got := Findings(nil, res, thresholds)
	if len(got) != 3 || got[0].ID != "slow" || got[1].ID != "cut" || got[2].ID != "tts" {
		t.Fatalf("findings: %+v", got)
	}
	thresholds.AnswerMs, thresholds.TTSStartMs = answer, speech
	got = Findings(nil, res, thresholds)
	if len(got) != 1 || got[0].ID != "cut" || len(got[0].Turns) != 1 || got[0].Turns[0] != 0 {
		t.Fatalf("equality must not exceed threshold: %+v", got)
	}
}

func TestFindingsRequireExactErrorReason(t *testing.T) {
	for _, tc := range []struct {
		raw  string
		want bool
	}{
		{`["error"]`, true},
		{"error", true},
		{`["stop", "error"]`, true},
		{`["no_error"]`, false},
		{`["stop"]`, false},
		{"", false},
		{`{"error":true}`, false},
	} {
		if got := hasErrorReason(tc.raw); got != tc.want {
			t.Errorf("%q: got %v, want %v", tc.raw, got, tc.want)
		}
	}
}

func TestFindingCoverageRequiresEveryTurn(t *testing.T) {
	value := 100.0
	if got := FindingCoverage(Result{Turns: []Turn{{E2EMs: &value}, {}}}); len(got) != 0 {
		t.Fatalf("partial coverage: %v", got)
	}
	got := FindingCoverage(Result{Turns: []Turn{{E2EMs: &value}, {E2EMs: &value}}})
	if len(got) != 1 || got[0] != "slow" {
		t.Fatalf("covered rules: %v", got)
	}
	if got := FindingCoverage(Result{}); got == nil {
		t.Fatal("empty must not signal full coverage")
	}
}
