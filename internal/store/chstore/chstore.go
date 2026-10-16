package chstore

import (
	"context"
	"fmt"
	"io/fs"

	"github.com/ClickHouse/clickhouse-go/v2"
	"github.com/ClickHouse/clickhouse-go/v2/lib/driver"
	"github.com/pressly/goose/v3"

	"github.com/mahimailabs/voicegateway/internal/store"
	"github.com/mahimailabs/voicegateway/migrations"
)

// Store reads and writes ClickHouse.
type Store struct {
	conn driver.Conn
}

// Open connects to ClickHouse at a clickhouse:// URL. The connection is lazy: Open succeeds while
// ClickHouse is still starting.
func Open(url string) (*Store, error) {
	opts, err := clickhouse.ParseDSN(url)
	if err != nil {
		return nil, fmt.Errorf("parse ClickHouse URL: %w", err)
	}
	conn, err := clickhouse.Open(opts)
	if err != nil {
		return nil, fmt.Errorf("open ClickHouse: %w", err)
	}
	return &Store{conn: conn}, nil
}

// Close releases the connections.
func (s *Store) Close() error { return s.conn.Close() }

// Ping reports whether ClickHouse answers.
func (s *Store) Ping(ctx context.Context) error { return s.conn.Ping(ctx) }

// Migrate applies every pending migration in migrations/clickhouse and returns how many ran.
func Migrate(ctx context.Context, url string) (int, error) {
	opts, err := clickhouse.ParseDSN(url)
	if err != nil {
		return 0, fmt.Errorf("parse ClickHouse URL: %w", err)
	}
	db := clickhouse.OpenDB(opts)
	defer func() { _ = db.Close() }()
	fsys, err := fs.Sub(migrations.ClickHouse, "clickhouse")
	if err != nil {
		return 0, fmt.Errorf("migrations: %w", err)
	}
	provider, err := goose.NewProvider(goose.DialectClickHouse, db, fsys)
	if err != nil {
		return 0, fmt.Errorf("migrations: %w", err)
	}
	results, err := provider.Up(ctx)
	if err != nil {
		return len(results), fmt.Errorf("migrate clickhouse: %w", err)
	}
	return len(results), nil
}

// Existing returns which of keys are already stored.
func (s *Store) Existing(ctx context.Context, keys []store.EventKey) (map[store.EventKey]bool, error) {
	type scope struct{ org, project, kind string }
	byScope := map[scope][]string{}
	for _, k := range keys {
		sc := scope{k.OrganisationID, k.ProjectID, k.Kind}
		byScope[sc] = append(byScope[sc], k.DedupeKey)
	}
	found := map[store.EventKey]bool{}
	for sc, ids := range byScope {
		rows, err := s.conn.Query(ctx,
			`SELECT dedupe_key FROM events WHERE organisation_id = ? AND project_id = ? AND kind = ? AND dedupe_key IN ?`,
			sc.org, sc.project, sc.kind, ids)
		if err != nil {
			return nil, fmt.Errorf("find stored events: %w", err)
		}
		for rows.Next() {
			var id string
			if err := rows.Scan(&id); err != nil {
				_ = rows.Close()
				return nil, fmt.Errorf("find stored events: %w", err)
			}
			found[store.EventKey{OrganisationID: sc.org, ProjectID: sc.project, Kind: sc.kind, DedupeKey: id}] = true
		}
		err = rows.Err()
		_ = rows.Close()
		if err != nil {
			return nil, fmt.Errorf("find stored events: %w", err)
		}
	}
	return found, nil
}

// Insert writes events in one batch. Either every row lands or the call fails.
func (s *Store) Insert(ctx context.Context, events []store.Event) error {
	if len(events) == 0 {
		return nil
	}
	batch, err := s.conn.PrepareBatch(ctx,
		`INSERT INTO events (organisation_id, project_id, kind, dedupe_key, event_id, stream_id, received_at, payload)`)
	if err != nil {
		return fmt.Errorf("prepare insert: %w", err)
	}
	for _, e := range events {
		if err := batch.Append(e.OrganisationID, e.ProjectID, e.Kind, e.DedupeKey, e.EventID, e.StreamID, e.ReceivedAt, string(e.Payload)); err != nil {
			_ = batch.Abort()
			return fmt.Errorf("insert event %s: %w", e.StreamID, err)
		}
	}
	if err := batch.Send(); err != nil {
		return fmt.Errorf("insert %d events: %w", len(events), err)
	}
	return nil
}
