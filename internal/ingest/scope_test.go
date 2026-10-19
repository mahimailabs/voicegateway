package ingest

import (
	"context"
	"errors"
	"testing"

	"github.com/mahimailabs/voicegateway/internal/store"
)

type scopeStore struct{}

func (scopeStore) Project(_ context.Context, org, id string) (store.Project, error) {
	if id != "voice" || (org != "attacker" && org != "customer") {
		return store.Project{}, store.ErrNotFound
	}
	return store.Project{OrganisationID: org, ID: id, LiveKitAPIKey: "same-key"}, nil
}

func (scopeStore) LiveKitSecret(_ context.Context, org, id string) (string, error) {
	if id != "voice" {
		return "", store.ErrNotFound
	}
	return org + "-secret", nil
}

func TestScopedProjectKeysDoNotSelectAnotherTenant(t *testing.T) {
	ctx := t.Context()
	for _, org := range []string{"attacker", "customer"} {
		scoped := ScopedProjects{Store: scopeStore{}, OrganisationID: org, ProjectID: "voice"}
		keys := projectKeys{ctx: ctx, projects: scoped}
		if got := keys.GetSecret("same-key"); got != org+"-secret" {
			t.Fatalf("%s resolved %q", org, got)
		}
		if keys.project.OrganisationID != org {
			t.Fatal("wrong destination")
		}
		if got := keys.GetSecret("other-key"); got != "" {
			t.Fatal("accepted key outside scope")
		}
		if _, err := scoped.LiveKitSecret(ctx, "other", "voice"); !errors.Is(err, store.ErrNotFound) {
			t.Fatalf("cross-scope secret: %v", err)
		}
	}
	for _, org := range []string{"", "missing"} {
		keys := projectKeys{ctx: ctx, projects: ScopedProjects{Store: scopeStore{}, OrganisationID: org, ProjectID: "voice"}}
		if got := keys.GetSecret("same-key"); got != "" {
			t.Fatal("unknown scope resolved a key")
		}
	}
}
