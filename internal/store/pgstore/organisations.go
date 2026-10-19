package pgstore

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgtype"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// PutOrganisation creates organisation id named name, or renames it, and reports whether this
// call created it. Parallel calls for one id create one row, and only one of them reports created.
func (s *Store) PutOrganisation(ctx context.Context, id, name string) (store.Organisation, bool, error) {
	r, err := s.q.PutOrganisation(ctx, PutOrganisationParams{ID: id, Name: name})
	if err != nil {
		return store.Organisation{}, false, fmt.Errorf("put organisation: %w", err)
	}
	return store.Organisation{ID: r.ID, Name: r.Name, CreatedAt: r.CreatedAt.Time.UTC()}, r.Created, nil
}

// Organisation returns an organisation with its counts, or store.ErrNotFound.
func (s *Store) Organisation(ctx context.Context, id string) (store.Organisation, error) {
	r, err := s.q.OrganisationSummary(ctx, id)
	if errors.Is(err, pgx.ErrNoRows) {
		return store.Organisation{}, store.ErrNotFound
	}
	if err != nil {
		return store.Organisation{}, fmt.Errorf("organisation: %w", err)
	}
	return store.Organisation{
		ID: r.ID, Name: r.Name, CreatedAt: r.CreatedAt.Time.UTC(),
		Members: r.Members, Admins: r.Admins, PendingInvitations: r.PendingInvitations,
	}, nil
}

// OrganisationUsage returns an organisation's call time in [from, to), or store.ErrNotFound when
// the organisation does not exist.
func (s *Store) OrganisationUsage(ctx context.Context, id string, from, to time.Time) (store.Usage, error) {
	if _, err := s.OrganisationName(ctx, id); err != nil {
		return store.Usage{}, err
	}
	r, err := s.q.OrganisationUsage(ctx, OrganisationUsageParams{
		OrganisationID: id,
		FromAt:         pgtype.Timestamptz{Time: from, Valid: true},
		ToAt:           pgtype.Timestamptz{Time: to, Valid: true},
	})
	if err != nil {
		return store.Usage{}, fmt.Errorf("organisation usage: %w", err)
	}
	return store.Usage{CallSeconds: r.CallSeconds, Calls: r.Calls, InferredCalls: r.InferredCalls, UnendedCalls: r.UnendedCalls}, nil
}
