package chstore

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"

	"github.com/mahimailabs/voicegateway/internal/store"
)

// StoreSIPRead writes one SIP read: what it saw first, when it succeeded, then the read itself.
func (s *Store) StoreSIPRead(ctx context.Context, read store.LayerRead, sip *store.SIP) error {
	if read.OK && sip != nil {
		body, err := json.Marshal(sip)
		if err != nil {
			return fmt.Errorf("encode sip config: %w", err)
		}
		batch, err := s.conn.PrepareBatch(pollInsert(ctx), `INSERT INTO sip_reads (organisation_id, project_id, read_at, config)`)
		if err != nil {
			return fmt.Errorf("prepare sip read: %w", err)
		}
		if err := batch.Append(read.OrganisationID, read.ProjectID, read.ReadAt, string(body)); err != nil {
			_ = batch.Abort()
			return fmt.Errorf("sip read: %w", err)
		}
		if err := batch.Send(); err != nil {
			return fmt.Errorf("insert sip read: %w", err)
		}
	}
	return s.storeLayerRead(ctx, read)
}

// LatestSIP returns the latest good SIP read of a project, or store.ErrNotFound when there is none.
func (s *Store) LatestSIP(ctx context.Context, organisationID, projectID string) (store.SIPRead, error) {
	var out store.SIPRead
	var body string
	err := s.conn.QueryRow(ctx, `
		SELECT read_at, config FROM sip_reads
		WHERE organisation_id = ? AND project_id = ?
		ORDER BY read_at DESC LIMIT 1`, organisationID, projectID).Scan(&out.ReadAt, &body)
	if errors.Is(err, sql.ErrNoRows) {
		return out, store.ErrNotFound
	}
	if err != nil {
		return out, fmt.Errorf("latest sip read: %w", err)
	}
	if err := json.Unmarshal([]byte(body), &out.SIP); err != nil {
		return out, fmt.Errorf("decode sip read: %w", err)
	}
	out.ReadAt = out.ReadAt.UTC()
	return out, nil
}
