package chstore

import (
	"context"
	"encoding/json"
	"fmt"
	"time"

	"github.com/mahimailabs/voicegateway/internal/event"
	"github.com/mahimailabs/voicegateway/internal/store"
)

// sampled names one table of sampled egress or ingress projections. The names are constants of
// this file, never input.
type sampled struct {
	table, id, body string
}

var (
	egressTable  = sampled{table: "egress_samples", id: "egress_id", body: "egress"}
	ingressTable = sampled{table: "ingress_samples", id: "ingress_id", body: "ingress"}
)

// sampleRow is one item of a read: its id, status and encoded projection.
type sampleRow struct {
	organisationID, projectID string
	readAt                    time.Time
	id, status                string
	updatedAt                 *time.Time
	body                      []byte
}

// StoreEgressRead writes one egress read: what it saw first, then the read itself, so a read
// recorded as good always has its samples behind it.
func (s *Store) StoreEgressRead(ctx context.Context, read store.LayerRead, egress []store.EgressSample) error {
	rows := make([]sampleRow, 0, len(egress))
	for _, e := range egress {
		body, err := json.Marshal(e.Egress)
		if err != nil {
			return fmt.Errorf("encode egress %s: %w", e.Egress.ID, err)
		}
		rows = append(rows, sampleRow{e.OrganisationID, e.ProjectID, e.ReadAt, e.Egress.ID, e.Egress.Status, e.UpdatedAt, body})
	}
	if err := s.storeSamples(ctx, egressTable, rows); err != nil {
		return err
	}
	return s.storeLayerRead(ctx, read)
}

// StoreIngressRead writes one ingress read: what it saw first, then the read itself.
func (s *Store) StoreIngressRead(ctx context.Context, read store.LayerRead, ingress []store.IngressSample) error {
	rows := make([]sampleRow, 0, len(ingress))
	for _, in := range ingress {
		body, err := json.Marshal(in.Ingress)
		if err != nil {
			return fmt.Errorf("encode ingress %s: %w", in.Ingress.ID, err)
		}
		rows = append(rows, sampleRow{in.OrganisationID, in.ProjectID, in.ReadAt, in.Ingress.ID, in.Ingress.Status, in.UpdatedAt, body})
	}
	if err := s.storeSamples(ctx, ingressTable, rows); err != nil {
		return err
	}
	return s.storeLayerRead(ctx, read)
}

// storeSamples inserts rows as one batch. A batch, not Exec: Exec binds a time at whole seconds, and
// read_at keeps milliseconds.
func (s *Store) storeSamples(ctx context.Context, t sampled, rows []sampleRow) error {
	if len(rows) == 0 {
		return nil
	}
	batch, err := s.conn.PrepareBatch(ctx, `INSERT INTO `+t.table+` (organisation_id, project_id, read_at, `+t.id+`, status, updated_at, `+t.body+`)`)
	if err != nil {
		return fmt.Errorf("prepare %s: %w", t.table, err)
	}
	for _, r := range rows {
		if err := batch.Append(r.organisationID, r.projectID, r.readAt, r.id, r.status, r.updatedAt, string(r.body)); err != nil {
			_ = batch.Abort()
			return fmt.Errorf("%s %s: %w", t.table, r.id, err)
		}
	}
	if err := batch.Send(); err != nil {
		return fmt.Errorf("insert %d %s: %w", len(rows), t.table, err)
	}
	return nil
}

// EgressSince returns every egress seen since a time, as last seen, most recently seen first, at
// most limit.
func (s *Store) EgressSince(ctx context.Context, organisationID, projectID string, since time.Time, limit int) ([]store.EgressSeen, error) {
	var out []store.EgressSeen
	err := s.since(ctx, egressTable, organisationID, projectID, since, limit, func(body string, first, last time.Time) error {
		e := store.EgressSeen{FirstSeen: first, LastSeen: last}
		if err := json.Unmarshal([]byte(body), &e.Egress); err != nil {
			return fmt.Errorf("decode egress: %w", err)
		}
		out = append(out, e)
		return nil
	})
	return out, err
}

// IngressSince returns every ingress endpoint seen since a time, as last seen, most recently seen
// first, at most limit.
func (s *Store) IngressSince(ctx context.Context, organisationID, projectID string, since time.Time, limit int) ([]store.IngressSeen, error) {
	var out []store.IngressSeen
	err := s.since(ctx, ingressTable, organisationID, projectID, since, limit, func(body string, first, last time.Time) error {
		in := store.IngressSeen{FirstSeen: first, LastSeen: last}
		if err := json.Unmarshal([]byte(body), &in.Ingress); err != nil {
			return fmt.Errorf("decode ingress: %w", err)
		}
		out = append(out, in)
		return nil
	})
	return out, err
}

func (s *Store) since(ctx context.Context, t sampled, organisationID, projectID string, since time.Time, limit int, each func(body string, first, last time.Time) error) error {
	rows, err := s.conn.Query(ctx, `
		SELECT argMax(`+t.body+`, read_at), min(read_at) AS first, max(read_at) AS last
		FROM `+t.table+`
		WHERE organisation_id = ? AND project_id = ? AND read_at >= ?
		GROUP BY `+t.id+`
		ORDER BY last DESC, `+t.id+`
		LIMIT ?`, organisationID, projectID, since, limit)
	if err != nil {
		return fmt.Errorf("%s: %w", t.table, err)
	}
	defer func() { _ = rows.Close() }()
	for rows.Next() {
		var body string
		var first, last time.Time
		if err := rows.Scan(&body, &first, &last); err != nil {
			return fmt.Errorf("%s: %w", t.table, err)
		}
		if err := each(body, first.UTC(), last.UTC()); err != nil {
			return err
		}
	}
	if err := rows.Err(); err != nil {
		return fmt.Errorf("%s: %w", t.table, err)
	}
	return nil
}

// EgressChanges returns, for every egress named, each read where its status differed from the read
// before, plus its first read, oldest first.
func (s *Store) EgressChanges(ctx context.Context, organisationID, projectID string, ids []string) ([]store.StatusChange, error) {
	return s.changes(ctx, egressTable, organisationID, projectID, ids)
}

// IngressChanges returns, for every ingress endpoint named, each read where its status differed
// from the read before, plus its first read, oldest first.
func (s *Store) IngressChanges(ctx context.Context, organisationID, projectID string, ids []string) ([]store.StatusChange, error) {
	return s.changes(ctx, ingressTable, organisationID, projectID, ids)
}

func (s *Store) changes(ctx context.Context, t sampled, organisationID, projectID string, ids []string) ([]store.StatusChange, error) {
	if len(ids) == 0 {
		return nil, nil
	}
	rows, err := s.conn.Query(ctx, `
		SELECT id, status, read_at AS at, updated_at FROM (
			SELECT `+t.id+` AS id, status, read_at, updated_at,
				row_number() OVER w AS n,
				lagInFrame(status) OVER w AS prev
			FROM `+t.table+`
			WHERE organisation_id = ? AND project_id = ? AND `+t.id+` IN ?
			WINDOW w AS (PARTITION BY `+t.id+` ORDER BY read_at ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
		)
		WHERE n = 1 OR status != prev
		ORDER BY at`, organisationID, projectID, ids)
	if err != nil {
		return nil, fmt.Errorf("%s changes: %w", t.table, err)
	}
	defer func() { _ = rows.Close() }()
	var out []store.StatusChange
	for rows.Next() {
		var c store.StatusChange
		if err := rows.Scan(&c.ID, &c.Status, &c.At, &c.UpdatedAt); err != nil {
			return nil, fmt.Errorf("%s changes: %w", t.table, err)
		}
		c.At = c.At.UTC()
		if c.UpdatedAt != nil {
			u := c.UpdatedAt.UTC()
			c.UpdatedAt = &u
		}
		out = append(out, c)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("%s changes: %w", t.table, err)
	}
	return out, nil
}

// webhookRead says where one kind of stored webhook carries its item's id, status and update time,
// as ClickHouse JSON paths written in this file. protojson leaves a zero enum out, so a missing
// status is the enum's zero value.
type webhookRead struct {
	what      string
	events    []string
	id        string
	status    string
	updatedAt string
	// state is the object that holds the status; a webhook without it reported no status.
	state      string
	zeroStatus string
}

var (
	egressWebhooks = webhookRead{
		what:       "egress webhooks",
		events:     []string{"egress_started", "egress_updated", "egress_ended"},
		id:         `'egressInfo', 'egressId'`,
		status:     `'egressInfo', 'status'`,
		updatedAt:  `'egressInfo', 'updatedAt'`,
		state:      `'egressInfo'`,
		zeroStatus: "EGRESS_STARTING",
	}
	ingressWebhooks = webhookRead{
		what:       "ingress webhooks",
		events:     []string{"ingress_started", "ingress_ended"},
		id:         `'ingressInfo', 'ingressId'`,
		status:     `'ingressInfo', 'state', 'status'`,
		updatedAt:  `'ingressInfo', 'state', 'updatedAt'`,
		state:      `'ingressInfo', 'state'`,
		zeroStatus: "ENDPOINT_INACTIVE",
	}
)

// EgressWebhooks returns the status each stored egress_started, egress_updated or egress_ended
// webhook received since a time reported for the egress named, oldest first. Only the id, status and
// times are read.
func (s *Store) EgressWebhooks(ctx context.Context, organisationID, projectID string, ids []string, since time.Time) ([]store.WebhookStatus, error) {
	return s.webhooks(ctx, egressWebhooks, organisationID, projectID, ids, since)
}

// IngressWebhooks returns the status each stored ingress_started or ingress_ended webhook reported
// since a time for the endpoints named, oldest first. Only the id, status and times are read.
func (s *Store) IngressWebhooks(ctx context.Context, organisationID, projectID string, ids []string, since time.Time) ([]store.WebhookStatus, error) {
	return s.webhooks(ctx, ingressWebhooks, organisationID, projectID, ids, since)
}

func (s *Store) webhooks(ctx context.Context, w webhookRead, organisationID, projectID string, ids []string, since time.Time) ([]store.WebhookStatus, error) {
	if len(ids) == 0 {
		return nil, nil
	}
	// protojson writes int64 as a JSON string; the raw value is read either way. The update time is
	// LiveKit's nanoseconds, the event's createdAt its seconds. A webhook without the status's object
	// reported no status, which stays unknown: it is left out rather than read as the zero status.
	rows, err := s.conn.Query(ctx, `
		SELECT id, status, updated_ns, created_s, received_at FROM (
			SELECT JSONExtractString(payload, `+w.id+`) AS id,
				JSONExtractString(payload, `+w.status+`) AS status,
				toInt64OrZero(trim(BOTH '"' FROM JSONExtractRaw(payload, `+w.updatedAt+`))) AS updated_ns,
				toInt64OrZero(trim(BOTH '"' FROM JSONExtractRaw(payload, 'createdAt'))) AS created_s,
				received_at
			FROM events
			WHERE organisation_id = ? AND project_id = ? AND kind = ? AND received_at >= ?
				AND JSONExtractString(payload, 'event') IN ?
				AND JSONHas(payload, `+w.state+`)
		)
		WHERE id IN ?
		ORDER BY received_at`,
		organisationID, projectID, string(event.KindLiveKitWebhook), since, w.events, ids)
	if err != nil {
		return nil, fmt.Errorf("%s: %w", w.what, err)
	}
	defer func() { _ = rows.Close() }()
	var out []store.WebhookStatus
	for rows.Next() {
		var h store.WebhookStatus
		var updatedNs, createdS int64
		if err := rows.Scan(&h.ID, &h.Status, &updatedNs, &createdS, &h.ReceivedAt); err != nil {
			return nil, fmt.Errorf("%s: %w", w.what, err)
		}
		if h.Status == "" {
			h.Status = w.zeroStatus
		}
		switch {
		case updatedNs > 0:
			t := time.Unix(0, updatedNs).UTC()
			h.At = &t
		case createdS > 0:
			t := time.Unix(createdS, 0).UTC()
			h.At = &t
		}
		h.ReceivedAt = h.ReceivedAt.UTC()
		out = append(out, h)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("%s: %w", w.what, err)
	}
	return out, nil
}
