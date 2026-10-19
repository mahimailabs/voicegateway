package instance

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/mahimailabs/voicegateway/internal/api"
	"github.com/mahimailabs/voicegateway/internal/store"
)

type entitlementMemory struct {
	u        store.EntitlementUsage
	writes   int
	writeErr error
}

func (s *entitlementMemory) PutEntitlement(_ context.Context, _ string, e store.Entitlement) error {
	if s.writeErr != nil {
		return s.writeErr
	}
	s.u.Entitlement = e
	s.writes++
	return nil
}

func (s *entitlementMemory) EntitlementUsage(context.Context, string) (store.EntitlementUsage, error) {
	return s.u, nil
}

func TestEntitlementAccessAndValidation(t *testing.T) {
	st := &entitlementMemory{}
	h := (&Handlers{Entitlements: st, Log: slog.New(slog.NewTextHandler(io.Discard, nil))}).API()
	instancePath := "/api/v1/instance/organisations/acme/entitlement"
	memberPath := "/api/v1/org/acme/entitlement"
	body := []byte(`{"from":"2026-10-01T00:00:00Z","to":"2026-11-01T00:00:00Z","limitMinutes":2000,"active":true,"revision":1}`)
	for _, tt := range []struct {
		name, method, path string
		p                  store.Principal
		status             int
	}{
		{"operator writes", "PUT", instancePath, store.Principal{Instance: true}, 200},
		{"admin cannot grant", "PUT", instancePath, store.Principal{UserID: "u", Roles: map[string]string{"acme": "admin"}}, 403},
		{"viewer reads", "GET", memberPath, store.Principal{UserID: "u", Roles: map[string]string{"acme": "viewer"}}, 200},
		{"other organisation", "GET", memberPath, store.Principal{UserID: "u", Roles: map[string]string{"other": "admin"}}, 403},
		{"project key cannot grant", "PUT", instancePath, store.Principal{Key: &store.KeyScope{OrganisationID: "acme", ProjectID: "p"}}, 403},
	} {
		t.Run(tt.name, func(t *testing.T) {
			router := (&api.API{Instance: h, Gate: func(next http.Handler) http.Handler {
				return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					next.ServeHTTP(w, r.WithContext(store.WithPrincipal(r.Context(), tt.p)))
				})
			}}).Routes()
			w := httptest.NewRecorder()
			router.ServeHTTP(w, httptest.NewRequest(tt.method, tt.path, bytes.NewReader(body)))
			if w.Code != tt.status {
				t.Fatalf("status %d: %s", w.Code, w.Body.String())
			}
			if tt.status == 200 {
				var u store.EntitlementUsage
				if err := json.Unmarshal(w.Body.Bytes(), &u); err != nil {
					t.Fatal(err)
				}
			}
		})
	}
	if st.writes != 1 {
		t.Fatalf("unauthorised writes: %d", st.writes)
	}
	router := (&api.API{Instance: h, Gate: func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			next.ServeHTTP(w, r.WithContext(store.WithPrincipal(r.Context(), store.Principal{Instance: true})))
		})
	}}).Routes()
	for _, bad := range []string{`{}`, string(body) + `{}`, `{"from":"2026-10-01T00:00:00Z","to":"2026-11-01T00:00:00Z","limitMinutes":2000,"revision":2}`} {
		w := httptest.NewRecorder()
		router.ServeHTTP(w, httptest.NewRequest("PUT", instancePath, bytes.NewBufferString(bad)))
		if w.Code != 400 {
			t.Fatalf("bad request %d", w.Code)
		}
	}
	if st.writes != 1 {
		t.Fatal("invalid request changed entitlement")
	}
	st.writeErr = store.ErrEntitlementRevision
	conflict := httptest.NewRecorder()
	router.ServeHTTP(conflict, httptest.NewRequest("PUT", instancePath, bytes.NewReader(body)))
	var failure struct{ Code string }
	if err := json.Unmarshal(conflict.Body.Bytes(), &failure); err != nil {
		t.Fatal(err)
	}
	if conflict.Code != 409 || failure.Code != "revision_conflict" {
		t.Fatalf("conflict %d: %s", conflict.Code, conflict.Body.String())
	}
	st.writeErr = nil
	// Reads stay available even when measured usage exhausted the allowance.
	st.u.CallSeconds = 120000
	st.u.From = time.Now().Add(-time.Hour)
	st.u.To = time.Now().Add(time.Hour)
	w := httptest.NewRecorder()
	router.ServeHTTP(w, httptest.NewRequest("GET", instancePath, nil))
	if w.Code != 200 {
		t.Fatal(w.Code)
	}
}
