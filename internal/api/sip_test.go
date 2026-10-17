package api_test

import (
	"context"
	"encoding/json"
	"net/http"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/ClickHouse/clickhouse-go/v2"
	"github.com/livekit/protocol/livekit"
	lksdk "github.com/livekit/server-sdk-go/v2"
	"github.com/twitchtv/twirp"

	"github.com/mahimailabs/voicegateway/internal/scrape"
	"github.com/mahimailabs/voicegateway/internal/store"
)

func (f *fixture) sipScraper(dial scrape.Dial) *scrape.SIPScraper {
	if dial == nil {
		dial = scrape.DialSDK
	}
	return &scrape.SIPScraper{
		Projects: f.projects, Samples: f.layers, Lock: scrape.RedisLock{Client: f.rdb}, Dial: dial,
		Log: quiet(), Now: now, Every: every, Timeout: 5 * time.Second,
	}
}

func (f *fixture) sipClient() *lksdk.SIPClient { return lksdk.NewSIPClient(f.url, devKey, devSecret) }

// sipRead is the contract's Sip, as much of it as these tests read.
type sipRead struct {
	Source         string              `json:"source"`
	LiveKitNumbers []store.SIPNumber   `json:"livekitNumbers"`
	Inbound        []store.SIPInbound  `json:"inbound"`
	Outbound       []store.SIPOutbound `json:"outbound"`
	Rules          []store.SIPRule     `json:"rules"`
	SourceStatus   *struct {
		LastGoodReadAt *time.Time `json:"lastGoodReadAt"`
		ErrorClass     *string    `json:"errorClass"`
	} `json:"sourceStatus"`
}

func TestSipRedacted(t *testing.T) {
	f := newFixture(t)
	ctx := context.Background()
	f.addProject("p1", devSecret)
	secrets := []string{
		"in-user-6f2a", "in-pass-9c1d", "in-header-value-71e0", "Inbound trunk name 4b2c",
		"out-user-58d3", "out-pass-0a7e", "out-header-value-3b8f", "Outbound trunk name 9d41",
		"8135", "secret-prefix-2e6b-", "Rule name 6c0a", "5551234567", "5557654321",
	}
	in, err := f.sipClient().CreateSIPInboundTrunk(ctx, &livekit.CreateSIPInboundTrunkRequest{Trunk: &livekit.SIPInboundTrunkInfo{
		Name: "Inbound trunk name 4b2c", Numbers: []string{"+15551234567"}, AllowedAddresses: []string{"10.0.0.0/8"},
		AuthUsername: "in-user-6f2a", AuthPassword: "in-pass-9c1d", Headers: map[string]string{"X-In": "in-header-value-71e0"},
		KrispEnabled: true,
	}})
	if err != nil {
		t.Fatal(err)
	}
	out, err := f.sipClient().CreateSIPOutboundTrunk(ctx, &livekit.CreateSIPOutboundTrunkRequest{Trunk: &livekit.SIPOutboundTrunkInfo{
		Name: "Outbound trunk name 9d41", Address: "sip.example.test", Transport: livekit.SIPTransport_SIP_TRANSPORT_TCP,
		Numbers: []string{"+15557654321"}, AuthUsername: "out-user-58d3", AuthPassword: "out-pass-0a7e",
		Headers: map[string]string{"X-Account": "out-header-value-3b8f"},
	}})
	if err != nil {
		t.Fatal(err)
	}
	rule, err := f.sipClient().CreateSIPDispatchRule(ctx, &livekit.CreateSIPDispatchRuleRequest{DispatchRule: &livekit.SIPDispatchRuleInfo{
		Name: "Rule name 6c0a", TrunkIds: []string{in.GetSipTrunkId()},
		Rule: &livekit.SIPDispatchRule{Rule: &livekit.SIPDispatchRule_DispatchRuleIndividual{
			DispatchRuleIndividual: &livekit.SIPDispatchRuleIndividual{RoomPrefix: "secret-prefix-2e6b-", Pin: "8135"},
		}},
		RoomConfig: &livekit.RoomConfiguration{Agents: []*livekit.RoomAgentDispatch{{AgentName: agentName}}},
	}})
	if err != nil {
		t.Fatal(err)
	}

	f.sipScraper(nil).Tick(ctx)

	code, body := f.getRead(t, "p1", "sip")
	if code != http.StatusOK {
		t.Fatalf("sip answered %d: %s", code, body)
	}
	var got sipRead
	if err := json.Unmarshal(body, &got); err != nil {
		t.Fatal(err)
	}
	if len(got.Inbound) != 1 || len(got.Outbound) != 1 || len(got.Rules) != 1 {
		t.Fatalf("read %d inbound, %d outbound, %d rules; want one each: %s", len(got.Inbound), len(got.Outbound), len(got.Rules), body)
	}
	gi, gout, gr := got.Inbound[0], got.Outbound[0], got.Rules[0]
	if gi.ID != in.GetSipTrunkId() || !isTrue(gi.Auth) || !isTrue(gi.Krisp) || !slices.Equal(gi.Numbers, []string{"•••• 4567"}) ||
		gi.AllowedAddresses == nil || *gi.AllowedAddresses != 1 {
		t.Errorf("inbound = %+v", gi)
	}
	if gout.ID != out.GetSipTrunkId() || !isTrue(gout.Auth) || !slices.Equal(gout.HeaderNames, []string{"X-Account"}) ||
		gout.Transport != "SIP_TRANSPORT_TCP" || gout.Address != "sip.example.test" || gout.DestinationCountry != nil {
		t.Errorf("outbound = %+v", gout)
	}
	if gr.ID != rule.GetSipDispatchRuleId() || gr.Type != "dispatchRuleIndividual" || !isTrue(gr.PIN) || !isTrue(gr.PrefixSet) ||
		gr.Agents == nil || *gr.Agents != 1 || !slices.Equal(gr.TrunkIDs, []string{in.GetSipTrunkId()}) || gr.Media.Encryption != nil {
		t.Errorf("rule = %+v", gr)
	}
	if len(got.LiveKitNumbers) != 0 || !strings.Contains(got.Source, "do not apply") {
		t.Errorf("a self-hosted server's numbers = %+v, source %q; want none, saying they do not apply", got.LiveKitNumbers, got.Source)
	}

	stored := f.storedSIP(t)
	for _, secret := range secrets {
		if strings.Contains(string(body), secret) {
			t.Errorf("the read serves %q", secret)
		}
		if strings.Contains(stored, secret) {
			t.Errorf("sip_reads stores %q", secret)
		}
	}
}

// storedSIP returns every stored SIP row, as ClickHouse holds it.
func (f *fixture) storedSIP(t *testing.T) string {
	t.Helper()
	opts, err := clickhouse.ParseDSN(f.chURL)
	if err != nil {
		t.Fatal(err)
	}
	db := clickhouse.OpenDB(opts)
	defer func() { _ = db.Close() }()
	rows, err := db.QueryContext(context.Background(), `SELECT config FROM sip_reads`)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = rows.Close() }()
	var all strings.Builder
	for rows.Next() {
		var c string
		if err := rows.Scan(&c); err != nil {
			t.Fatal(err)
		}
		all.WriteString(c)
	}
	if err := rows.Err(); err != nil {
		t.Fatal(err)
	}
	if all.Len() == 0 {
		t.Fatal("no SIP read was stored")
	}
	return all.String()
}

func TestSipPermissionDenied(t *testing.T) {
	f := newFixture(t)
	ctx := context.Background()
	f.addProject("never", devSecret)
	f.addProject("good", devSecret)
	if _, err := f.sipClient().CreateSIPInboundTrunk(ctx, &livekit.CreateSIPInboundTrunkRequest{Trunk: &livekit.SIPInboundTrunkInfo{
		Name: "in", Numbers: []string{"+15551234567"},
	}}); err != nil {
		t.Fatal(err)
	}
	// livekit-server --dev has one key, and it holds every grant; LiveKit Cloud keys can lack the
	// SIP admin grant. The denial is LiveKit's own Twirp error for that case, in front of the real
	// server, for the projects marked denied.
	denied := map[string]bool{"never": true}
	s := f.sipScraper(func(url, key, secret string) scrape.LiveKit {
		return grantCheck{
			LiveKit: scrape.DialSDK(url, key, strings.TrimPrefix(secret, deniedSecret)),
			denied:  strings.HasPrefix(secret, deniedSecret),
		}
	})
	s.Projects = deniedProjects{projects: f.projects, denied: denied}
	s.Tick(ctx)
	reads, err := f.layers.LayerReads(ctx, org, "good", store.LayerSIP, time.Now().Add(-time.Hour))
	if err != nil || len(reads) != 1 || !reads[0].OK {
		t.Fatalf("good reads = %+v, %v", reads, err)
	}
	lastGood := reads[0].ReadAt

	denied["good"] = true
	time.Sleep(every)
	s.Tick(ctx)

	code, body := f.getRead(t, "good", "sip")
	if code != http.StatusOK {
		t.Fatalf("a project with earlier good reads answered %d: %s", code, body)
	}
	var got sipRead
	if err := json.Unmarshal(body, &got); err != nil {
		t.Fatal(err)
	}
	if len(got.Inbound) != 1 {
		t.Errorf("inbound = %+v, want the trunk from the last good read", got.Inbound)
	}
	st := got.SourceStatus
	if st == nil || st.ErrorClass == nil || *st.ErrorClass != store.ErrorPermissionDenied ||
		st.LastGoodReadAt == nil || !st.LastGoodReadAt.Equal(lastGood) {
		t.Errorf("sourceStatus = %+v, want permission_denied with last good read %v", st, lastGood)
	}

	code, body = f.getRead(t, "never", "sip")
	var e struct{ Code, Message string }
	if err := json.Unmarshal(body, &e); err != nil {
		t.Fatal(err)
	}
	if code != http.StatusServiceUnavailable || e.Code != "source_unavailable" || !strings.Contains(e.Message, "SIP admin grant") {
		t.Errorf("never-read project answered %d %s %q, want 503 naming the SIP admin grant", code, e.Code, e.Message)
	}
}

// deniedProjects marks the secret of each project whose key lacks the SIP grant, so the dial can
// tell them apart; the marker is removed before the SDK signs anything.
type deniedProjects struct {
	*projects
	denied map[string]bool
}

func (d deniedProjects) LiveKitSecret(ctx context.Context, organisationID, projectID string) (string, error) {
	secret, err := d.projects.LiveKitSecret(ctx, organisationID, projectID)
	if d.denied[projectID] {
		return deniedSecret + secret, err
	}
	return secret, err
}

const deniedSecret = "denied:"

// grantCheck answers SIP reads the way LiveKit does for a key without the SIP admin grant.
type grantCheck struct {
	scrape.LiveKit
	denied bool
}

var errNoSIPGrant = twirp.NewError(twirp.PermissionDenied, "permissions denied")

func (g grantCheck) ListSIPInboundTrunk(ctx context.Context) ([]*livekit.SIPInboundTrunkInfo, error) {
	if g.denied {
		return nil, errNoSIPGrant
	}
	return g.LiveKit.ListSIPInboundTrunk(ctx)
}

func isTrue(b *bool) bool { return b != nil && *b }
