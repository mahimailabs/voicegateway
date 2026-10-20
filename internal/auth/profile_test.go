package auth_test

import (
	"context"
	"encoding/json"
	"net/http"
	"strings"
	"testing"

	"github.com/mahimailabs/voicegateway/internal/store"
)

func TestAccountProfileSessionPermissionsAndValidation(t *testing.T) {
	f := newFixture(t)
	f.addProjectCall("default")
	for _, role := range []string{"viewer", "member", "admin"} {
		f.addUser(role+"@example.com", "default", role)
	}
	s := f.service()
	web := http.NewCrossOriginProtection().Handler(s.Routes())
	cookies := map[string]*http.Cookie{}
	for _, role := range []string{"viewer", "member", "admin"} {
		cookie := f.signedIn(s, role+"@example.com")
		cookies[role] = cookie
		res := f.do(web, http.MethodPut, "/auth/profile", `{"name":"`+role+`","revision":0}`, request{tls: true, cookies: []*http.Cookie{cookie}})
		var profile store.AccountProfile
		err := json.NewDecoder(res.Body).Decode(&profile)
		_ = res.Body.Close()
		if err != nil || res.StatusCode != http.StatusOK || profile.Name == nil || *profile.Name != role || profile.Revision != 1 {
			t.Fatalf("profile as %s: %d %+v %v", role, res.StatusCode, profile, err)
		}
	}
	for _, tc := range []struct {
		name, body string
		status     int
		headers    map[string]string
		cookie     *http.Cookie
	}{
		{name: "unauthenticated", body: `{"name":"x","revision":1}`, status: 401},
		{name: "project key", body: `{"name":"x","revision":1}`, status: 401, headers: map[string]string{"Authorization": "Bearer " + f.projectKey("default", "p")}},
		{name: "cross origin", body: `{"name":"x","revision":1}`, status: 403, cookie: cookies["viewer"], headers: map[string]string{"Sec-Fetch-Site": "cross-site", "Origin": "https://other.example"}},
		{name: "stale", body: `{"name":"x","revision":0}`, status: 409, cookie: cookies["viewer"]},
		{name: "other user", body: `{"name":"x","revision":1,"userId":"` + f.userID("admin@example.com") + `"}`, status: 400, cookie: cookies["viewer"]},
		{name: "missing name", body: `{"revision":1}`, status: 400, cookie: cookies["viewer"]},
		{name: "control", body: `{"name":"x\n","revision":1}`, status: 400, cookie: cookies["viewer"]},
		{name: "oversize", body: `{"name":"` + strings.Repeat("x", 4096) + `","revision":1}`, status: 400, cookie: cookies["viewer"]},
		{name: "utf8", body: "{\"name\":\"" + string([]byte{0xff}) + "\",\"revision\":1}", status: 400, cookie: cookies["viewer"]},
	} {
		t.Run(tc.name, func(t *testing.T) {
			rq := request{tls: true, headers: tc.headers}
			if tc.cookie != nil {
				rq.cookies = []*http.Cookie{tc.cookie}
			}
			res := f.do(web, http.MethodPut, "/auth/profile", tc.body, rq)
			_ = res.Body.Close()
			if res.StatusCode != tc.status {
				t.Fatalf("status %d, want %d", res.StatusCode, tc.status)
			}
		})
	}
	for _, role := range []string{"viewer", "member", "admin"} {
		p, err := f.pg.AccountProfile(context.Background(), f.userID(role+"@example.com"))
		if err != nil || p.Name == nil || *p.Name != role || p.Revision != 1 {
			t.Fatalf("rejected writes changed %s: %+v %v", role, p, err)
		}
	}
	res := f.do(web, http.MethodPost, "/auth/sign-out", "", request{tls: true, cookies: []*http.Cookie{cookies["viewer"]}})
	_ = res.Body.Close()
	res = f.do(web, http.MethodPut, "/auth/profile", `{"name":"x","revision":1}`, request{tls: true, cookies: []*http.Cookie{cookies["viewer"]}})
	_ = res.Body.Close()
	if res.StatusCode != 401 {
		t.Fatalf("expired session write: %d", res.StatusCode)
	}
	// A new session reads the persisted account, not data tied to the old cookie.
	fresh := f.signedIn(s, "viewer@example.com")
	for _, path := range []string{"/auth/profile", "/auth/session"} {
		res = f.do(web, http.MethodGet, path, "", request{tls: true, cookies: []*http.Cookie{fresh}})
		var body json.RawMessage
		err := json.NewDecoder(res.Body).Decode(&body)
		_ = res.Body.Close()
		if err != nil || res.StatusCode != http.StatusOK {
			t.Fatalf("fresh session %s: %d %v", path, res.StatusCode, err)
		}
		if path == "/auth/session" {
			var session struct {
				Profile json.RawMessage `json:"profile"`
			}
			if err := json.Unmarshal(body, &session); err != nil {
				t.Fatal(err)
			}
			body = session.Profile
		}
		var profile store.AccountProfile
		if err := json.Unmarshal(body, &profile); err != nil {
			t.Fatal(err)
		}
		if profile.Name == nil || *profile.Name != "viewer" || profile.Revision != 1 {
			t.Fatalf("fresh session lost profile: %+v", profile)
		}
	}
}
