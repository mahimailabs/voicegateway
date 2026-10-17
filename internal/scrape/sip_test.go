package scrape

import (
	"testing"
	"time"

	"github.com/livekit/protocol/livekit"
	"google.golang.org/protobuf/types/known/durationpb"
)

func TestSIPUnsetStaysNull(t *testing.T) {
	require := livekit.SIPMediaEncryption_SIP_MEDIA_ENCRYPT_REQUIRE
	tests := []struct {
		name           string
		rule           *livekit.SIPDispatchRuleInfo
		wantPrefixSet  *bool
		wantEncryption *string
		wantTimeout    *string
	}{
		{
			name: "direct rule has no prefix to report",
			rule: &livekit.SIPDispatchRuleInfo{Rule: &livekit.SIPDispatchRule{Rule: &livekit.SIPDispatchRule_DispatchRuleDirect{
				DispatchRuleDirect: &livekit.SIPDispatchRuleDirect{RoomName: "lobby"},
			}}},
		},
		{
			name: "rule without its own media settings",
			rule: &livekit.SIPDispatchRuleInfo{Rule: &livekit.SIPDispatchRule{Rule: &livekit.SIPDispatchRule_DispatchRuleIndividual{
				DispatchRuleIndividual: &livekit.SIPDispatchRuleIndividual{},
			}}},
			wantPrefixSet: ptr(false),
		},
		{
			name: "rule set through the deprecated encryption field",
			rule: &livekit.SIPDispatchRuleInfo{
				Rule: &livekit.SIPDispatchRule{Rule: &livekit.SIPDispatchRule_DispatchRuleIndividual{
					DispatchRuleIndividual: &livekit.SIPDispatchRuleIndividual{},
				}},
				MediaEncryption: livekit.SIPMediaEncryption_SIP_MEDIA_ENCRYPT_ALLOW, //nolint:staticcheck // the field older servers set
			},
			wantPrefixSet:  ptr(false),
			wantEncryption: ptr("SIP_MEDIA_ENCRYPT_ALLOW"),
		},
		{
			name: "rule with its own media settings",
			rule: &livekit.SIPDispatchRuleInfo{
				Rule: &livekit.SIPDispatchRule{Rule: &livekit.SIPDispatchRule_DispatchRuleCallee{
					DispatchRuleCallee: &livekit.SIPDispatchRuleCallee{RoomPrefix: "call-"},
				}},
				Media: &livekit.SIPMediaConfig{Encryption: &require, MediaTimeout: durationpb.New(1500 * time.Millisecond)},
			},
			wantPrefixSet:  ptr(true),
			wantEncryption: ptr("SIP_MEDIA_ENCRYPT_REQUIRE"),
			wantTimeout:    ptr("1.5s"),
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := projectSIP(nil, nil, []*livekit.SIPDispatchRuleInfo{tt.rule}).Rules[0]
			if !equal(got.PrefixSet, tt.wantPrefixSet) {
				t.Errorf("prefixSet = %v, want %v", deref(got.PrefixSet), deref(tt.wantPrefixSet))
			}
			if !equal(got.Media.Encryption, tt.wantEncryption) {
				t.Errorf("encryption = %v, want %v", deref(got.Media.Encryption), deref(tt.wantEncryption))
			}
			if tt.rule.Media == nil && got.Media.OnlyListedCodecs != nil {
				t.Errorf("onlyListedCodecs = %v with no media config, want null", *got.Media.OnlyListedCodecs)
			}
			if !equal(got.Media.MediaTimeout, tt.wantTimeout) {
				t.Errorf("mediaTimeout = %v, want %v", deref(got.Media.MediaTimeout), deref(tt.wantTimeout))
			}
		})
	}
}

func TestSIPAddressDropsUser(t *testing.T) {
	out := projectSIP(nil, []*livekit.SIPOutboundTrunkInfo{{Address: "acct-77@sip.example.test"}}, nil).Outbound[0]
	if out.Address != "sip.example.test" {
		t.Errorf("address = %q, want the host only", out.Address)
	}
}

func equal[T comparable](a, b *T) bool {
	if a == nil || b == nil {
		return a == b
	}
	return *a == *b
}

func deref[T any](p *T) any {
	if p == nil {
		return nil
	}
	return *p
}
