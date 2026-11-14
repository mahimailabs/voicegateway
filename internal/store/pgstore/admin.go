package pgstore

import (
	"context"
	"fmt"

	"github.com/jackc/pgx/v5/pgtype"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// SetInstanceAdmin sets or clears an account's instance admin flag (decision 0041), or
// store.ErrNotFound when no account has the email.
func (s *Store) SetInstanceAdmin(ctx context.Context, email string, on bool) error {
	n, err := s.q.SetInstanceAdmin(ctx, SetInstanceAdminParams{InstanceAdmin: on, Email: foldEmail(email)})
	if err != nil {
		return fmt.Errorf("set instance admin: %w", err)
	}
	if n == 0 {
		return store.ErrNotFound
	}
	return nil
}

// AdminOrganisations returns the newest limit organisations with their counts, and how many
// organisations there are in all.
func (s *Store) AdminOrganisations(ctx context.Context, limit int) ([]store.AdminOrganisation, int64, error) {
	rows, err := s.q.AdminOrganisations(ctx, int32(limit)) //nolint:gosec // limit is a small constant
	if err != nil {
		return nil, 0, fmt.Errorf("admin organisations: %w", err)
	}
	out := make([]store.AdminOrganisation, 0, len(rows))
	var total int64
	for _, r := range rows {
		total = r.Total
		out = append(out, store.AdminOrganisation{
			ID: r.ID, Name: r.Name, CreatedAt: r.CreatedAt.Time.UTC(),
			Members: r.Members, Admins: r.Admins, Projects: r.Projects,
		})
	}
	return out, total, nil
}

// AdminUsers returns the newest limit accounts with their organisation roles, and how many
// accounts there are in all.
func (s *Store) AdminUsers(ctx context.Context, limit int) ([]store.AdminUser, int64, error) {
	rows, err := s.q.AdminUsers(ctx, int32(limit)) //nolint:gosec // limit is a small constant
	if err != nil {
		return nil, 0, fmt.Errorf("admin users: %w", err)
	}
	out := make([]store.AdminUser, 0, len(rows))
	ids := make([]pgtype.UUID, 0, len(rows))
	at := make(map[pgtype.UUID]int, len(rows))
	var total int64
	for i, r := range rows {
		total = r.Total
		ids = append(ids, r.ID)
		at[r.ID] = i
		out = append(out, store.AdminUser{
			ID: r.ID.String(), Email: r.Email, CreatedAt: r.CreatedAt.Time.UTC(),
			InstanceAdmin: r.InstanceAdmin, Roles: map[string]string{},
		})
	}
	if len(ids) == 0 {
		return out, total, nil
	}
	ms, err := s.q.AdminMemberships(ctx, ids)
	if err != nil {
		return nil, 0, fmt.Errorf("admin memberships: %w", err)
	}
	for _, m := range ms {
		if i, ok := at[m.UserID]; ok {
			out[i].Roles[m.OrganisationID] = m.Role
		}
	}
	return out, total, nil
}
