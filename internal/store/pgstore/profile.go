package pgstore

import (
	"context"
	"errors"
	"fmt"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgtype"

	"github.com/mahimailabs/voicegateway/internal/store"
)

func (s *Store) AccountProfile(ctx context.Context, userID string) (store.AccountProfile, error) {
	id, err := uuid(userID)
	if err != nil {
		return store.AccountProfile{}, store.ErrNotFound
	}
	row, err := s.q.ReadAccountProfile(ctx, id)
	if errors.Is(err, pgx.ErrNoRows) {
		return store.AccountProfile{}, store.ErrNotFound
	}
	if err != nil {
		return store.AccountProfile{}, fmt.Errorf("read account profile: %w", err)
	}
	return accountProfile(row.DisplayName, row.ProfileRevision), nil
}

func (s *Store) UpdateAccountProfile(ctx context.Context, userID string, revision int64, value string) (store.AccountProfile, error) {
	name, err := store.ProfileName(value)
	if err != nil {
		return store.AccountProfile{}, err
	}
	if revision < 0 || revision >= 9007199254740991 {
		return store.AccountProfile{}, store.ErrInvalidProfileRevision
	}
	id, err := uuid(userID)
	if err != nil {
		return store.AccountProfile{}, store.ErrNotFound
	}
	text := pgtype.Text{}
	if name != nil {
		text = pgtype.Text{String: *name, Valid: true}
	}
	row, err := s.q.UpdateAccountProfile(ctx, UpdateAccountProfileParams{UserID: id, DisplayName: text, ExpectedRevision: revision})
	if errors.Is(err, pgx.ErrNoRows) {
		if _, readErr := s.AccountProfile(ctx, userID); readErr != nil {
			return store.AccountProfile{}, readErr
		}
		return store.AccountProfile{}, store.ErrProfileConflict
	}
	if err != nil {
		return store.AccountProfile{}, fmt.Errorf("update account profile: %w", err)
	}
	return accountProfile(row.DisplayName, row.ProfileRevision), nil
}

func accountProfile(name pgtype.Text, revision int64) store.AccountProfile {
	profile := store.AccountProfile{Revision: revision}
	if name.Valid {
		profile.Name = &name.String
	}
	return profile
}
