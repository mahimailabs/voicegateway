package scrape

import (
	"slices"
	"testing"

	"github.com/livekit/protocol/livekit"

	"github.com/mahimailabs/voicegateway/internal/store"
)

func TestSIPFindingsUseNativeConfigurationWithoutSecrets(t *testing.T) {
	cfg := projectSIP([]*livekit.SIPInboundTrunkInfo{{SipTrunkId: "in", Numbers: []string{"+15551234567"}}}, []*livekit.SIPOutboundTrunkInfo{{SipTrunkId: "out", Transport: livekit.SIPTransport_SIP_TRANSPORT_TCP}}, nil)
	got := sipFindings(cfg)
	has := func(rule, source string) bool {
		return slices.ContainsFunc(got.Findings, func(f store.StandingFinding) bool { return f.ID == "sip:"+rule && f.SourceID == source })
	}
	for _, pair := range [][2]string{{"plain-phone", "inbound:in"}, {"plain-phone", "outbound:out"}, {"plain-signaling", "outbound:out"}, {"unrestricted-inbound", "inbound:in"}, {"missing-dispatch", "inbound:in"}} {
		if !has(pair[0], pair[1]) {
			t.Errorf("missing %v: %+v", pair, got)
		}
	}
	cfg.Rules = []store.SIPRule{{ID: "rule", Type: "dispatchRuleIndividual", HidePhoneNumber: ptr(false), TrunkIDs: []string{"in"}}}
	cfg.Inbound[0].Media.Encryption = ptr(livekit.SIPMediaEncryption_SIP_MEDIA_ENCRYPT_REQUIRE.String())
	got = sipFindings(cfg)
	if has("missing-dispatch", "inbound:in") || has("plain-phone", "inbound:in") {
		t.Fatalf("healthy configuration still reported: %+v", got)
	}
	if !has("phone-identity", "rule:rule") {
		t.Fatal("explicit visible phone identity not reported")
	}
	if !slices.Contains(got.Covered, store.FindingKey{IssueID: "sip:plain-phone", SourceID: "inbound:in"}) {
		t.Fatal("known healthy media did not cover prior finding")
	}
}

func TestSIPFindingsUnknownFieldsCannotClearPriorEvidence(t *testing.T) {
	if sipFindings(nil) != nil {
		t.Fatal("failed source became empty evaluation")
	}
	cfg := &store.SIP{Inbound: []store.SIPInbound{{ID: "in"}}, Outbound: []store.SIPOutbound{{ID: "out", Transport: "SIP_TRANSPORT_AUTO"}}, Rules: []store.SIPRule{{ID: "rule", Type: "dispatchRuleIndividual"}}}
	got := sipFindings(cfg)
	for _, key := range []store.FindingKey{{IssueID: "sip:plain-phone", SourceID: "inbound:in"}, {IssueID: "sip:unrestricted-inbound", SourceID: "inbound:in"}, {IssueID: "sip:plain-signaling", SourceID: "outbound:out"}, {IssueID: "sip:phone-identity", SourceID: "rule:rule"}} {
		if slices.Contains(got.Covered, key) {
			t.Errorf("unknown field marked evaluated: %+v", key)
		}
	}
	// The redacted last four digits cannot establish duplicate phone numbers.
	cfg.Inbound = append(cfg.Inbound, store.SIPInbound{ID: "in2", Numbers: []string{"•••• 4567"}})
	cfg.Inbound[0].Numbers = []string{"•••• 4567"}
	if got := sipFindings(cfg); slices.ContainsFunc(got.Findings, func(f store.StandingFinding) bool { return f.ID == "sip:duplicate-number" }) {
		t.Fatal("masked numbers treated as exact identity")
	}
}

func TestCloudAgentFindingsUnknownDeploymentDoesNotMeanRecovery(t *testing.T) {
	if cloudAgentFindings(nil) != nil {
		t.Fatal("failed read became healthy")
	}
	cfg := &store.CloudAgents{Applies: true, Agents: []store.CloudAgent{{ID: "agent", Deployments: []store.CloudAgentDeployment{{Status: "CrashLoop", Region: "us-east"}}}}}
	got := cloudAgentFindings(cfg)
	if len(got.Findings) != 1 || len(got.Covered) != 1 {
		t.Fatalf("explicit failure: %+v", got)
	}
	cfg.Agents[0].Deployments[0].Status = "FutureState"
	got = cloudAgentFindings(cfg)
	if len(got.Findings) != 0 || len(got.Covered) != 0 || len(got.Subjects) != 1 {
		t.Fatalf("unknown state invented recovery: %+v", got)
	}
	cfg.Agents[0].Deployments[0].Status = "Sleeping"
	got = cloudAgentFindings(cfg)
	if len(got.Findings) != 0 || len(got.Covered) != 1 {
		t.Fatalf("sleeping was treated as failure: %+v", got)
	}
	cfg.Applies = false
	got = cloudAgentFindings(cfg)
	if len(got.Subjects) != 0 || len(got.Findings) != 0 {
		t.Fatalf("non-Cloud source evaluated: %+v", got)
	}
}
