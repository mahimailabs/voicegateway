package scrape

import (
	"context"
	"fmt"
	"log/slog"
	"slices"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/livekit/protocol/livekit"
	"google.golang.org/protobuf/types/known/durationpb"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// SIPScraper reads the SIP trunks, dispatch rules and LiveKit phone numbers of every project on a
// timer and stores their redacted projection.
type SIPScraper struct {
	Projects Projects
	Samples  SIPSamples
	Lock     Locker
	Dial     Dial
	Log      *slog.Logger
	Now      func() time.Time
	// Every is how often the configuration is read.
	Every time.Duration
	// Timeout bounds each LiveKit call.
	Timeout time.Duration
}

// Run reads every project once per Every until ctx is cancelled. It returns nil on cancellation.
func (s *SIPScraper) Run(ctx context.Context) error {
	t := time.NewTicker(s.Every)
	defer t.Stop()
	for {
		s.Tick(ctx)
		select {
		case <-ctx.Done():
			return nil
		case <-t.C:
		}
	}
}

// Tick reads every project's SIP configuration once.
func (s *SIPScraper) Tick(ctx context.Context) {
	projects, err := s.Projects.Projects(ctx)
	if err != nil {
		if ctx.Err() == nil {
			s.Log.Error("scrape: list projects", "err", err)
		}
		return
	}
	var wg sync.WaitGroup
	for _, p := range projects {
		wg.Go(func() {
			if err := s.SIP(ctx, p); err != nil && ctx.Err() == nil {
				s.Log.Error("scrape: sip", "organisation", p.OrganisationID, "project", p.ID, "err", err)
			}
		})
	}
	wg.Wait()
}

// SIP reads one project's SIP configuration and stores it with the read. It skips the project when
// another worker holds its lock for this interval. A LiveKit failure is stored as a failed read
// with its class; an error is returned only when nothing could be stored.
func (s *SIPScraper) SIP(ctx context.Context, p store.Project) error {
	hold := s.Every * 9 / 10
	got, err := s.Lock.Lock(ctx, lockKey(p, store.LayerSIP), hold)
	if err != nil {
		return fmt.Errorf("lock: %w", err)
	}
	if !got {
		return nil
	}
	secret, err := s.Projects.LiveKitSecret(ctx, p.OrganisationID, p.ID)
	if err != nil {
		return fmt.Errorf("open LiveKit secret: %w", err)
	}
	lk := s.Dial(p.LiveKitURL, p.LiveKitAPIKey, secret)

	rctx, cancel := context.WithTimeout(ctx, hold)
	cfg, err := s.readSIP(rctx, lk)
	cancel()
	read := store.LayerRead{OrganisationID: p.OrganisationID, ProjectID: p.ID, Layer: store.LayerSIP, Every: s.Every, ReadAt: s.Now().UTC()}
	if err != nil {
		if ctx.Err() != nil {
			return nil
		}
		read.ErrorClass = classify(err)
		s.Log.Warn("scrape: sip read failed", "organisation", p.OrganisationID, "project", p.ID, "class", read.ErrorClass)
		cfg = nil
	} else {
		read.OK = true
	}
	sctx, cancel := context.WithTimeout(ctx, s.Timeout)
	defer cancel()
	if err := s.Samples.StoreSIPRead(sctx, read, cfg); err != nil {
		return fmt.Errorf("store sip read: %w", err)
	}
	return nil
}

func (s *SIPScraper) readSIP(ctx context.Context, lk LiveKit) (*store.SIP, error) {
	inbound, err := call(ctx, s.Timeout, lk.ListSIPInboundTrunk)
	if err != nil {
		return nil, fmt.Errorf("list inbound trunks: %w", err)
	}
	outbound, err := call(ctx, s.Timeout, lk.ListSIPOutboundTrunk)
	if err != nil {
		return nil, fmt.Errorf("list outbound trunks: %w", err)
	}
	rules, err := call(ctx, s.Timeout, lk.ListSIPDispatchRule)
	if err != nil {
		return nil, fmt.Errorf("list dispatch rules: %w", err)
	}
	cfg := projectSIP(inbound, outbound, rules)
	numbers, err := call(ctx, s.Timeout, lk.ListPhoneNumbers)
	switch {
	case err == nil:
		cfg.PhoneNumbers = true
		for _, n := range numbers {
			cfg.LiveKitNumbers = append(cfg.LiveKitNumbers, projectNumber(n))
		}
	case classify(err) == store.ErrorNotSupported:
		// A self-hosted server has no phone number service; numbers do not apply there.
	default:
		return nil, fmt.Errorf("list phone numbers: %w", err)
	}
	return cfg, nil
}

// projectSIP keeps only what the console contract shows of the trunks and dispatch rules: presence
// of credentials, never their values.
func projectSIP(inbound []*livekit.SIPInboundTrunkInfo, outbound []*livekit.SIPOutboundTrunkInfo, rules []*livekit.SIPDispatchRuleInfo) *store.SIP {
	cfg := &store.SIP{
		LiveKitNumbers: []store.SIPNumber{},
		Inbound:        make([]store.SIPInbound, 0, len(inbound)),
		Outbound:       make([]store.SIPOutbound, 0, len(outbound)),
		Rules:          make([]store.SIPRule, 0, len(rules)),
	}
	for _, t := range inbound {
		cfg.Inbound = append(cfg.Inbound, store.SIPInbound{
			ID:               t.GetSipTrunkId(),
			Numbers:          maskNumbers(t.GetNumbers()),
			AllowedNumbers:   ptr(int64(len(t.GetAllowedNumbers()))),
			AllowedAddresses: ptr(int64(len(t.GetAllowedAddresses()))),
			Auth:             ptr(t.GetAuthUsername() != "" || t.GetAuthPassword() != ""),
			Krisp:            ptr(t.GetKrispEnabled()),
			Media:            projectMedia(t.GetMedia(), ptr(t.GetMediaEncryption())), //nolint:staticcheck // older servers set only the deprecated field
		})
	}
	for _, t := range outbound {
		names := make([]string, 0, len(t.GetHeaders()))
		for name := range t.GetHeaders() {
			names = append(names, name)
		}
		slices.Sort(names)
		cfg.Outbound = append(cfg.Outbound, store.SIPOutbound{
			ID:                 t.GetSipTrunkId(),
			Address:            sipHost(t.GetAddress()),
			Transport:          t.GetTransport().String(),
			Numbers:            maskNumbers(t.GetNumbers()),
			DestinationCountry: nonEmpty(t.GetDestinationCountry()),
			Auth:               ptr(t.GetAuthUsername() != "" || t.GetAuthPassword() != ""),
			HeaderNames:        names,
			Media:              projectMedia(t.GetMedia(), ptr(t.GetMediaEncryption())), //nolint:staticcheck // as for inbound
		})
	}
	for _, r := range rules {
		rule := store.SIPRule{
			ID:              r.GetSipDispatchRuleId(),
			TrunkIDs:        r.GetTrunkIds(),
			HidePhoneNumber: ptr(r.GetHidePhoneNumber()),
			InboundNumbers:  ptr(int64(len(r.GetInboundNumbers()))),
			Agents:          ptr(int64(len(r.GetRoomConfig().GetAgents()))),
			Media:           projectMedia(r.GetMedia(), ruleEncryption(r)),
		}
		if rule.TrunkIDs == nil {
			rule.TrunkIDs = []string{}
		}
		switch d := r.GetRule().GetRule().(type) {
		case *livekit.SIPDispatchRule_DispatchRuleIndividual:
			rule.Type = "dispatchRuleIndividual"
			rule.PrefixSet = ptr(d.DispatchRuleIndividual.GetRoomPrefix() != "")
			rule.PIN = ptr(d.DispatchRuleIndividual.GetPin() != "")
		case *livekit.SIPDispatchRule_DispatchRuleDirect:
			// A direct rule names a room, not a prefix.
			rule.Type = "dispatchRuleDirect"
			rule.PIN = ptr(d.DispatchRuleDirect.GetPin() != "")
		case *livekit.SIPDispatchRule_DispatchRuleCallee:
			rule.Type = "dispatchRuleCallee"
			rule.PrefixSet = ptr(d.DispatchRuleCallee.GetRoomPrefix() != "")
			rule.PIN = ptr(d.DispatchRuleCallee.GetPin() != "")
		}
		cfg.Rules = append(cfg.Rules, rule)
	}
	return cfg
}

// ruleEncryption is the encryption a rule sets through the deprecated field. A rule's encryption
// applies only when the rule sets one, and that field cannot tell "not set" from "disabled", so
// disabled there stays unknown.
func ruleEncryption(r *livekit.SIPDispatchRuleInfo) *livekit.SIPMediaEncryption {
	e := r.GetMediaEncryption() //nolint:staticcheck // older servers set only the deprecated field
	if e == livekit.SIPMediaEncryption_SIP_MEDIA_ENCRYPT_DISABLE {
		return nil
	}
	return &e
}

// sipHost drops any user part of a SIP address, such as "account@sip.example.com".
func sipHost(address string) string {
	if i := strings.LastIndex(address, "@"); i >= 0 {
		return address[i+1:]
	}
	return address
}

// projectMedia reads a media config; fallback is the encryption to show when the config sets none.
func projectMedia(m *livekit.SIPMediaConfig, fallback *livekit.SIPMediaEncryption) store.SIPMedia {
	out := store.SIPMedia{Codecs: []string{}}
	if m != nil {
		out.OnlyListedCodecs = ptr(m.GetOnlyListedCodecs())
	}
	switch {
	case m != nil && m.Encryption != nil:
		out.Encryption = ptr(m.GetEncryption().String())
	case fallback != nil:
		out.Encryption = ptr(fallback.String())
	}
	for _, c := range m.GetCodecs() {
		out.Codecs = append(out.Codecs, c.GetName())
	}
	if d := m.GetMediaTimeout(); d != nil {
		out.MediaTimeout = ptr(protoDuration(d))
	}
	return out
}

// protoDuration writes a duration the way protobuf's JSON mapping does, such as "30s" or "1.5s".
func protoDuration(d *durationpb.Duration) string {
	return strconv.FormatFloat(d.AsDuration().Seconds(), 'f', -1, 64) + "s"
}

func projectNumber(n *livekit.PhoneNumber) store.SIPNumber {
	out := store.SIPNumber{Number: maskNumber(n.GetE164Format()), RuleIDs: n.GetSipDispatchRuleIds()}
	if n.Status != livekit.PhoneNumberStatus_PHONE_NUMBER_STATUS_UNSPECIFIED {
		out.Status = ptr(n.GetStatus().String())
	}
	if len(out.RuleIDs) == 0 && n.GetSipDispatchRuleId() != "" { //nolint:staticcheck // fallback for servers that set only the deprecated field
		out.RuleIDs = []string{n.GetSipDispatchRuleId()} //nolint:staticcheck // as above
	}
	if out.RuleIDs == nil {
		out.RuleIDs = []string{}
	}
	return out
}

func maskNumbers(numbers []string) []string {
	out := make([]string, 0, len(numbers))
	for _, n := range numbers {
		out = append(out, maskNumber(n))
	}
	return out
}
