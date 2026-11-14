package api

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/mahimailabs/voicegateway/internal/store"
)

type noAdminReads struct{}

func (noAdminReads) AdminOrganisations(context.Context, int) ([]store.AdminOrganisation, int64, error) {
	return nil, 0, nil
}

func (noAdminReads) AdminUsers(context.Context, int) ([]store.AdminUser, int64, error) {
	return nil, 0, nil
}

func (noAdminReads) OrganisationUsage(context.Context, string, time.Time, time.Time) (store.Usage, error) {
	return store.Usage{}, nil
}

// The admin reads open to a signed-in instance admin and to no other credential (decision 0041):
// an organisation Admin, a project key, the instance token and an embed token all get 403.
func TestAdminRoutesNeedTheInstanceAdminFlag(t *testing.T) {
	const user = "00000000-0000-4000-8000-000000000001"
	tests := []struct {
		name string
		p    store.Principal
		want int
	}{
		{"instance admin", store.Principal{UserID: user, InstanceAdmin: true}, http.StatusOK},
		{"organisation admin", store.Principal{UserID: user, Roles: map[string]string{"default": "admin"}}, http.StatusForbidden},
		{"project key", store.Principal{Key: &store.KeyScope{OrganisationID: "default", ProjectID: "p"}, InstanceAdmin: true}, http.StatusForbidden},
		{"instance token", store.Principal{Instance: true}, http.StatusForbidden},
		{"embed token", store.Principal{Embed: &store.EmbedScope{OrganisationID: "default", ProjectID: "p"}}, http.StatusForbidden},
		{"flag without a user", store.Principal{InstanceAdmin: true}, http.StatusForbidden},
	}
	paths := []string{
		"/api/v1/admin/organisations",
		"/api/v1/admin/users",
		"/api/v1/admin/organisations/default/usage?from=2026-11-01T00:00:00Z&to=2026-11-02T00:00:00Z",
	}
	for _, tt := range tests {
		gate := func(next http.Handler) http.Handler {
			return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				next.ServeHTTP(w, r.WithContext(store.WithPrincipal(r.Context(), tt.p)))
			})
		}
		routes := (&API{Admin: noAdminReads{}, Now: time.Now, Gate: gate}).Routes()
		for _, path := range paths {
			rec := httptest.NewRecorder()
			routes.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, path, nil))
			if rec.Code != tt.want {
				t.Errorf("%s, %s = %d, want %d", tt.name, path, rec.Code, tt.want)
			}
		}
	}
}

// An instance admin holds no organisation role: the flag alone reads no project.
func TestInstanceAdminFlagOpensNoProject(t *testing.T) {
	gate := func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			p := store.Principal{UserID: "00000000-0000-4000-8000-000000000001", InstanceAdmin: true, Roles: map[string]string{}}
			next.ServeHTTP(w, r.WithContext(store.WithPrincipal(r.Context(), p)))
		})
	}
	routes := (&API{Now: time.Now, Gate: gate}).Routes()
	rec := httptest.NewRecorder()
	routes.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/api/v1/org/default/project/p/calls", nil))
	if rec.Code != http.StatusForbidden {
		t.Errorf("an instance admin with no membership read a project's calls: %d, want 403", rec.Code)
	}
}
