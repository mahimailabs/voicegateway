package api

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"testing"
	"time"

	"github.com/mahimailabs/voicegateway/internal/store"
)

func TestSettingsHTTPPersistenceAndValidation(t *testing.T) {
	f := newFixture(t)
	a := f.api(nil)
	a.Settings = f.pg
	path := "/api/v1/org/default/project/" + f.project + "/settings"
	var initial projectSettingsRead
	if code := f.get(a, path, &initial); code != http.StatusOK || initial.Revision != 0 {
		t.Fatalf("initial read: %d %+v", code, initial)
	}
	send := func(body string, want int) projectSettingsRead {
		t.Helper()
		w := httptest.NewRecorder()
		r := httptest.NewRequest(http.MethodPut, path, strings.NewReader(body))
		r.Header.Set("Content-Type", "application/json")
		a.Routes().ServeHTTP(w, r)
		if w.Code != want {
			t.Fatalf("write %s: %d %s", body, w.Code, w.Body.String())
		}
		if w.Header().Get("Cache-Control") != "no-store" {
			t.Fatal("settings response must not be cached")
		}
		var out projectSettingsRead
		if want == http.StatusOK {
			if err := json.Unmarshal(w.Body.Bytes(), &out); err != nil {
				t.Fatal(err)
			}
		}
		return out
	}
	valid := `{"thresholds":{"answerMs":2400.5,"joinMs":3500,"ttsStartMs":1200},"revision":0}`
	saved := send(valid, http.StatusOK)
	if saved.Revision != 1 || saved.Thresholds.AnswerMs != 2400.5 {
		t.Fatalf("saved %+v", saved)
	}
	send(valid, http.StatusConflict)
	for _, body := range []string{
		`{}`, `null`, `[]`,
		`{"thresholds":null,"revision":1}`,
		`{"thresholds":{"answerMs":2400,"joinMs":3500},"revision":1}`,
		`{"thresholds":{"answerMs":99,"joinMs":3500,"ttsStartMs":1200},"revision":1}`,
		`{"thresholds":{"answerMs":60001,"joinMs":3500,"ttsStartMs":1200},"revision":1}`,
		`{"thresholds":{"answerMs":2400,"joinMs":3500,"ttsStartMs":1200,"extra":1},"revision":1}`,
		strings.ReplaceAll(valid, `"revision":0`, `"revision":-1`),
		strings.ReplaceAll(valid, `"revision":0`, `"revision":1.5`),
		valid + `{}`, strings.Repeat(" ", 4097) + valid,
	} {
		send(body, http.StatusBadRequest)
	}
	var after projectSettingsRead
	if code := f.get(a, path, &after); code != http.StatusOK || after.Thresholds != saved.Thresholds || after.Revision != saved.Revision {
		t.Fatalf("invalid writes changed settings: %d %+v", code, after)
	}
	other := f.addProject(t.Name() + "-other")
	got, err := f.pg.ProjectSettings(context.Background(), "default", other)
	if err != nil || got.Revision != 0 || got.Thresholds != initial.Thresholds {
		t.Fatalf("other scope %+v %v", got, err)
	}
}

func TestSettingsHTTPRoleGate(t *testing.T) {
	f := newFixture(t)
	for _, role := range []string{"viewer", "member", "admin", ""} {
		t.Run(role, func(t *testing.T) {
			a := f.api(nil)
			a.Settings = f.pg
			a.Gate = func(next http.Handler) http.Handler {
				return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					p := store.Principal{UserID: "00000000-0000-4000-8000-000000000001", Roles: map[string]string{"default": role}}
					next.ServeHTTP(w, r.WithContext(store.WithPrincipal(r.Context(), p)))
				})
			}
			for _, method := range []string{http.MethodGet, http.MethodPut} {
				w := httptest.NewRecorder()
				r := httptest.NewRequest(method, "/api/v1/org/default/project/"+f.project+"/settings", strings.NewReader(`{}`))
				a.Routes().ServeHTTP(w, r)
				want := http.StatusOK
				if method == http.MethodPut {
					want = http.StatusBadRequest
				}
				if role == "" || role == "viewer" && method == http.MethodPut {
					want = http.StatusForbidden
				}
				if w.Code != want {
					t.Fatalf("%s as %q: got %d want %d: %s", method, role, w.Code, want, w.Body.String())
				}
			}
		})
	}
}

func TestSettingsProjectKeyAndCrossOrigin(t *testing.T) {
	f := newFixture(t)
	a := f.api(nil)
	a.Settings = f.pg
	own := "/api/v1/org/default/project/" + f.project + "/settings"
	other := "/api/v1/org/default/project/" + f.addProject(t.Name()+"-other") + "/settings"
	body := `{"thresholds":{"answerMs":2400,"joinMs":3500,"ttsStartMs":1200},"revision":0}`
	a.Gate = func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			p := store.Principal{Key: &store.KeyScope{KeyID: "test", OrganisationID: "default", ProjectID: f.project}}
			next.ServeHTTP(w, r.WithContext(store.WithPrincipal(r.Context(), p)))
		})
	}
	for _, tc := range []struct {
		method, path string
		want         int
	}{
		{http.MethodGet, own, http.StatusOK},
		{http.MethodGet, other, http.StatusForbidden},
		{http.MethodPut, own, http.StatusForbidden},
		{http.MethodPut, other, http.StatusForbidden},
	} {
		w := httptest.NewRecorder()
		a.Routes().ServeHTTP(w, httptest.NewRequest(tc.method, tc.path, strings.NewReader(body)))
		if w.Code != tc.want {
			t.Fatalf("key %s %s: %d, want %d", tc.method, tc.path, w.Code, tc.want)
		}
	}
	a.Gate = passThrough
	// The same standard-library protection wraps the application mux in internal/app.
	handler := http.NewCrossOriginProtection().Handler(a.Routes())
	for _, tc := range []struct {
		site string
		want int
	}{
		{"cross-site", http.StatusForbidden},
		{"same-origin", http.StatusOK},
	} {
		w := httptest.NewRecorder()
		r := httptest.NewRequest(http.MethodPut, own, strings.NewReader(body))
		r.Header.Set("Sec-Fetch-Site", tc.site)
		handler.ServeHTTP(w, r)
		if w.Code != tc.want {
			t.Fatalf("%s write: %d %s", tc.site, w.Code, w.Body.String())
		}
	}
	got, err := f.pg.ProjectSettings(context.Background(), "default", f.project)
	if err != nil || got.Revision != 1 {
		t.Fatalf("only same-origin write should persist: %+v %v", got, err)
	}
}

func TestSettingsChangeCallTotalsAndInvalidateSnapshot(t *testing.T) {
	f := newFixture(t)
	f.connectedAt(statsDay0.Add(-time.Hour))
	f.statsCall(statsDay0.Add(time.Hour), "web", "agent", 1500.25, 2400.75)
	before, code := f.list(nil)
	if code != http.StatusOK || before.Totals.Answers.OverBudget == nil || *before.Totals.Answers.OverBudget != 2 {
		t.Fatalf("before settings change: %d %+v", code, before.Totals.Answers)
	}
	if _, err := f.pg.UpdateProjectSettings(context.Background(), "default", f.project, 0, store.Thresholds{AnswerMs: 1500.5, JoinMs: 2000, TTSStartMs: 1000}); err != nil {
		t.Fatal(err)
	}
	after, code := f.list(nil)
	if code != http.StatusOK || after.Totals.Answers.BudgetMs != 1500.5 || after.Totals.Answers.OverBudget == nil || *after.Totals.Answers.OverBudget != 1 {
		t.Fatalf("after settings change: %d %+v", code, after.Totals.Answers)
	}
	var stats callStats
	if code := f.get(f.statsAPI(), statsPath(f.project, statsDay0, statsDay0.Add(24*time.Hour)), &stats); code != http.StatusOK || stats.Answers.BudgetMs != 1500.5 || stats.Answers.OverBudget == nil || *stats.Answers.OverBudget != 1 {
		t.Fatalf("stats must match list thresholds: %d %+v", code, stats.Answers)
	}
	if _, code := f.list(url.Values{"snapshot": {before.Page.Snapshot}}); code != http.StatusBadRequest {
		t.Fatalf("old settings snapshot returned status %d", code)
	}
	// A request parameter cannot spoof the server-owned revision binding.
	if _, code := f.list(url.Values{"snapshot": {before.Page.Snapshot}, "settingsRevision": {"0"}}); code != http.StatusBadRequest {
		t.Fatalf("spoofed revision returned status %d", code)
	}
}
