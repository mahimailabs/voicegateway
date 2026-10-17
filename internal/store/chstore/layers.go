package chstore

import (
	"context"
	"encoding/json"
	"fmt"
	"time"

	"github.com/mahimailabs/voicegateway/internal/event"
	"github.com/mahimailabs/voicegateway/internal/store"
)

// StoreRead writes one layer read: its samples first, then the read itself, so a read recorded as
// good always has its samples behind it.
func (s *Store) StoreRead(ctx context.Context, read store.LayerRead, rooms []store.RoomSample, participants []store.ParticipantSample) error {
	if len(rooms) > 0 {
		batch, err := s.conn.PrepareBatch(ctx, `INSERT INTO room_samples (organisation_id, project_id, read_at, room_sid, room)`)
		if err != nil {
			return fmt.Errorf("prepare room samples: %w", err)
		}
		for _, r := range rooms {
			body, err := json.Marshal(r.Room)
			if err != nil {
				_ = batch.Abort()
				return fmt.Errorf("encode room %s: %w", r.Room.SID, err)
			}
			if err := batch.Append(r.OrganisationID, r.ProjectID, r.ReadAt, r.Room.SID, string(body)); err != nil {
				_ = batch.Abort()
				return fmt.Errorf("room sample %s: %w", r.Room.SID, err)
			}
		}
		if err := batch.Send(); err != nil {
			return fmt.Errorf("insert %d room samples: %w", len(rooms), err)
		}
	}
	if len(participants) > 0 {
		batch, err := s.conn.PrepareBatch(ctx,
			`INSERT INTO participant_samples (organisation_id, project_id, read_at, room_sid, participant_sid, state, sip_call_status, participant)`)
		if err != nil {
			return fmt.Errorf("prepare participant samples: %w", err)
		}
		for _, p := range participants {
			body, err := json.Marshal(p.Participant)
			if err != nil {
				_ = batch.Abort()
				return fmt.Errorf("encode participant %s: %w", p.Participant.SID, err)
			}
			if err := batch.Append(p.OrganisationID, p.ProjectID, p.ReadAt, p.RoomSID, p.Participant.SID, p.State, p.SIPCallStatus, string(body)); err != nil {
				_ = batch.Abort()
				return fmt.Errorf("participant sample %s: %w", p.Participant.SID, err)
			}
		}
		if err := batch.Send(); err != nil {
			return fmt.Errorf("insert %d participant samples: %w", len(participants), err)
		}
	}
	// A batch, not Exec: Exec binds a time at whole seconds, and read_at keeps milliseconds.
	batch, err := s.conn.PrepareBatch(ctx, `INSERT INTO layer_reads (organisation_id, project_id, layer, read_at, ok, error_class, every_ms)`)
	if err != nil {
		return fmt.Errorf("prepare layer read: %w", err)
	}
	every := uint32(read.Every.Milliseconds()) //nolint:gosec // intervals are seconds, far below 2^32 ms
	if err := batch.Append(read.OrganisationID, read.ProjectID, read.Layer, read.ReadAt, read.OK, read.ErrorClass, every); err != nil {
		_ = batch.Abort()
		return fmt.Errorf("layer read: %w", err)
	}
	if err := batch.Send(); err != nil {
		return fmt.Errorf("insert layer read: %w", err)
	}
	return nil
}

// LayerReads returns a layer's reads since a time, oldest first.
func (s *Store) LayerReads(ctx context.Context, organisationID, projectID, layer string, since time.Time) ([]store.LayerRead, error) {
	rows, err := s.conn.Query(ctx, `
		SELECT read_at, ok, error_class, every_ms FROM layer_reads
		WHERE organisation_id = ? AND project_id = ? AND layer = ? AND read_at >= ?
		ORDER BY read_at`, organisationID, projectID, layer, since)
	if err != nil {
		return nil, fmt.Errorf("layer reads: %w", err)
	}
	defer func() { _ = rows.Close() }()
	var out []store.LayerRead
	for rows.Next() {
		r := store.LayerRead{OrganisationID: organisationID, ProjectID: projectID, Layer: layer}
		var every uint32
		if err := rows.Scan(&r.ReadAt, &r.OK, &r.ErrorClass, &every); err != nil {
			return nil, fmt.Errorf("layer reads: %w", err)
		}
		r.ReadAt = r.ReadAt.UTC()
		r.Every = time.Duration(every) * time.Millisecond
		out = append(out, r)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("layer reads: %w", err)
	}
	return out, nil
}

// LayerStatus sums up every read of a layer, however old.
func (s *Store) LayerStatus(ctx context.Context, organisationID, projectID, layer string) (store.LayerStatus, error) {
	var (
		n              uint64
		good           uint64
		last, lastGood time.Time
		lastClass      string
	)
	err := s.conn.QueryRow(ctx, `
		SELECT count(), countIf(ok), max(read_at), maxIf(read_at, ok), argMax(error_class, read_at)
		FROM layer_reads WHERE organisation_id = ? AND project_id = ? AND layer = ?`,
		organisationID, projectID, layer).Scan(&n, &good, &last, &lastGood, &lastClass)
	if err != nil {
		return store.LayerStatus{}, fmt.Errorf("layer status: %w", err)
	}
	var st store.LayerStatus
	if n > 0 {
		t := last.UTC()
		st.LastReadAt = &t
		st.ErrorClass = lastClass
	}
	if good > 0 {
		t := lastGood.UTC()
		st.LastGoodReadAt = &t
	}
	return st, nil
}

// RoomsSince returns every room seen since a time, as last seen, most recently seen first, at most
// limit.
func (s *Store) RoomsSince(ctx context.Context, organisationID, projectID string, since time.Time, limit int) ([]store.RoomSeen, error) {
	rows, err := s.conn.Query(ctx, `
		SELECT argMax(room, read_at), min(read_at) AS first, max(read_at) AS last
		FROM room_samples
		WHERE organisation_id = ? AND project_id = ? AND read_at >= ?
		GROUP BY room_sid
		ORDER BY last DESC, room_sid
		LIMIT ?`, organisationID, projectID, since, limit)
	if err != nil {
		return nil, fmt.Errorf("rooms: %w", err)
	}
	defer func() { _ = rows.Close() }()
	var out []store.RoomSeen
	for rows.Next() {
		var body string
		var r store.RoomSeen
		if err := rows.Scan(&body, &r.FirstSeen, &r.LastSeen); err != nil {
			return nil, fmt.Errorf("rooms: %w", err)
		}
		if err := json.Unmarshal([]byte(body), &r.Room); err != nil {
			return nil, fmt.Errorf("decode room: %w", err)
		}
		r.FirstSeen, r.LastSeen = r.FirstSeen.UTC(), r.LastSeen.UTC()
		out = append(out, r)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("rooms: %w", err)
	}
	return out, nil
}

// Participants returns every participant seen in the rooms, as last seen.
func (s *Store) Participants(ctx context.Context, organisationID, projectID string, roomSIDs []string) ([]store.ParticipantSeen, error) {
	if len(roomSIDs) == 0 {
		return nil, nil
	}
	rows, err := s.conn.Query(ctx, `
		SELECT room_sid, argMax(participant, read_at), min(read_at), max(read_at)
		FROM participant_samples
		WHERE organisation_id = ? AND project_id = ? AND room_sid IN ?
		GROUP BY room_sid, participant_sid
		ORDER BY room_sid, min(read_at), participant_sid`, organisationID, projectID, roomSIDs)
	if err != nil {
		return nil, fmt.Errorf("participants: %w", err)
	}
	defer func() { _ = rows.Close() }()
	var out []store.ParticipantSeen
	for rows.Next() {
		var body string
		var p store.ParticipantSeen
		if err := rows.Scan(&p.RoomSID, &body, &p.FirstSeen, &p.LastSeen); err != nil {
			return nil, fmt.Errorf("participants: %w", err)
		}
		if err := json.Unmarshal([]byte(body), &p.Participant); err != nil {
			return nil, fmt.Errorf("decode participant: %w", err)
		}
		p.FirstSeen, p.LastSeen = p.FirstSeen.UTC(), p.LastSeen.UTC()
		out = append(out, p)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("participants: %w", err)
	}
	return out, nil
}

// ParticipantChanges returns, for every participant in the rooms, each read where its agent state
// or SIP call status differed from the read before, plus its first read, oldest first. A state that
// comes back (listening, thinking, listening) is a change each time.
func (s *Store) ParticipantChanges(ctx context.Context, organisationID, projectID string, roomSIDs []string) ([]store.ParticipantChange, error) {
	if len(roomSIDs) == 0 {
		return nil, nil
	}
	rows, err := s.conn.Query(ctx, `
		SELECT room_sid, participant_sid, state, sip_call_status, read_at AS at FROM (
			SELECT room_sid, participant_sid, state, sip_call_status, read_at,
				row_number() OVER w AS n,
				lagInFrame(state) OVER w AS prev_state,
				lagInFrame(sip_call_status) OVER w AS prev_sip
			FROM participant_samples
			WHERE organisation_id = ? AND project_id = ? AND room_sid IN ?
			WINDOW w AS (PARTITION BY room_sid, participant_sid ORDER BY read_at ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
		)
		WHERE n = 1 OR state != prev_state OR sip_call_status != prev_sip
		ORDER BY at`, organisationID, projectID, roomSIDs)
	if err != nil {
		return nil, fmt.Errorf("participant changes: %w", err)
	}
	defer func() { _ = rows.Close() }()
	var out []store.ParticipantChange
	for rows.Next() {
		var c store.ParticipantChange
		if err := rows.Scan(&c.RoomSID, &c.ParticipantSID, &c.State, &c.SIPCallStatus, &c.At); err != nil {
			return nil, fmt.Errorf("participant changes: %w", err)
		}
		c.At = c.At.UTC()
		out = append(out, c)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("participant changes: %w", err)
	}
	return out, nil
}

// RoomsWithWebhooks returns the SIDs of rooms that a stored room, participant or track webhook named
// since a time, so the worker reads them in full before their next full pass is due.
func (s *Store) RoomsWithWebhooks(ctx context.Context, organisationID, projectID string, since time.Time) ([]string, error) {
	rows, err := s.conn.Query(ctx, `
		SELECT DISTINCT JSONExtractString(payload, 'room', 'sid') AS room_sid FROM events
		WHERE organisation_id = ? AND project_id = ? AND kind = ? AND received_at > ?
			AND match(JSONExtractString(payload, 'event'), '^(room|participant|track)_')
			AND room_sid != ''`, organisationID, projectID, string(event.KindLiveKitWebhook), since)
	if err != nil {
		return nil, fmt.Errorf("rooms with webhooks: %w", err)
	}
	defer func() { _ = rows.Close() }()
	var out []string
	for rows.Next() {
		var sid string
		if err := rows.Scan(&sid); err != nil {
			return nil, fmt.Errorf("rooms with webhooks: %w", err)
		}
		out = append(out, sid)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("rooms with webhooks: %w", err)
	}
	return out, nil
}
