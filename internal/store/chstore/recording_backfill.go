package chstore

import (
	"context"
	"fmt"

	"github.com/mahimailabs/voicegateway/internal/event"
	"github.com/mahimailabs/voicegateway/internal/store"
)

// RecordingEvents reads a bounded, deterministic page including events without call links.
func (s *Store) RecordingEvents(ctx context.Context, b store.RecordingBackfill) ([]store.Event, error) {
	rows, err := s.conn.Query(ctx, `SELECT dedupe_key,event_id,received_at,payload,toString(call_id)
 FROM events FINAL WHERE organisation_id=? AND project_id=? AND kind=?
 AND JSONExtractString(payload,'event')='egress_ended'
 AND (toUnixTimestamp64Nano(received_at),dedupe_key) > (?,?)
 ORDER BY received_at,dedupe_key LIMIT 100`, b.OrganisationID, b.ProjectID, string(event.KindLiveKitWebhook), b.CursorNS, b.CursorKey)
	if err != nil {
		return nil, fmt.Errorf("read recording backfill: %w", err)
	}
	defer func() { _ = rows.Close() }()
	out := make([]store.Event, 0, 100)
	for rows.Next() {
		row := store.Event{OrganisationID: b.OrganisationID, ProjectID: b.ProjectID, Kind: string(event.KindLiveKitWebhook)}
		var call *string
		if err := rows.Scan(&row.DedupeKey, &row.EventID, &row.ReceivedAt, &row.Payload, &call); err != nil {
			return nil, err
		}
		if call != nil {
			row.CallID = *call
		}
		out = append(out, row)
	}
	return out, rows.Err()
}
