package app

import (
	"context"
	"errors"
	"testing"

	"github.com/mahimailabs/voicegateway/internal/config"
	"github.com/mahimailabs/voicegateway/internal/store"
	"github.com/mahimailabs/voicegateway/internal/store/pgstore"
)

// The instance admin flag (decision 0041) is set from the command line, loads with the principal on
// the next request, and the admin lists read every organisation and account without a credential.
func TestInstanceAdminFlagAndLists(t *testing.T) {
	ctx := context.Background()
	pool, url := newPostgres(t)
	cfg := config.Config{PostgresURL: url}
	pw := func() (string, error) { return "a long enough password", nil }
	if _, err := pool.Exec(ctx, `INSERT INTO organisations (id, name) VALUES ('org-b', 'B')`); err != nil {
		t.Fatal(err)
	}
	if _, err := AddUser(ctx, cfg, "Kai@Example.com", "default", "admin", pw); err != nil {
		t.Fatal(err)
	}
	if _, err := AddUser(ctx, cfg, "john.doe@example.com", "org-b", "viewer", pw); err != nil {
		t.Fatal(err)
	}
	pg := pgstore.NewStore(pool, nil)
	kai, err := pg.UserByEmail(ctx, "kai@example.com")
	if err != nil {
		t.Fatal(err)
	}
	if p, err := pg.Principal(ctx, kai.ID); err != nil || p.InstanceAdmin {
		t.Fatalf("a new organisation admin: InstanceAdmin %v, err %v; want false", p.InstanceAdmin, err)
	}
	if err := SetInstanceAdmin(ctx, cfg, " Kai@Example.com ", true); err != nil {
		t.Fatal(err)
	}
	if p, err := pg.Principal(ctx, kai.ID); err != nil || !p.InstanceAdmin {
		t.Fatalf("after the command: InstanceAdmin %v, err %v; want true", p.InstanceAdmin, err)
	}
	if err := SetInstanceAdmin(ctx, cfg, "nobody@example.com", true); !errors.Is(err, store.ErrNotFound) {
		t.Errorf("an unknown email: %v, want ErrNotFound", err)
	}

	users, total, err := pg.AdminUsers(ctx, 10)
	if err != nil || total != 2 || len(users) != 2 {
		t.Fatalf("AdminUsers = %d of %d, %v; want 2 of 2", len(users), total, err)
	}
	seen := map[string]store.AdminUser{}
	for _, u := range users {
		seen[u.Email] = u
	}
	if u := seen["kai@example.com"]; !u.InstanceAdmin || u.Roles["default"] != "admin" {
		t.Errorf("kai = %+v", u)
	}
	if u := seen["john.doe@example.com"]; u.InstanceAdmin || u.Roles["org-b"] != "viewer" || len(u.Roles) != 1 {
		t.Errorf("john = %+v", u)
	}
	if one, total, err := pg.AdminUsers(ctx, 1); err != nil || len(one) != 1 || total != 2 {
		t.Errorf("a limit of 1 = %d of %d, %v; want 1 of 2", len(one), total, err)
	}

	orgs, total, err := pg.AdminOrganisations(ctx, 10)
	if err != nil || total != int64(len(orgs)) || len(orgs) < 2 {
		t.Fatalf("AdminOrganisations = %d of %d, %v", len(orgs), total, err)
	}
	for _, o := range orgs {
		switch o.ID {
		case "default":
			if o.Members != 1 || o.Admins != 1 {
				t.Errorf("default = %+v", o)
			}
		case "org-b":
			if o.Members != 1 || o.Admins != 0 || o.Projects != 0 {
				t.Errorf("org-b = %+v", o)
			}
		}
	}

	if err := SetInstanceAdmin(ctx, cfg, "kai@example.com", false); err != nil {
		t.Fatal(err)
	}
	if p, err := pg.Principal(ctx, kai.ID); err != nil || p.InstanceAdmin {
		t.Errorf("after --off: InstanceAdmin %v, err %v; want false", p.InstanceAdmin, err)
	}
}
