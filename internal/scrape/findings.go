package scrape

import (
	"context"
	"slices"
	"strings"

	"github.com/livekit/protocol/livekit"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// Findings stores evaluation alongside source health; it never interprets a failed read as an
// empty successful snapshot. The implementation is injected by app, like Samples.
type Findings interface {
	ApplyStandingFindings(context.Context, store.LayerRead, *store.StandingEvaluation) error
}

func sipFindings(cfg *store.SIP) *store.StandingEvaluation {
	if cfg == nil {
		return nil
	}
	out := &store.StandingEvaluation{Subjects: []string{}, Covered: []store.FindingKey{}, Findings: []store.StandingFinding{}}
	check := func(subject, rule string, known, failed bool, severity, title, detail string) {
		id := store.LayerSIP + ":" + rule
		if !known {
			out.Incomplete = true
			return
		}
		out.Covered = append(out.Covered, store.FindingKey{IssueID: id, SourceID: subject})
		if failed {
			out.Findings = append(out.Findings, store.StandingFinding{Finding: store.Finding{ID: id, Severity: severity, Title: title, Detail: detail}, SourceID: subject, Where: subject})
		}
	}
	media := func(subject string, m store.SIPMedia) {
		known := m.Encryption != nil && slices.Contains([]string{livekit.SIPMediaEncryption_SIP_MEDIA_ENCRYPT_DISABLE.String(), livekit.SIPMediaEncryption_SIP_MEDIA_ENCRYPT_ALLOW.String(), livekit.SIPMediaEncryption_SIP_MEDIA_ENCRYPT_REQUIRE.String()}, *m.Encryption)
		disabled := known && *m.Encryption == livekit.SIPMediaEncryption_SIP_MEDIA_ENCRYPT_DISABLE.String()
		check(subject, "plain-phone", known, disabled, "medium", "SIP media encryption disabled", "The trunk configuration disables media encryption. Per-call overrides and negotiated media are separate evidence.")
	}
	for _, trunk := range cfg.Inbound {
		subject := "inbound:" + trunk.ID
		out.Subjects = append(out.Subjects, subject)
		media(subject, trunk.Media)
		known := trunk.Auth != nil && trunk.AllowedAddresses != nil && trunk.AllowedNumbers != nil
		open := known && !*trunk.Auth && *trunk.AllowedAddresses == 0 && *trunk.AllowedNumbers == 0
		check(subject, "unrestricted-inbound", known, open, "high", "Inbound trunk has no source restriction", "No authentication, allowed source addresses or allowed caller numbers are configured.")
		matched := false
		for _, rule := range cfg.Rules {
			if len(rule.TrunkIDs) == 0 || slices.Contains(rule.TrunkIDs, trunk.ID) {
				matched = true
				break
			}
		}
		check(subject, "missing-dispatch", true, !matched, "high", "Inbound trunk has no dispatch rule", "No dispatch rule includes this trunk, and no wildcard dispatch rule exists.")
	}
	for _, trunk := range cfg.Outbound {
		subject := "outbound:" + trunk.ID
		out.Subjects = append(out.Subjects, subject)
		media(subject, trunk.Media)
		// AUTO does not establish the negotiated transport. Only explicit UDP/TCP/TLS are covered.
		known := slices.Contains([]string{livekit.SIPTransport_SIP_TRANSPORT_UDP.String(), livekit.SIPTransport_SIP_TRANSPORT_TCP.String(), livekit.SIPTransport_SIP_TRANSPORT_TLS.String()}, trunk.Transport)
		check(subject, "plain-signaling", known, known && trunk.Transport != livekit.SIPTransport_SIP_TRANSPORT_TLS.String(), "medium", "Outbound SIP signaling is not configured for TLS", "The trunk explicitly selects UDP or TCP instead of TLS.")
	}
	for _, rule := range cfg.Rules {
		subject := "rule:" + rule.ID
		out.Subjects = append(out.Subjects, subject)
		individual := rule.Type == "dispatchRuleIndividual"
		known := rule.Type != "" && (!individual || rule.HidePhoneNumber != nil)
		check(subject, "phone-identity", known, individual && rule.HidePhoneNumber != nil && !*rule.HidePhoneNumber, "medium", "Caller number included in participant identity", "The individual dispatch rule does not hide the caller phone number.")
		check(subject, "wildcard-dispatch", true, len(rule.TrunkIDs) == 0 && len(cfg.Inbound) > 1, "low", "Dispatch rule applies to all inbound trunks", "The rule has no trunk restriction while multiple inbound trunks exist.")
	}
	return out
}

func cloudAgentFindings(cfg *store.CloudAgents) *store.StandingEvaluation {
	if cfg == nil {
		return nil
	}
	out := &store.StandingEvaluation{Subjects: []string{}, Covered: []store.FindingKey{}, Findings: []store.StandingFinding{}}
	if !cfg.Applies {
		return out
	}
	failures := []string{"Error", "CrashLoop", "Disabled", "BuildFailed", "ServerError", "Suspended"}
	// The protocol exposes free-form strings. Unrecognized future statuses are unknown, not healthy.
	nonFailures := []string{"Running", "Ready", "Active", "Sleeping", "SettingUp", "Deploying", "Updating"}
	for _, agent := range cfg.Agents {
		subject := "agent:" + agent.ID
		out.Subjects = append(out.Subjects, subject)
		known := len(agent.Deployments) > 0
		bad := []string{}
		for _, deployment := range agent.Deployments {
			failed := slices.Contains(failures, deployment.Status)
			known = known && (failed || slices.Contains(nonFailures, deployment.Status))
			if failed {
				bad = append(bad, deployment.Region+": "+deployment.Status)
			}
		}
		rule := store.LayerCloudAgents + ":deployment-error"
		if !known {
			out.Incomplete = true
		}
		if known {
			out.Covered = append(out.Covered, store.FindingKey{IssueID: rule, SourceID: subject})
		}
		if len(bad) > 0 {
			slices.Sort(bad)
			out.Findings = append(out.Findings, store.StandingFinding{Finding: store.Finding{ID: rule, Severity: "high", Title: "Cloud agent deployment reports a failure state", Detail: strings.Join(bad, "; ") + ". Deployment state alone does not establish which jobs failed."}, SourceID: subject, Where: agent.ID})
		}
	}
	return out
}

// Poll coverage reports availability of native component state. Terminal findings come from
// stored webhooks; a later empty poll must never delete those event occurrences.
func egressCoverage(ok bool, items []*livekit.EgressInfo) *store.StandingEvaluation {
	if !ok {
		return nil
	}
	out := &store.StandingEvaluation{}
	for _, item := range items {
		if item == nil || item.GetEgressId() == "" {
			out.Incomplete = true
			continue
		}
		if _, known := livekit.EgressStatus_name[int32(item.GetStatus())]; !known {
			out.Incomplete = true
		}
	}
	return out
}

func ingressCoverage(ok bool, items []*livekit.IngressInfo) *store.StandingEvaluation {
	if !ok {
		return nil
	}
	out := &store.StandingEvaluation{}
	for _, item := range items {
		if item == nil || item.GetIngressId() == "" || item.GetState() == nil {
			out.Incomplete = true
			continue
		}
		if _, known := livekit.IngressState_Status_name[int32(item.GetState().GetStatus())]; !known {
			out.Incomplete = true
		}
	}
	return out
}
