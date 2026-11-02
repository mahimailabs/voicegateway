package api

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// do sends a request with a JSON body to a, checks the answer against the contract and decodes it.
func (f *fixture) do(a *API, method, path, body string, out any) (int, []byte) {
	f.t.Helper()
	req := httptest.NewRequest(method, path, strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	rec := httptest.NewRecorder()
	a.Routes().ServeHTTP(rec, req)
	res := rec.Result()
	f.validateResponse(f.t, httptest.NewRequest(method, path, strings.NewReader(body)), res.StatusCode, res.Header, rec.Body.Bytes())
	if out != nil && len(rec.Body.Bytes()) > 0 {
		if err := json.Unmarshal(rec.Body.Bytes(), out); err != nil {
			f.t.Fatalf("decode %s: %v", rec.Body.Bytes(), err)
		}
	}
	return res.StatusCode, rec.Body.Bytes()
}

// Alert setup through the API: a webhook's URL and signing secret never come back after the answer
// that made the secret, only its host does; a rule accepts only known kinds; email needs SMTP on the
// server.
func TestAlertSetupKeepsChannelSecrets(t *testing.T) {
	f := newFixture(t)
	a := f.api(nil)
	a.Alerts = f.pg
	a.AlertTest = func(string, string) (json.RawMessage, error) { return json.RawMessage(`{"type":"alert.test"}`), nil }
	base := "/api/v1/org/default/project/" + f.project

	var setup alertSetup
	if code, _ := f.do(a, http.MethodGet, base+"/alert-setup", "", &setup); code != http.StatusOK {
		t.Fatalf("getAlertSetup = %d", code)
	}
	if len(setup.Items) != 7 || !setup.Items[0].On || len(setup.Items[0].To) != 0 || len(setup.Channels) != 0 || setup.EmailAvailable {
		t.Fatalf("default setup = %+v, want seven rules on, sending nowhere, no channel, no email", setup)
	}

	const hook = `{"url":"https://hooks.example.com/in/T0K3N?sig=abc"}`
	var saved savedAlertChannel
	if code, _ := f.do(a, http.MethodPut, base+"/alert-channels/webhook", hook, &saved); code != http.StatusOK {
		t.Fatalf("updateAlertChannel = %d", code)
	}
	if saved.SigningSecret == nil || !strings.HasPrefix(*saved.SigningSecret, "whsec_") || saved.Target != "hooks.example.com" {
		t.Fatalf("saved = %+v, want a whsec_ secret and the host", saved)
	}
	first := *saved.SigningSecret
	if _, _ = f.do(a, http.MethodPut, base+"/alert-channels/webhook", hook, &saved); saved.SigningSecret != nil {
		t.Fatal("saving again returned the secret; it is kept and shown once")
	}
	if _, _ = f.do(a, http.MethodPut, base+"/alert-channels/webhook", `{"url":"https://hooks.example.com/in/T0K3N","rotateSecret":true}`, &saved); saved.SigningSecret == nil || *saved.SigningSecret == first {
		t.Fatal("rotateSecret did not make a new secret")
	}
	code, body := f.do(a, http.MethodGet, base+"/alert-setup", "", &setup)
	if code != http.StatusOK || bytes.Contains(body, []byte("T0K3N")) || bytes.Contains(body, []byte("whsec_")) {
		t.Fatalf("getAlertSetup = %d %s: it must show neither the URL's path nor the secret", code, body)
	}
	if len(setup.Channels) != 1 || setup.Channels[0].Target != "hooks.example.com" || setup.Channels[0].LastDelivery != nil {
		t.Fatalf("channels = %+v", setup.Channels)
	}

	for _, bad := range []string{`{"url":"ftp://hooks.example.com"}`, `{"url":"https://"}`, `{"recipients":["a@example.com"]}`, `{"url":"https://x.example.com","extra":1}`} {
		if code, _ := f.do(a, http.MethodPut, base+"/alert-channels/webhook", bad, nil); code != http.StatusBadRequest {
			t.Errorf("webhook %s = %d, want 400", bad, code)
		}
	}
	if code, _ := f.do(a, http.MethodPut, base+"/alert-channels/email", `{"recipients":["oncall@example.com"]}`, nil); code != http.StatusBadRequest {
		t.Errorf("email without SMTP = %d, want 400", code)
	}
	a.AlertEmail = true
	if code, _ := f.do(a, http.MethodPut, base+"/alert-channels/email", `{"recipients":["oncall@example.com","Someone <x@example.com>"]}`, nil); code != http.StatusBadRequest {
		t.Errorf("email with a display name = %d, want 400", code)
	}
	if code, _ := f.do(a, http.MethodPut, base+"/alert-channels/email", `{"recipients":["oncall@example.com","ONCALL@example.com"]}`, &saved); code != http.StatusOK || saved.Recipients == nil || len(*saved.Recipients) != 1 || saved.Target != "1 recipient" {
		t.Errorf("email = %d %+v, want one recipient", code, saved)
	}

	var rule struct {
		On bool     `json:"on"`
		To []string `json:"to"`
	}
	if code, _ := f.do(a, http.MethodPut, base+"/alert-rules/slow-answer", `{"on":true,"to":["webhook","email","webhook"]}`, &rule); code != http.StatusOK || strings.Join(rule.To, ",") != "email,webhook" {
		t.Fatalf("updateAlertRule = %d %+v", code, rule)
	}
	if code, _ := f.do(a, http.MethodPut, base+"/alert-rules/slow-answer", `{"on":true,"to":["sms"]}`, nil); code != http.StatusBadRequest {
		t.Errorf("a rule to an unknown kind = %d, want 400", code)
	}
	if code, _ := f.do(a, http.MethodPut, base+"/alert-rules/no-such-rule", `{"on":true,"to":[]}`, nil); code != http.StatusNotFound {
		t.Errorf("unknown rule = %d, want 404", code)
	}

	if code, _ := f.do(a, http.MethodPost, base+"/alert-channels/webhook/test", "", nil); code != http.StatusNoContent {
		t.Fatalf("testAlertChannel = %d", code)
	}
	f.do(a, http.MethodGet, base+"/alert-setup", "", &setup)
	for _, c := range setup.Channels {
		if c.Kind == "webhook" && (c.LastDelivery == nil || c.LastDelivery.Change != "test" || c.LastDelivery.State != "pending") {
			t.Fatalf("webhook lastDelivery = %+v, want the pending test", c.LastDelivery)
		}
	}
	if code, _ := f.do(a, http.MethodDelete, base+"/alert-channels/email", "", nil); code != http.StatusNoContent {
		t.Fatalf("deleteAlertChannel = %d", code)
	}
	if code, _ := f.do(a, http.MethodPost, base+"/alert-channels/email/test", "", nil); code != http.StatusNotFound {
		t.Errorf("test of a removed channel = %d, want 404", code)
	}
}

// The chat channels take only their provider's https webhook URL and the paging channels a key and
// a region; none of them comes back, only the host each posts to. On Cloud a webhook must be https.
// Email recipients are shown to Admins only.
func TestAlertChannelKinds(t *testing.T) {
	f := newFixture(t)
	a := f.api(nil)
	a.Alerts, a.AlertEmail = f.pg, true
	base := "/api/v1/org/default/project/" + f.project
	put := func(kind, body string) (int, savedAlertChannel) {
		t.Helper()
		var saved savedAlertChannel
		code, _ := f.do(a, http.MethodPut, base+"/alert-channels/"+kind, body, &saved)
		return code, saved
	}
	tests := []struct {
		kind, body, target string
	}{
		{"slack", `{"url":"https://hooks.slack.com/services/T000/B000/S3CR3T"}`, "hooks.slack.com"},
		{"teams", `{"url":"https://prod-01.westus.logic.azure.com:443/workflows/abc/triggers/manual/paths/invoke?sig=S3CR3T"}`, "prod-01.westus.logic.azure.com"},
		{"teams", `{"url":"https://default0a.environment.api.powerplatform.com/powerautomate/automations/direct/workflows/abc/triggers/manual/paths/invoke?sig=S3CR3T"}`, "default0a.environment.api.powerplatform.com"},
		{"discord", `{"url":"https://discord.com/api/webhooks/123456/S3CR3T-token_x"}`, "discord.com"},
		{"pagerduty", `{"key":"S3CR3T0123456789abcdef0123456789"}`, "events.pagerduty.com"},
		{"pagerduty", `{"key":"S3CR3T0123456789abcdef0123456789","region":"eu"}`, "events.eu.pagerduty.com"},
		{"opsgenie", `{"key":"0a1b2c3d-0000-4000-8000-00000000abcd","region":"eu"}`, "api.eu.opsgenie.com"},
	}
	for _, tt := range tests {
		if code, saved := put(tt.kind, tt.body); code != http.StatusOK || saved.Target != tt.target || saved.SigningSecret != nil {
			t.Errorf("%s %s = %d %+v, want target %s", tt.kind, tt.body, code, saved, tt.target)
		}
	}
	for _, bad := range []struct{ kind, body string }{
		{"slack", `{"url":"https://hooks.example.com/services/T000/B000/X"}`},
		{"slack", `{"url":"http://hooks.slack.com/services/T000/B000/X"}`},
		{"slack", `{"url":"https://hooks.slack.com/triggers/T000/1/x"}`},
		{"teams", `{"url":"https://evil.example.com/?x.logic.azure.com"}`},
		{"discord", `{"url":"https://discord.com/channels/1/2"}`},
		{"discord", `{"url":"https://discord.com/api/webhooks/1/x","key":"k"}`},
		{"pagerduty", `{"key":"short"}`},
		{"pagerduty", `{"key":"S3CR3T0123456789abcdef0123456789","region":"au"}`},
		{"pagerduty", `{"url":"https://events.pagerduty.com"}`},
		{"opsgenie", `{"key":"not-a-key"}`},
	} {
		if code, _ := put(bad.kind, bad.body); code != http.StatusBadRequest {
			t.Errorf("%s %s = %d, want 400", bad.kind, bad.body, code)
		}
	}
	if code, _ := f.do(a, http.MethodPut, base+"/alert-rules/slow-answer", `{"on":true,"to":["slack","pagerduty","teams","discord","opsgenie"]}`, nil); code != http.StatusOK {
		t.Errorf("a rule to every new kind = %d, want 200", code)
	}
	if code, _ := put("email", `{"recipients":["oncall@example.com"]}`); code != http.StatusOK {
		t.Fatalf("email = %d", code)
	}
	code, body := f.do(a, http.MethodGet, base+"/alert-setup", "", nil)
	if code != http.StatusOK || bytes.Contains(body, []byte("S3CR3T")) || !bytes.Contains(body, []byte("oncall@example.com")) {
		t.Fatalf("getAlertSetup as Admin = %d %s: want no URL or key, and the recipients", code, body)
	}

	viewer := *a
	viewer.Gate = func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			p := store.Principal{UserID: "00000000-0000-4000-8000-000000000002", Roles: map[string]string{"default": "viewer"}}
			next.ServeHTTP(w, r.WithContext(store.WithPrincipal(r.Context(), p)))
		})
	}
	var setup alertSetup
	if code, body := f.do(&viewer, http.MethodGet, base+"/alert-setup", "", &setup); code != http.StatusOK || bytes.Contains(body, []byte("oncall@example.com")) {
		t.Fatalf("getAlertSetup as Viewer = %d %s: want no recipients", code, body)
	}
	for _, c := range setup.Channels {
		if c.Kind == "email" && (c.Recipients != nil || c.Target != "1 recipient") {
			t.Errorf("Viewer's email channel = %+v, want the count only", c)
		}
	}

	cloud := *a
	cloud.AlertHTTPSOnly = true
	if code, _ := f.do(&cloud, http.MethodPut, base+"/alert-channels/webhook", `{"url":"http://hooks.example.com/in"}`, nil); code != http.StatusBadRequest {
		t.Errorf("plain http webhook on Cloud = %d, want 400", code)
	}
	if code, _ := f.do(&cloud, http.MethodPut, base+"/alert-channels/webhook", `{"url":"https://hooks.example.com/in"}`, nil); code != http.StatusOK {
		t.Errorf("https webhook on Cloud = %d, want 200", code)
	}
}

// The reads the Alerts page makes answer in the contract's shapes, every rule present and unknown
// incidents not_found.
func TestAlertReads(t *testing.T) {
	f := newFixture(t)
	a := f.api(nil)
	a.Alerts = f.pg
	base := "/api/v1/org/default/project/" + f.project
	q := "?from=" + time.Now().Add(-24*time.Hour).UTC().Format(time.RFC3339) + "&to=" + time.Now().Add(time.Minute).UTC().Format(time.RFC3339)
	var events alertEvents
	if code, _ := f.do(a, http.MethodGet, base+"/alert-events"+q, "", &events); code != http.StatusOK || len(events.Rules) != 7 || !events.Complete {
		t.Fatalf("listAlertEvents = %d %+v", code, events)
	}
	if code, body := f.do(a, http.MethodGet, base+"/alert-incidents"+q, "", nil); code != http.StatusOK || !bytes.Contains(body, []byte(`"items":[]`)) {
		t.Fatalf("listAlertIncidents = %d %s", code, body)
	}
	if code, _ := f.do(a, http.MethodGet, base+"/alert-incidents?from=x&to=y", "", nil); code != http.StatusBadRequest {
		t.Errorf("bad range = %d, want 400", code)
	}
	if code, _ := f.do(a, http.MethodPost, base+"/alert-incidents/00000000-0000-4000-8000-000000000009/resolve", "", nil); code != http.StatusNotFound {
		t.Errorf("unknown incident = %d, want 404", code)
	}
}
