package alert

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	ogclient "github.com/opsgenie/opsgenie-go-sdk-v2/client"

	"github.com/mahimailabs/voicegateway/internal/store"
)

var at = time.Date(2026, 10, 28, 12, 0, 0, 0, time.UTC)

// firing is a firing message whose subject, an agent name the customer chose, tries to mention
// everyone.
func firing(change string) Message {
	link, call := "https://vg.example.com/org/o/project/p/alerts?tab=history", "https://vg.example.com/org/o/project/p/calls/c1"
	return Message{
		Type: "alert." + change, At: at, OrganisationID: "o", ProjectID: "p", URL: &link,
		Incident: &Incident{
			ID: "8f2b6a1e-0000-4000-8000-000000000001", RuleID: "slow-answer", Title: "Slow answers", Severity: "medium",
			Subject: "Agent <!channel> @everyone", State: "firing", Occurrences: 3, OpenedAt: at,
			Last: Occurrence{Where: "room-1", Detail: "2 turns exceeded the 1500 ms response budget", CallURL: &call},
		},
	}
}

// A chat message carries what came from a call as plain text: Slack's fallback text escapes
// markup, its blocks are plain_text, Discord parses no mentions, and Teams gets an Adaptive Card.
func TestChatMessagesCarryNoMarkup(t *testing.T) {
	for _, kind := range []string{"slack", "discord", "teams"} {
		body, err := chatMessage(kind, firing("firing"))
		if err != nil {
			t.Fatalf("%s: %v", kind, err)
		}
		var v map[string]any
		if err := json.Unmarshal(body, &v); err != nil {
			t.Fatalf("%s: not JSON: %v", kind, err)
		}
		switch kind {
		case "slack":
			if text := v["text"].(string); strings.Contains(text, "<!channel>") || !strings.Contains(text, "&lt;!channel&gt;") {
				t.Errorf("slack text %q is not escaped", text)
			}
			if strings.Contains(string(body), `"mrkdwn"`) {
				t.Errorf("slack blocks use mrkdwn: %s", body)
			}
			if !strings.Contains(string(body), "calls/c1") {
				t.Errorf("slack message has no call link: %s", body)
			}
		case "discord":
			mentions, ok := v["allowed_mentions"].(map[string]any)
			if !ok || len(mentions["parse"].([]any)) != 0 {
				t.Errorf("discord allowed_mentions = %v, want an empty parse list", v["allowed_mentions"])
			}
			if embeds := v["embeds"].([]any); len(embeds) != 1 || !strings.HasPrefix(embeds[0].(map[string]any)["title"].(string), "Firing: Slow answers") {
				t.Errorf("discord embeds = %v", embeds)
			}
		case "teams":
			attachments := v["attachments"].([]any)
			if len(attachments) != 1 || attachments[0].(map[string]any)["contentType"] != "application/vnd.microsoft.card.adaptive" {
				t.Errorf("teams attachments = %v, want one Adaptive Card", attachments)
			}
		}
	}
	// An occurrence with no place and no detail still gives Slack a section text: an empty one is
	// refused with invalid_blocks, which would mark the channel broken.
	bare := firing("firing")
	bare.Incident.Last = Occurrence{}
	body, err := chatMessage("slack", bare)
	if err != nil {
		t.Fatal(err)
	}
	var msg struct {
		Blocks []struct {
			Type string `json:"type"`
			Text *struct {
				Text string `json:"text"`
			} `json:"text"`
		} `json:"blocks"`
	}
	if err := json.Unmarshal(body, &msg); err != nil {
		t.Fatal(err)
	}
	for _, b := range msg.Blocks {
		if b.Type == "section" && (b.Text == nil || b.Text.Text != "Slow answers") {
			t.Errorf("slack section without an occurrence = %+v, want the alert's title", b.Text)
		}
	}
	// A test message names the channel it proves.
	body, err = chatMessage("slack", Message{Type: "alert.test", At: at})
	if err != nil || !strings.Contains(string(body), "The Slack channel of this project works.") {
		t.Fatalf("slack test message = %s, %v", body, err)
	}
}

type recorded struct {
	path string
	body map[string]any
	auth string
}

// recorder answers each request with the next status and body, and keeps what it was sent.
func recorder(t *testing.T, answers ...string) (*httptest.Server, func() []recorded) {
	t.Helper()
	var mu sync.Mutex
	var got []recorded
	h := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		mu.Lock()
		defer mu.Unlock()
		raw, _ := io.ReadAll(r.Body)
		var body map[string]any
		_ = json.Unmarshal(raw, &body)
		got = append(got, recorded{path: r.URL.RequestURI(), body: body, auth: r.Header.Get("Authorization")})
		status, answer, _ := strings.Cut(answers[min(len(got), len(answers))-1], " ")
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("X-Request-Id", "req-1")
		code := map[string]int{"202": 202, "400": 400, "422": 422, "429": 429}[status]
		w.WriteHeader(code)
		_, _ = io.WriteString(w, answer)
	})
	srv := httptest.NewServer(h)
	t.Cleanup(srv.Close)
	return srv, func() []recorded {
		mu.Lock()
		defer mu.Unlock()
		return append([]recorded(nil), got...)
	}
}

// PagerDuty: firing and reminders trigger under one dedup key, resolved resolves it, and a test is
// a change event, which pages nobody. A refusal is final, without the routing key in its message;
// 429 is retried.
func TestPagerDutyEvents(t *testing.T) {
	const key = "R0UT1NGK3Y0123456789abcdefABCDEF"
	srv, got := recorder(t,
		`202 {"status":"success","dedup_key":"x"}`, `202 {"status":"success"}`, `202 {"status":"success"}`,
		`400 {"status":"invalid event","message":"Event object is invalid","errors":["routing key R0UT1NGK3Y0123456789abcdefABCDEF not found"]}`,
		`429 {"status":"throttle"}`)
	prior := pagerDutyEndpoints["eu"]
	pagerDutyEndpoints["eu"] = srv.URL
	t.Cleanup(func() { pagerDutyEndpoints["eu"] = prior })
	d := &Deliverer{HTTP: srv.Client(), Now: func() time.Time { return at }}
	c := store.AlertKeyConfig{Key: key, Region: "eu"}
	ctx := context.Background()
	for _, m := range []Message{firing("firing"), firing("resolved"), {Type: "alert.test", At: at}} {
		if o := d.pagerDuty(ctx, c, m); !o.ok {
			t.Fatalf("%s: %+v, want delivered", m.Type, o)
		}
	}
	r := got()
	if r[0].path != "/v2/enqueue" || r[0].body["event_action"] != "trigger" || r[0].body["dedup_key"] != "voicegateway-8f2b6a1e-0000-4000-8000-000000000001" || r[0].body["routing_key"] != key {
		t.Errorf("firing sent %+v", r[0])
	}
	if p := r[0].body["payload"].(map[string]any); p["severity"] != "warning" || p["source"] != "Agent <!channel> @everyone" {
		t.Errorf("firing payload = %v", p)
	}
	if r[1].body["event_action"] != "resolve" || r[1].body["dedup_key"] != r[0].body["dedup_key"] {
		t.Errorf("resolved sent %+v", r[1])
	}
	if r[2].path != "/v2/change/enqueue" {
		t.Errorf("test went to %s, want a change event", r[2].path)
	}
	if r[0].auth != "" {
		t.Errorf("an event carried Authorization %q; the routing key is the credential", r[0].auth)
	}
	o := d.pagerDuty(ctx, c, firing("firing"))
	// The client reads only REST API error objects, so an Events API refusal comes back as its status.
	if o.ok || o.retry || o.status != "HTTP 400" || strings.Contains(deref(o.err), key) {
		t.Errorf("400: %+v (%v), want final, without the key", o, deref(o.err))
	}
	if o := d.pagerDuty(ctx, c, firing("reminder")); !o.retry || o.status != "HTTP 429" {
		t.Errorf("429: %+v, want retried", o)
	}
}

// Opsgenie: firing creates the alert under the incident's alias, resolved closes it by alias, and
// the API key goes only in the GenieKey header. A refusal is final.
func TestOpsgenieAlerts(t *testing.T) {
	const key = "00000000-1111-2222-3333-444444444444"
	ok := `202 {"result":"Request will be processed","took":0.1,"requestId":"req-1"}`
	// The SDK speaks plain http to a host without "api" in its name (client.buildRequestUrl, for its
	// own tests), so the test server needs no TLS.
	srv, got := recorder(t, ok, ok, ok, `422 {"message":"Key format is not valid","took":0.1,"requestId":"req-2"}`)
	prior := opsgenieHosts["us"]
	opsgenieHosts["us"] = ogclient.ApiUrl(strings.TrimPrefix(srv.URL, "http://"))
	t.Cleanup(func() { opsgenieHosts["us"] = prior })
	d := &Deliverer{HTTP: srv.Client(), Now: func() time.Time { return at }}
	c := store.AlertKeyConfig{Key: key, Region: "us"}
	ctx := context.Background()
	for _, m := range []Message{firing("firing"), firing("resolved"), {Type: "alert.test", At: at, ProjectID: "p"}} {
		if o := d.opsgenie(ctx, c, m); !o.ok {
			t.Fatalf("%s: %+v (%v), want delivered", m.Type, o, deref(o.err))
		}
	}
	r := got()
	if r[0].path != "/v2/alerts" || r[0].body["alias"] != "voicegateway-8f2b6a1e-0000-4000-8000-000000000001" || r[0].body["priority"] != "P3" || r[0].auth != "GenieKey "+key {
		t.Errorf("firing sent %+v", r[0])
	}
	if r[1].path != "/v2/alerts/voicegateway-8f2b6a1e-0000-4000-8000-000000000001/close?identifierType=alias" {
		t.Errorf("resolved went to %s", r[1].path)
	}
	if r[2].body["priority"] != "P5" || r[2].body["alias"] != "voicegateway-test-p" {
		t.Errorf("test sent %+v, want a P5 alert under the project's test alias", r[2])
	}
	if o := d.opsgenie(ctx, c, firing("firing")); o.ok || o.retry || o.status != "HTTP 422" {
		t.Errorf("422: %+v, want final", o)
	}
}

// On Cloud no channel posts over plain http, even one saved before the API refused it.
func TestHTTPSOnlyRefusesPlainHTTP(t *testing.T) {
	calls := 0
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { calls++ }))
	defer srv.Close()
	d := &Deliverer{HTTP: srv.Client(), HTTPSOnly: true, Now: func() time.Time { return at }}
	o := d.post(context.Background(), srv.URL+"/hook", []byte("{}"), nil, nil)
	if o.ok || o.retry || o.status != "address_not_allowed" || calls != 0 {
		t.Fatalf("plain http on Cloud: %+v after %d requests, want refused before sending", o, calls)
	}
}

func deref(s *string) string {
	if s == nil {
		return ""
	}
	return *s
}
